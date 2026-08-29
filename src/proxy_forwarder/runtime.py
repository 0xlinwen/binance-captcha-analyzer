from __future__ import annotations

import base64
import shutil
import socket
import ssl
import subprocess
import time
import urllib.error
import urllib.request
import requests
from typing import Any, Callable, Mapping

from .logging import ProxyLogger, emit_log
from .proxy_utils import build_proxy_url, describe_proxy, parse_proxy_text

DISABLED_GOST_BINARY = "__disabled_gost__"


def _is_gost_requested(gost_settings: Mapping[str, Any] | None) -> bool:
    """判断调用方是否明确要求启动 gost 本地转发。"""
    if not isinstance(gost_settings, Mapping) or not gost_settings:
        return False
    gost_bin = str(gost_settings.get("binary") or "").strip()
    return bool(gost_bin) and gost_bin != DISABLED_GOST_BINARY


def stop_proxy_runtime(proxy_info: Mapping[str, Any] | None) -> None:
    if not proxy_info:
        return
    process = proxy_info.get("_gost_process")
    if not process:
        return
    try:
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=2)
    except Exception:
        try:
            if process.poll() is None:
                process.kill()
        except Exception:
            pass


def _pick_free_local_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        sock.listen(1)
        return int(sock.getsockname()[1])


def _wait_local_listener(host: str, port: int, timeout_seconds: int) -> bool:
    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        try:
            with socket.create_connection((host, port), timeout=1):
                return True
        except Exception:
            time.sleep(0.2)
    return False


def _make_connect_request(
    sock: socket.socket,
    host: str,
    port: int,
    auth: str | None = None,
) -> bool:
    request = f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n"
    if auth:
        encoded = base64.b64encode(auth.encode()).decode()
        request += f"Proxy-Authorization: Basic {encoded}\r\n"
    request += "\r\n"
    sock.sendall(request.encode())

    response = b""
    while b"\r\n\r\n" not in response:
        chunk = sock.recv(4096)
        if not chunk:
            break
        response += chunk

    first_line = response.split(b"\r\n")[0].decode(errors="replace")
    return "200" in first_line


def _read_http_response_headers(stream) -> bytes:
    response = b""
    while b"\r\n\r\n" not in response:
        chunk = stream.recv(4096)
        if not chunk:
            break
        response += chunk
        if len(response) >= 65536:
            break
    return response


def _parse_http_status_code(response: bytes) -> int | None:
    first_line = response.split(b"\r\n", 1)[0].decode(errors="replace").strip()
    parts = first_line.split()
    if len(parts) < 2:
        return None
    try:
        return int(parts[1])
    except ValueError:
        return None


def check_proxy_via_chain(
    bootstrap_proxy: Mapping[str, Any],
    dynamic_proxy: Mapping[str, Any],
    target_host: str = "api.ip.sb",
    target_port: int = 443,
    timeout: int = 15,
    *,
    logger: ProxyLogger | None = None,
) -> tuple[bool, str | None]:
    b_ip = bootstrap_proxy.get("ip") or bootstrap_proxy.get("host") or ""
    b_port = int(bootstrap_proxy.get("port") or 0)
    b_user = bootstrap_proxy.get("user") or bootstrap_proxy.get("username") or ""
    b_pass = bootstrap_proxy.get("password") or ""
    b_auth = f"{b_user}:{b_pass}" if b_user and b_pass else None

    d_ip = dynamic_proxy.get("ip") or dynamic_proxy.get("host") or ""
    d_port = int(dynamic_proxy.get("port") or 0)
    d_user = dynamic_proxy.get("user") or dynamic_proxy.get("username") or ""
    d_pass = dynamic_proxy.get("password") or ""
    d_auth = f"{d_user}:{d_pass}" if d_user and d_pass else None

    try:
        sock = socket.create_connection((b_ip, b_port), timeout=timeout)
        sock.settimeout(timeout)

        if not _make_connect_request(sock, d_ip, d_port, b_auth):
            emit_log(logger, "proxy-chain", "bootstrap-connect-failed")
            sock.close()
            return False, None

        if not _make_connect_request(sock, target_host, target_port, d_auth):
            emit_log(logger, "proxy-chain", "target-connect-failed", target=target_host)
            sock.close()
            return False, None

        context = ssl.create_default_context()
        context.check_hostname = False
        context.verify_mode = ssl.CERT_NONE
        tls_sock = context.wrap_socket(sock, server_hostname=target_host)
        http_request = f"GET /ip HTTP/1.1\r\nHost: {target_host}\r\nConnection: close\r\n\r\n"
        tls_sock.sendall(http_request.encode())
        response = tls_sock.recv(4096).decode(errors="replace")
        tls_sock.close()

        ok = "200" in response.split("\r\n")[0]
        lines = [line.strip() for line in response.splitlines()]
        body_lines = [
            line for line in lines
            if line and not line.startswith("HTTP/") and ":" not in line
        ]
        exit_ip = body_lines[-1] if body_lines else None
        if ok:
            emit_log(logger, "proxy-chain", exit_ip=exit_ip or "?")
        return ok, exit_ip
    except Exception as exc:
        emit_log(logger, "proxy-chain", "error", error=exc)
        return False, None


def probe_url_via_chain(
    bootstrap_proxy: Mapping[str, Any],
    dynamic_proxy: Mapping[str, Any],
    target_url: str,
    timeout: int = 15,
) -> tuple[bool, int | None, float, str]:
    parsed = urllib.parse.urlsplit(target_url)
    target_host = (parsed.hostname or "").strip()
    if not target_host:
        return False, None, 0.0, "invalid_target_host"

    target_port = int(parsed.port or (443 if parsed.scheme.lower() == "https" else 80))
    target_path = parsed.path or "/"
    if parsed.query:
        target_path = f"{target_path}?{parsed.query}"

    b_ip = bootstrap_proxy.get("ip") or bootstrap_proxy.get("host") or ""
    b_port = int(bootstrap_proxy.get("port") or 0)
    b_user = bootstrap_proxy.get("user") or bootstrap_proxy.get("username") or ""
    b_pass = bootstrap_proxy.get("password") or ""
    b_auth = f"{b_user}:{b_pass}" if b_user and b_pass else None

    d_ip = dynamic_proxy.get("ip") or dynamic_proxy.get("host") or ""
    d_port = int(dynamic_proxy.get("port") or 0)
    d_user = dynamic_proxy.get("user") or dynamic_proxy.get("username") or ""
    d_pass = dynamic_proxy.get("password") or ""
    d_auth = f"{d_user}:{d_pass}" if d_user and d_pass else None

    probe_start = time.monotonic()
    sock: socket.socket | None = None
    stream = None
    try:
        sock = socket.create_connection((b_ip, b_port), timeout=timeout)
        sock.settimeout(timeout)

        if not _make_connect_request(sock, d_ip, d_port, b_auth):
            return False, None, (time.monotonic() - probe_start) * 1000.0, "bootstrap_connect_failed"

        if not _make_connect_request(sock, target_host, target_port, d_auth):
            return False, None, (time.monotonic() - probe_start) * 1000.0, "target_connect_failed"

        if parsed.scheme.lower() == "https":
            context = ssl.create_default_context()
            context.check_hostname = False
            context.verify_mode = ssl.CERT_NONE
            stream = context.wrap_socket(sock, server_hostname=target_host)
        else:
            stream = sock

        request = (
            f"GET {target_path} HTTP/1.1\r\n"
            f"Host: {target_host}\r\n"
            "Connection: close\r\n"
            "Cache-Control: no-cache\r\n"
            "Pragma: no-cache\r\n"
            "User-Agent: Mozilla/5.0\r\n"
            "\r\n"
        )
        stream.sendall(request.encode())
        response = _read_http_response_headers(stream)
        elapsed_ms = (time.monotonic() - probe_start) * 1000.0
        status_code = _parse_http_status_code(response)
        if status_code is None:
            return False, None, elapsed_ms, "empty_http_status"
        if 500 <= status_code < 600:
            return False, status_code, elapsed_ms, f"http_{status_code}"
        return True, status_code, elapsed_ms, "ok"
    except Exception as exc:
        return False, None, (time.monotonic() - probe_start) * 1000.0, str(exc)
    finally:
        try:
            if stream and stream is not sock:
                stream.close()
        except Exception:
            pass
        try:
            if sock:
                sock.close()
        except Exception:
            pass


def fetch_proxy_via_bootstrap(
    proxy_api: str,
    bootstrap_proxy: Mapping[str, Any],
    timeout: int,
    *,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    proxy_url = build_proxy_url(bootstrap_proxy)
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.proxies = {"http": proxy_url, "https": proxy_url}
            response = session.get(proxy_api, timeout=timeout)
            response.raise_for_status()
            text = response.text.strip()
    except Exception as exc:
        emit_log(logger, "bootstrap-proxy", "fetch-failed", error=exc)
        return None
    return parse_proxy_text(text)


def fetch_public_ip_via_proxy(
    proxy_info: Mapping[str, Any],
    timeout: int,
    *,
    logger: ProxyLogger | None = None,
) -> str | None:
    proxy_url = build_proxy_url(proxy_info)
    try:
        with requests.Session() as session:
            session.trust_env = False
            session.proxies = {"http": proxy_url, "https": proxy_url}
            response = session.get("https://api.ip.sb/ip", headers={"User-Agent": "Mozilla/5.0"}, timeout=timeout)
            response.raise_for_status()
            ip = response.text.strip()
    except Exception as exc:
        emit_log(logger, "proxy-ip", "fetch-failed", proxy=describe_proxy(proxy_info), error=exc)
        return None
    return ip or None


def _probe_url_via_proxy(
    proxy_info: Mapping[str, Any],
    target_url: str,
    *,
    timeout_seconds: float,
) -> tuple[bool, int | None, float, str]:
    proxy_url = build_proxy_url(proxy_info)
    proxy_handler = urllib.request.ProxyHandler({"http": proxy_url, "https": proxy_url})
    https_handler = urllib.request.HTTPSHandler(context=ssl._create_unverified_context())
    opener = urllib.request.build_opener(proxy_handler, https_handler)
    request = urllib.request.Request(
        target_url,
        headers={
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "User-Agent": "Mozilla/5.0",
        },
    )
    started_at = time.monotonic()
    try:
        with opener.open(request, timeout=timeout_seconds) as response:
            status_code = int(getattr(response, "status", 0) or response.getcode() or 0)
            return True, status_code, (time.monotonic() - started_at) * 1000.0, "ok"
    except urllib.error.HTTPError as exc:
        elapsed_ms = (time.monotonic() - started_at) * 1000.0
        if 500 <= int(exc.code) < 600:
            return False, int(exc.code), elapsed_ms, f"http_{int(exc.code)}"
        return True, int(exc.code), elapsed_ms, "ok"
    except Exception as exc:
        return False, None, (time.monotonic() - started_at) * 1000.0, str(exc)


def _normalize_quality_check(quality_check: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not isinstance(quality_check, Mapping):
        return None

    target_url = str(quality_check.get("target_url") or "").strip()
    if not quality_check.get("enabled") or not target_url:
        return None

    try:
        timeout_seconds = float(quality_check.get("timeout_seconds", 10))
    except (TypeError, ValueError):
        timeout_seconds = 10.0
    try:
        max_latency_ms = float(quality_check.get("max_latency_ms", 5000))
    except (TypeError, ValueError):
        max_latency_ms = 5000.0

    return {
        "target_url": target_url,
        "timeout_seconds": max(1.0, timeout_seconds),
        "max_latency_ms": max(0.0, max_latency_ms),
    }


def _validate_proxy_quality_with_reason(
    proxy_info: dict[str, Any],
    *,
    quality_check: Mapping[str, Any] | None = None,
    bootstrap_proxy: Mapping[str, Any] | None = None,
    logger: ProxyLogger | None = None,
) -> tuple[dict[str, Any] | None, str]:
    normalized_quality_check = _normalize_quality_check(quality_check)
    if not normalized_quality_check:
        return proxy_info, "ok"

    target_url = normalized_quality_check["target_url"]
    timeout_seconds = normalized_quality_check["timeout_seconds"]
    max_latency_ms = normalized_quality_check["max_latency_ms"]

    if bootstrap_proxy:
        ok, status_code, elapsed_ms, reason = probe_url_via_chain(
            bootstrap_proxy,
            proxy_info,
            target_url,
            timeout=max(1, int(timeout_seconds)),
        )
    else:
        ok, status_code, elapsed_ms, reason = _probe_url_via_proxy(
            proxy_info,
            target_url,
            timeout_seconds=timeout_seconds,
        )

    status_text = str(status_code) if status_code is not None else "?"
    if not ok:
        emit_log(
            logger,
            "proxy-quality",
            "skip",
            proxy=describe_proxy(proxy_info),
            target=target_url,
            reason=reason,
            status=status_text,
            latency=f"{elapsed_ms:.0f}ms",
        )
        return None, "quality_probe_failed"

    if max_latency_ms > 0 and elapsed_ms > max_latency_ms:
        emit_log(
            logger,
            "proxy-quality",
            "skip",
            proxy=describe_proxy(proxy_info),
            target=target_url,
            reason="latency",
            status=status_text,
            latency=f"{elapsed_ms:.0f}ms",
            max=f"{max_latency_ms:.0f}ms",
        )
        return None, "quality_latency_exceeded"

    emit_log(
        logger,
        "proxy-quality",
        proxy=describe_proxy(proxy_info),
        target=target_url,
        status=status_text,
        latency=f"{elapsed_ms:.0f}ms",
    )
    return (
        {
            **proxy_info,
            "quality_check_url": target_url,
            "quality_check_status_code": status_code or 0,
            "quality_check_latency_ms": round(elapsed_ms, 1),
        },
        "ok",
    )


def _validate_exit_ip_with_reason(
    proxy_info: dict[str, Any],
    *,
    timeout: int,
    blocked_exit_ips: set[str] | None = None,
    blocked_exit_ips_provider: Callable[[], set[str]] | None = None,
    require_exit_ip: bool = False,
    logger: ProxyLogger | None = None,
) -> tuple[dict[str, Any] | None, str]:
    blocked_ips = {str(ip).strip() for ip in (blocked_exit_ips or set()) if str(ip).strip()}
    if blocked_exit_ips_provider is not None:
        try:
            blocked_ips.update(
                str(ip).strip()
                for ip in blocked_exit_ips_provider()
                if str(ip).strip()
            )
        except Exception as exc:
            emit_log(logger, "proxy-ip", "blocked-source-failed", error=exc)

    effective_require_exit_ip = require_exit_ip or bool(blocked_ips)
    exit_ip = str(proxy_info.get("exit_ip") or "").strip()
    if not exit_ip:
        exit_ip = str(fetch_public_ip_via_proxy(proxy_info, timeout, logger=logger) or "").strip()

    if effective_require_exit_ip and not exit_ip:
        emit_log(logger, "proxy-ip", "skip", proxy=describe_proxy(proxy_info), reason="exit_ip_unavailable")
        return None, "exit_ip_unavailable"

    if exit_ip and exit_ip in blocked_ips:
        emit_log(logger, "proxy-ip", "skip", ip=exit_ip, reason="blocked")
        return None, "blocked_exit_ip"

    if exit_ip:
        return {**proxy_info, "exit_ip": exit_ip}, "ok"
    return proxy_info, "ok"


def _acquire_dynamic_proxy(
    proxy_api: str,
    bootstrap_proxy: Mapping[str, Any],
    *,
    max_attempts: int,
    timeout: int,
    final_check_timeout: int,
    skip_check: bool = False,
    blocked_exit_ips: set[str] | None = None,
    blocked_exit_ips_provider: Callable[[], set[str]] | None = None,
    quality_check: Mapping[str, Any] | None = None,
    require_exit_ip: bool = False,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    consumed_attempts = 0
    fetch_skip_count = 0
    blocked_skip_count = 0
    max_fetch_skips = max(max_attempts * 10, 20)
    max_blocked_skips = max(max_attempts * 10, 20)

    while consumed_attempts < max_attempts:
        emit_log(
            logger,
            "dynamic-proxy",
            "attempt",
            current=f"{consumed_attempts + 1}/{max_attempts}",
            bootstrap=describe_proxy(bootstrap_proxy),
        )
        proxy_info = fetch_proxy_via_bootstrap(
            proxy_api,
            bootstrap_proxy,
            timeout,
            logger=logger,
        )
        if not proxy_info:
            fetch_skip_count += 1
            emit_log(logger, "dynamic-proxy", "fetch-skip", count=fetch_skip_count, consume_retry=False)
            if fetch_skip_count >= max_fetch_skips:
                emit_log(logger, "dynamic-proxy", "stop", reason="too_many_fetch_skips")
                return None
            continue

        fetch_skip_count = 0
        emit_log(logger, "dynamic-proxy", "received", proxy=describe_proxy(proxy_info))
        if skip_check:
            validated_proxy, reason = _validate_exit_ip_with_reason(
                proxy_info,
                timeout=final_check_timeout,
                blocked_exit_ips=blocked_exit_ips,
                blocked_exit_ips_provider=blocked_exit_ips_provider,
                require_exit_ip=require_exit_ip,
                logger=logger,
            )
            if not validated_proxy:
                if reason == "blocked_exit_ip":
                    blocked_skip_count += 1
                    emit_log(
                        logger,
                        "dynamic-proxy",
                        "blocked-skip",
                        count=blocked_skip_count,
                        consume_retry=False,
                    )
                    if blocked_skip_count >= max_blocked_skips:
                        emit_log(logger, "dynamic-proxy", "stop", reason="too_many_blocked_skips")
                        return None
                    continue
                consumed_attempts += 1
                continue

            quality_validated_proxy, quality_reason = _validate_proxy_quality_with_reason(
                validated_proxy,
                quality_check=quality_check,
                bootstrap_proxy=bootstrap_proxy,
                logger=logger,
            )
            if quality_validated_proxy:
                return quality_validated_proxy
            if quality_reason == "quality_latency_exceeded":
                emit_log(logger, "dynamic-proxy", "retry", reason="slow")
            else:
                emit_log(logger, "dynamic-proxy", "retry", reason="unavailable")
            consumed_attempts += 1
            continue

        ok, exit_ip = check_proxy_via_chain(
            bootstrap_proxy,
            proxy_info,
            timeout=final_check_timeout,
            logger=logger,
        )
        if ok:
            validated_proxy, reason = _validate_exit_ip_with_reason(
                {**proxy_info, "exit_ip": exit_ip},
                timeout=final_check_timeout,
                blocked_exit_ips=blocked_exit_ips,
                blocked_exit_ips_provider=blocked_exit_ips_provider,
                require_exit_ip=require_exit_ip,
                logger=logger,
            )
            if not validated_proxy:
                if reason == "blocked_exit_ip":
                    blocked_skip_count += 1
                    emit_log(
                        logger,
                        "dynamic-proxy",
                        "blocked-skip",
                        count=blocked_skip_count,
                        consume_retry=False,
                    )
                    if blocked_skip_count >= max_blocked_skips:
                        emit_log(logger, "dynamic-proxy", "stop", reason="too_many_blocked_skips")
                        return None
                    continue
                consumed_attempts += 1
                continue

            quality_validated_proxy, quality_reason = _validate_proxy_quality_with_reason(
                validated_proxy,
                quality_check=quality_check,
                bootstrap_proxy=bootstrap_proxy,
                logger=logger,
            )
            if quality_validated_proxy:
                return quality_validated_proxy
            if quality_reason == "quality_latency_exceeded":
                emit_log(logger, "dynamic-proxy", "retry", reason="slow")
            else:
                emit_log(logger, "dynamic-proxy", "retry", reason="unavailable")
            consumed_attempts += 1
            continue

        consumed_attempts += 1

    return None


def _start_gost_chain(
    bootstrap_proxy: Mapping[str, Any],
    dynamic_proxy: Mapping[str, Any],
    gost_settings: Mapping[str, Any],
    timeout_seconds: int,
    *,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    gost_bin = str(gost_settings.get("binary") or "gost").strip() or "gost"
    gost_path = shutil.which(gost_bin) if "/" not in gost_bin else gost_bin
    if not gost_path:
        emit_log(logger, "gost", "binary-not-found", binary=gost_bin)
        return None

    listen_host = str(gost_settings.get("listen_host") or "127.0.0.1").strip() or "127.0.0.1"
    listen_port = int(gost_settings.get("listen_port") or 0)
    if listen_port <= 0:
        listen_port = _pick_free_local_port(listen_host)

    local_server = f"http://{listen_host}:{listen_port}"
    bootstrap_url = build_proxy_url(bootstrap_proxy)
    dynamic_url = build_proxy_url(dynamic_proxy)
    command = [
        gost_path,
        f"-L=http://{listen_host}:{listen_port}",
        f"-F={bootstrap_url}",
        f"-F={dynamic_url}",
    ]

    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        emit_log(logger, "gost", "start-failed", error=exc)
        return None

    if not _wait_local_listener(listen_host, listen_port, timeout_seconds):
        stop_proxy_runtime({"_gost_process": process})
        emit_log(logger, "gost", "start-timeout")
        return None
    if process.poll() is not None:
        emit_log(logger, "gost", "start-failed", reason="process_exited", exit_code=process.returncode)
        return None

    return {
        "server": local_server,
        "local_server": local_server,
        "username": "",
        "password": "",
        "exit_ip": dynamic_proxy.get("exit_ip"),
        "bootstrap_upstream": dict(bootstrap_proxy),
        "final_upstream": dict(dynamic_proxy),
        "_gost_process": process,
        "_gost_command": command,
    }


def _start_gost_forwarder(
    upstream_proxy: Mapping[str, Any],
    gost_settings: Mapping[str, Any],
    timeout_seconds: int,
    *,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    gost_bin = str(gost_settings.get("binary") or "gost").strip() or "gost"
    gost_path = shutil.which(gost_bin) if "/" not in gost_bin else gost_bin
    if not gost_path:
        emit_log(logger, "gost", "binary-not-found", binary=gost_bin)
        return None

    listen_host = str(gost_settings.get("listen_host") or "127.0.0.1").strip() or "127.0.0.1"
    listen_port = int(gost_settings.get("listen_port") or 0)
    if listen_port <= 0:
        listen_port = _pick_free_local_port(listen_host)

    local_server = f"http://{listen_host}:{listen_port}"
    upstream_url = build_proxy_url(upstream_proxy)
    command = [gost_path, f"-L=http://{listen_host}:{listen_port}", f"-F={upstream_url}"]

    try:
        process = subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception as exc:
        emit_log(logger, "gost", "start-failed", error=exc)
        return None

    if not _wait_local_listener(listen_host, listen_port, timeout_seconds):
        stop_proxy_runtime({"_gost_process": process})
        emit_log(logger, "gost", "start-timeout")
        return None
    if process.poll() is not None:
        emit_log(logger, "gost", "start-failed", reason="process_exited", exit_code=process.returncode)
        return None

    return {
        "server": local_server,
        "local_server": local_server,
        "username": "",
        "password": "",
        "upstream": dict(upstream_proxy),
        "_gost_process": process,
        "_gost_command": command,
    }


def _build_static_runtime(
    upstream_proxy: dict[str, Any],
    gost_settings: Mapping[str, Any],
    timeout_seconds: int,
    *,
    blocked_exit_ips: set[str] | None = None,
    blocked_exit_ips_provider: Callable[[], set[str]] | None = None,
    quality_check: Mapping[str, Any] | None = None,
    require_exit_ip: bool = False,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    runtime = upstream_proxy
    if _is_gost_requested(gost_settings):
        runtime = _start_gost_forwarder(
            upstream_proxy,
            gost_settings,
            timeout_seconds,
            logger=logger,
        )
        if not runtime:
            emit_log(logger, "gost", "required-forwarder-unavailable")
            return None

    validated_runtime, reason = _validate_exit_ip_with_reason(
        runtime,
        timeout=timeout_seconds,
        blocked_exit_ips=blocked_exit_ips,
        blocked_exit_ips_provider=blocked_exit_ips_provider,
        require_exit_ip=require_exit_ip,
        logger=logger,
    )
    if validated_runtime:
        quality_validated_runtime, _ = _validate_proxy_quality_with_reason(
            validated_runtime,
            quality_check=quality_check,
            logger=logger,
        )
        if quality_validated_runtime:
            return quality_validated_runtime

    if reason != "ok":
        stop_proxy_runtime(runtime)
    else:
        stop_proxy_runtime(runtime)
    return None


def get_proxy_runtime(
    proxy_settings: Mapping[str, Any],
    *,
    max_attempts: int,
    skip_check: bool = False,
    blocked_exit_ips: set[str] | None = None,
    blocked_exit_ips_provider: Callable[[], set[str]] | None = None,
    quality_check: Mapping[str, Any] | None = None,
    require_exit_ip: bool = False,
    logger: ProxyLogger | None = None,
) -> dict[str, Any] | None:
    if not proxy_settings.get("enabled"):
        return None

    static_config = proxy_settings.get("static") or {}
    bootstrap_config = proxy_settings.get("bootstrap") or {}

    static_upstream = {
        "scheme": static_config.get("scheme") or "http",
        "ip": static_config.get("host"),
        "port": static_config.get("port"),
        "user": static_config.get("username"),
        "password": static_config.get("password"),
    }
    if not static_upstream["ip"] or not static_upstream["port"]:
        static_upstream = None

    bootstrap_upstream = {
        "scheme": bootstrap_config.get("scheme") or "http",
        "ip": bootstrap_config.get("host"),
        "port": bootstrap_config.get("port"),
        "user": bootstrap_config.get("username"),
        "password": bootstrap_config.get("password"),
    }
    if not bootstrap_upstream["ip"] or not bootstrap_upstream["port"]:
        bootstrap_upstream = None

    mode = str(proxy_settings.get("mode") or "").strip().lower()
    gost_settings = proxy_settings.get("gost") or {}
    check_timeout = int(proxy_settings.get("check_timeout_seconds", 15))

    if mode == "static":
        if not static_upstream:
            return None
        return _build_static_runtime(
            static_upstream,
            gost_settings,
            check_timeout,
            blocked_exit_ips=blocked_exit_ips,
            blocked_exit_ips_provider=blocked_exit_ips_provider,
            quality_check=quality_check,
            require_exit_ip=require_exit_ip,
            logger=logger,
        )

    if mode == "dynamic":
        if not bootstrap_upstream:
            emit_log(logger, "dynamic-proxy", "config-missing", field="bootstrap")
            return None

        final_upstream = _acquire_dynamic_proxy(
            proxy_api=str(proxy_settings.get("api_url") or ""),
            bootstrap_proxy=bootstrap_upstream,
            max_attempts=max_attempts,
            timeout=int(proxy_settings.get("timeout_seconds", 15)),
            final_check_timeout=check_timeout,
            skip_check=skip_check,
            blocked_exit_ips=blocked_exit_ips,
            blocked_exit_ips_provider=blocked_exit_ips_provider,
            quality_check=quality_check,
            require_exit_ip=require_exit_ip,
            logger=logger,
        )
        if not final_upstream:
            return None

        gost_runtime = _start_gost_chain(
            bootstrap_proxy=bootstrap_upstream,
            dynamic_proxy=final_upstream,
            gost_settings=gost_settings,
            timeout_seconds=check_timeout,
            logger=logger,
        )
        if gost_runtime:
            return gost_runtime
        if _is_gost_requested(gost_settings):
            emit_log(logger, "gost", "required-chain-unavailable")
            return None

        return {
            **final_upstream,
            "exit_ip": final_upstream.get("exit_ip"),
            "bootstrap_upstream": bootstrap_upstream,
            "final_upstream": final_upstream,
        }

    return None
