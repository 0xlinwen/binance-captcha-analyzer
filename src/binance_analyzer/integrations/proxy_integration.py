from __future__ import annotations

from typing import Any, Callable, Mapping

from proxy_forwarder import (
    ProxyLogger,
    build_playwright_proxy_config,
    build_proxy_quality_check,
    default_log,
    describe_proxy,
    get_proxy_runtime,
    resolve_proxy_settings,
    stop_proxy_runtime,
)


def make_proxy_logger(prefix: str) -> ProxyLogger:
    prefix_text = str(prefix).strip()

    def log(stage: str, message: str = "", /, **fields: Any) -> None:
        default_log(f"{prefix_text} {stage}".strip(), message, **fields)

    return log


def normalize_proxy_module_config(proxy_config: Mapping[str, Any] | None) -> dict[str, Any]:
    if proxy_config is None:
        return {"enabled": False}
    if not isinstance(proxy_config, Mapping):
        raise ValueError("proxy 配置必须是对象")
    native_keys = {"enabled", "mode", "api_url", "static", "bootstrap", "gost"}
    unknown_keys = set(proxy_config) - native_keys - {
        "timeout_seconds",
        "check_timeout_seconds",
        "proxy_quality_check_enabled",
        "proxy_quality_check_timeout_seconds",
        "proxy_quality_check_max_latency_ms",
        "proxy_quality_check_url",
        "max_attempts",
        "used_ips_file",
    }
    if unknown_keys:
        names = ", ".join(sorted(unknown_keys))
        raise ValueError(f"proxy 配置包含不支持的字段: {names}")
    return dict(proxy_config)


def _resolve_site_config(app_config: Mapping[str, Any]) -> dict[str, str]:
    login_config = app_config["login"]
    login_start_url = str(login_config["start_url"]).strip()
    if not login_start_url:
        raise ValueError("缺少必填配置: login.start_url")
    return {"url": login_start_url}


def _has_bootstrap_proxy(proxy_settings: Mapping[str, Any]) -> bool:
    bootstrap = proxy_settings.get("bootstrap") or {}
    return bool(bootstrap.get("host")) and bool(bootstrap.get("port"))


def create_proxy_runtime(
    app_config: Mapping[str, Any],
    *,
    max_attempts: int = 5,
    skip_check: bool = False,
    blocked_exit_ips: set[str] | None = None,
    blocked_exit_ips_provider: Callable[[], set[str]] | None = None,
    logger: ProxyLogger | None = None,
    require_exit_ip: bool = False,
) -> dict[str, Any] | None:
    raw_proxy_config = normalize_proxy_module_config(app_config.get("proxy"))
    proxy_settings = resolve_proxy_settings(raw_proxy_config)
    if not proxy_settings.get("enabled"):
        return None

    quality_check = build_proxy_quality_check(_resolve_site_config(app_config), raw_proxy_config)

    if proxy_settings.get("mode") == "dynamic" and not _has_bootstrap_proxy(proxy_settings):
        raise ValueError("dynamic 代理模式必须配置 proxy.bootstrap")

    return get_proxy_runtime(
        proxy_settings,
        max_attempts=max(1, max_attempts),
        skip_check=skip_check,
        blocked_exit_ips=blocked_exit_ips,
        blocked_exit_ips_provider=blocked_exit_ips_provider,
        quality_check=quality_check,
        require_exit_ip=require_exit_ip,
        logger=logger,
    )


def build_proxy_launch_config(proxy_runtime: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not proxy_runtime:
        return None
    return build_playwright_proxy_config(proxy_runtime)


def describe_proxy_runtime(proxy_runtime: Mapping[str, Any] | None) -> str:
    return describe_proxy(proxy_runtime)


def stop_managed_proxy_runtime(proxy_runtime: Mapping[str, Any] | None) -> None:
    stop_proxy_runtime(proxy_runtime)
