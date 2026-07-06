import json
import os
from pathlib import Path


def _require_dict(config: dict, key: str) -> dict:
    value = config.get(key)
    if not isinstance(value, dict):
        raise ValueError(f"配置 {key} 必须是对象")
    return value


def _require_text(config: dict, key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少必填配置: {key}")
    config[key] = value
    return value


def _require_positive_int(config: dict, key: str) -> int:
    value = config.get(key)
    if isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置 {key} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"配置 {key} 必须是正整数")
    config[key] = parsed
    return parsed


def _require_bool(config: dict, key: str) -> bool:
    value = config.get(key)
    if not isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是布尔值")
    return value


def _positive_int(value, *, key: str) -> int:
    if isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是正整数")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"配置 {key} 必须是正整数") from exc
    if parsed <= 0:
        raise ValueError(f"配置 {key} 必须是正整数")
    return parsed


def _normalize_captcha_config(config: dict) -> None:
    captcha = _require_dict(config, "captcha")

    unsupported_keys = {"cooldown_min_sec", "cooldown_max_sec"} & set(captcha)
    if unsupported_keys:
        names = ", ".join(sorted(unsupported_keys))
        raise ValueError(f"captcha 配置包含不支持的旧字段: {names}")

    _require_text(captcha, "retry_mode")
    captcha["max_attempts_per_round"] = _positive_int(
        captcha.get("max_attempts_per_round"),
        key="captcha.max_attempts_per_round",
    )
    captcha["max_rounds"] = _positive_int(captcha.get("max_rounds"), key="captcha.max_rounds")
    captcha["cooldown_on_risk_min_sec"] = _positive_int(
        captcha.get("cooldown_on_risk_min_sec"),
        key="captcha.cooldown_on_risk_min_sec",
    )
    captcha["cooldown_on_risk_max_sec"] = _positive_int(
        captcha.get("cooldown_on_risk_max_sec"),
        key="captcha.cooldown_on_risk_max_sec",
    )
    captcha["click_retry_per_cell"] = _positive_int(
        captcha.get("click_retry_per_cell"),
        key="captcha.click_retry_per_cell",
    )
    if captcha["cooldown_on_risk_min_sec"] > captcha["cooldown_on_risk_max_sec"]:
        raise ValueError("配置 captcha.cooldown_on_risk_min_sec 不能大于 captcha.cooldown_on_risk_max_sec")


def _normalize_register_config(config: dict) -> None:
    register_config = config.get("register")
    if register_config is None:
        register_config = {}
        config["register"] = register_config
    if not isinstance(register_config, dict):
        raise ValueError("配置 register 必须是对象")
    register_config["submit_error_ack_max_attempts"] = _positive_int(
        register_config.get("submit_error_ack_max_attempts", 3),
        key="register.submit_error_ack_max_attempts",
    )


def load_config(base_dir: Path) -> dict:
    """读取配置并补齐当前版本的运行默认值。"""
    config_path = base_dir / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError("config.json 必须是 JSON 对象")

    if "mode" not in config:
        raise ValueError("缺少必填配置: mode")
    mode = str(config.get("mode") or "").strip().lower()
    config["mode"] = mode
    if config["mode"] not in {"login", "register"}:
        raise ValueError("配置 mode 只支持 login/register")

    login_config = _require_dict(config, "login")
    _require_text(login_config, "start_url")
    _normalize_register_config(config)

    _normalize_captcha_config(config)

    _require_bool(config, "headless")
    _require_positive_int(config, "max_login_retries")

    cache_config = _require_dict(config, "cache")
    _require_bool(cache_config, "enabled")

    proxy_config = _require_dict(config, "proxy")
    _require_bool(proxy_config, "enabled")
    _require_text(proxy_config, "used_ips_file")

    runtime_config = _require_dict(config, "runtime")
    runtime_config["max_workers_default"] = _positive_int(
        runtime_config.get("max_workers_default"),
        key="runtime.max_workers_default",
    )
    runtime_config["retry_delay_min_sec"] = _positive_int(
        runtime_config.get("retry_delay_min_sec"),
        key="runtime.retry_delay_min_sec",
    )
    runtime_config["retry_delay_max_sec"] = _positive_int(
        runtime_config.get("retry_delay_max_sec"),
        key="runtime.retry_delay_max_sec",
    )
    runtime_config["proxy_retry_delay_min_sec"] = _positive_int(
        runtime_config.get("proxy_retry_delay_min_sec"),
        key="runtime.proxy_retry_delay_min_sec",
    )
    runtime_config["proxy_retry_delay_max_sec"] = _positive_int(
        runtime_config.get("proxy_retry_delay_max_sec"),
        key="runtime.proxy_retry_delay_max_sec",
    )
    if runtime_config["retry_delay_min_sec"] > runtime_config["retry_delay_max_sec"]:
        raise ValueError("配置 runtime.retry_delay_min_sec 不能大于 runtime.retry_delay_max_sec")
    if runtime_config["proxy_retry_delay_min_sec"] > runtime_config["proxy_retry_delay_max_sec"]:
        raise ValueError("配置 runtime.proxy_retry_delay_min_sec 不能大于 runtime.proxy_retry_delay_max_sec")

    mfa_config = _require_dict(config, "mfa")
    mfa_config["submit_retry"] = _positive_int(mfa_config.get("submit_retry"), key="mfa.submit_retry")
    if "email_verification_enabled" not in mfa_config:
        mfa_config["email_verification_enabled"] = True
    _require_bool(mfa_config, "email_verification_enabled")
    keywords = mfa_config.get("not_registered_keywords")
    if not isinstance(keywords, list) or not all(str(keyword).strip() for keyword in keywords):
        raise ValueError("配置 mfa.not_registered_keywords 必须是非空字符串列表")

    env_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if env_api_key:
        config["openrouter_api_key"] = env_api_key

    if not config.get("openrouter_api_key"):
        raise ValueError("缺少 OpenRouter API Key，请设置 OPENROUTER_API_KEY 或在 config.json 中配置 openrouter_api_key")

    models = config.get("models")
    if not isinstance(models, list):
        raise ValueError("配置 models 必须是非空字符串列表")
    models = [str(model).strip() for model in models if str(model).strip()]
    if not models:
        raise ValueError("配置 models 至少需要一个模型名称")
    config["models"] = models

    _require_text(config, "imap_host")
    _require_positive_int(config, "imap_port")
    _require_text(config, "accounts_file")
    _require_text(config, "output_file")
    if "max_workers" in config:
        _require_positive_int(config, "max_workers")
    return config
