"""代理解析、格式化与客户端配置工具。"""

from __future__ import annotations

import json
import urllib.parse
from typing import Any, Mapping


def parse_proxy_text(text: str) -> dict[str, Any] | None:
    normalized = str(text or "").strip()
    if not normalized:
        return None

    try:
        payload = json.loads(normalized)
    except json.JSONDecodeError:
        payload = None

    if isinstance(payload, dict):
        direct_ip = payload.get("ip")
        direct_port = payload.get("port")
        if direct_ip and direct_port:
            return {
                "ip": str(direct_ip),
                "port": str(direct_port),
                "user": str(payload.get("user") or payload.get("username") or ""),
                "password": str(payload.get("password") or ""),
            }

        data = payload.get("data")
        if isinstance(data, list) and data:
            first = data[0]
            if isinstance(first, dict) and first.get("ip") and first.get("port"):
                return {
                    "ip": str(first["ip"]),
                    "port": str(first["port"]),
                    "user": str(first.get("user") or first.get("username") or ""),
                    "password": str(first.get("password") or ""),
                }

    candidates = [line.strip() for line in normalized.replace("\r", "\n").split("\n")]
    candidates = [line for line in candidates if line]
    for candidate in candidates:
        if "@" in candidate:
            parts = candidate.split("@")
            if len(parts) == 4:
                return {
                    "ip": parts[0],
                    "port": parts[1],
                    "user": parts[2],
                    "password": parts[3],
                }
            continue
        if ":" in candidate:
            parts = candidate.split(":")
            if len(parts) == 2:
                return {"ip": parts[0], "port": parts[1], "user": "", "password": ""}
    return None


def build_proxy_url(proxy_info: Mapping[str, Any]) -> str:
    server = str(proxy_info.get("server") or "").strip()
    if server:
        return server

    username = proxy_info.get("user") or proxy_info.get("username") or ""
    password = proxy_info.get("password") or ""
    credentials = ""
    if username and password:
        user = urllib.parse.quote(str(username), safe="")
        pwd = urllib.parse.quote(str(password), safe="")
        credentials = f"{user}:{pwd}@"

    host = proxy_info.get("ip") or proxy_info.get("host") or ""
    port = proxy_info.get("port") or ""
    scheme = str(proxy_info.get("scheme") or "http").strip().lower()
    if scheme not in {"http", "socks5", "socks5h"}:
        raise ValueError(f"不支持的代理协议: {scheme}")
    return f"{scheme}://{credentials}{host}:{port}"


def describe_proxy(proxy_info: Mapping[str, Any] | None) -> str:
    if not proxy_info:
        return "disabled"
    if proxy_info.get("local_server") and proxy_info.get("final_upstream"):
        return (
            f"{proxy_info['local_server']}"
            f" -> {describe_proxy(proxy_info.get('bootstrap_upstream'))}"
            f" -> {describe_proxy(proxy_info.get('final_upstream'))}"
        )
    if proxy_info.get("server"):
        return str(proxy_info["server"])
    if proxy_info.get("ip") and proxy_info.get("port"):
        return f"{proxy_info['ip']}:{proxy_info['port']}"
    return "unknown"


def public_proxy_info(proxy_info: Any):
    if not isinstance(proxy_info, dict):
        return proxy_info
    return {
        key: public_proxy_info(value)
        for key, value in proxy_info.items()
        if not str(key).startswith("_")
    }


def build_proxy_client_config(proxy_info: Mapping[str, Any]) -> dict[str, str]:
    config = {"server": build_proxy_url(proxy_info)}
    username = proxy_info.get("user") or proxy_info.get("username") or ""
    password = proxy_info.get("password") or ""
    if username and password:
        config["username"] = str(username)
        config["password"] = str(password)
    return config


def build_playwright_proxy_config(proxy_info: Mapping[str, Any]) -> dict[str, str]:
    server = str(proxy_info.get("server") or "").strip()
    username = str(proxy_info.get("user") or proxy_info.get("username") or "")
    password = str(proxy_info.get("password") or "")

    if server:
        parsed = urllib.parse.urlsplit(server)
        if parsed.username and not username:
            username = urllib.parse.unquote(parsed.username)
        if parsed.password and not password:
            password = urllib.parse.unquote(parsed.password)
        if parsed.hostname and parsed.port:
            server = urllib.parse.urlunsplit(
                (
                    parsed.scheme or "http",
                    f"{parsed.hostname}:{parsed.port}",
                    parsed.path,
                    parsed.query,
                    parsed.fragment,
                )
            )
    else:
        host = proxy_info.get("ip") or proxy_info.get("host") or ""
        port = proxy_info.get("port") or ""
        server = f"http://{host}:{port}"

    config = {"server": server}
    if username and password:
        config["username"] = username
        config["password"] = password
    return config
