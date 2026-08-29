from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping


DEFAULT_PROXY_CONFIG: dict[str, Any] = {
    "enabled": False,
    "mode": "dynamic",
    "api_url": "",
    "timeout_seconds": 15,
    "check_timeout_seconds": 15,
    "proxy_quality_check_enabled": None,
    "proxy_quality_check_timeout_seconds": None,
    "proxy_quality_check_max_latency_ms": None,
    "proxy_quality_check_url": None,
    "static": {
        "scheme": "http",
        "host": "",
        "port": "",
        "username": "",
        "password": "",
    },
    "bootstrap": {
        "scheme": "http",
        "host": "",
        "port": "",
        "username": "",
        "password": "",
    },
    "gost": {
        "binary": "gost",
        "listen_host": "127.0.0.1",
        "listen_port": 0,
    },
}


def _sanitize_url_text(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    sanitized_parts: list[str] = []
    for char in text:
        code = ord(char)
        if code < 32 or code == 127:
            sanitized_parts.append(f"%{code:02X}")
        else:
            sanitized_parts.append(char)
    return "".join(sanitized_parts)


def _require_bool(value: Any, *, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是布尔值")
    return value


def _optional_bool(value: Any, *, key: str) -> bool | None:
    if value is None:
        return None
    return _require_bool(value, key=key)


def load_json_config(config_path: str | Path) -> dict[str, Any]:
    path = Path(config_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"config file does not exist: {path}")

    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, dict):
        raise TypeError(f"config file must contain a JSON object: {path}")
    return data


def build_proxy_quality_check(
    site_config: Mapping[str, Any] | None,
    proxy_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    site_config = site_config or {}
    proxy_config = proxy_config or {}

    def resolve_value(key: str, default: Any) -> Any:
        if key in proxy_config and proxy_config.get(key) is not None:
            return proxy_config.get(key)
        return site_config.get(key, default)

    target_url = str(
        resolve_value("proxy_quality_check_url", site_config.get("url") or "")
        or site_config.get("url")
        or ""
    ).strip()
    default_enabled = bool(proxy_config) and _require_bool(proxy_config.get("enabled", False), key="proxy.enabled")
    configured_enabled = _optional_bool(
        resolve_value("proxy_quality_check_enabled", None),
        key="proxy.proxy_quality_check_enabled",
    )
    # 固定代理地址无需因延迟阈值被误判淘汰；动态代理仍按配置/默认值执行质量检测。
    mode = str(proxy_config.get("mode") or "").strip().lower()
    enabled = (False if mode == "static" else default_enabled) if configured_enabled is None else configured_enabled

    try:
        timeout_seconds = float(resolve_value("proxy_quality_check_timeout_seconds", 10))
    except (TypeError, ValueError):
        raise ValueError("配置 proxy.proxy_quality_check_timeout_seconds 必须是数字")
    try:
        max_latency_ms = float(resolve_value("proxy_quality_check_max_latency_ms", 2500))
    except (TypeError, ValueError):
        raise ValueError("配置 proxy.proxy_quality_check_max_latency_ms 必须是数字")

    return {
        "enabled": enabled and bool(target_url),
        "target_url": target_url,
        "timeout_seconds": max(1.0, timeout_seconds),
        "max_latency_ms": max(0.0, max_latency_ms),
    }


def resolve_proxy_settings(
    proxy_config: Mapping[str, Any],
    *,
    force_disable: bool = False,
    override_api: str | None = None,
) -> dict[str, Any]:
    static = proxy_config.get("static") or {}
    bootstrap = proxy_config.get("bootstrap") or {}
    gost = proxy_config.get("gost") or {}

    static_host = str(static.get("host") or "").strip()
    static_scheme = str(static.get("scheme") or "http").strip().lower()
    static_port = str(static.get("port") or "").strip()
    static_username = str(static.get("username") or "").strip()
    static_password = str(static.get("password") or "")

    bootstrap_host = str(bootstrap.get("host") or "").strip()
    bootstrap_scheme = str(bootstrap.get("scheme") or "http").strip().lower()
    bootstrap_port = str(bootstrap.get("port") or "").strip()
    bootstrap_username = str(bootstrap.get("username") or "").strip()
    bootstrap_password = str(bootstrap.get("password") or "")

    return {
        "enabled": _require_bool(proxy_config.get("enabled", False), key="proxy.enabled") and not force_disable,
        "mode": str(proxy_config.get("mode") or "").strip(),
        "api_url": _sanitize_url_text(override_api or proxy_config.get("api_url") or ""),
        "timeout_seconds": int(proxy_config.get("timeout_seconds", 15)),
        "check_timeout_seconds": int(proxy_config.get("check_timeout_seconds", 15)),
        "static": {
            "scheme": static_scheme,
            "host": static_host,
            "port": static_port,
            "username": static_username,
            "password": static_password,
        },
        "bootstrap": {
            "scheme": bootstrap_scheme,
            "host": bootstrap_host,
            "port": bootstrap_port,
            "username": bootstrap_username,
            "password": bootstrap_password,
        },
        "gost": {
            "binary": str(gost.get("binary", "gost") or "gost").strip() or "gost",
            "listen_host": str(gost.get("listen_host", "127.0.0.1") or "127.0.0.1").strip()
            or "127.0.0.1",
            "listen_port": int(gost.get("listen_port", 0) or 0),
        },
    }
