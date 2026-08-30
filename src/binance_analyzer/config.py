import json
import os
from pathlib import Path
from urllib.parse import quote, urlparse, urlunparse

DEFAULT_PROXY_POOL_ID = "default"


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


def _load_proxy_profile(base_dir: Path, automation_config: dict) -> dict:
    """加载独立代理策略，并保留 automation.json 中的 bootstrap 白名单出口。"""
    proxy_path = base_dir / "config" / "proxy.json"
    if not proxy_path.exists():
        return automation_config

    try:
        with proxy_path.open("r", encoding="utf-8") as file:
            proxy_document = json.load(file)
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"代理配置文件读取失败: {proxy_path}: {exc}") from exc
    if not isinstance(proxy_document, dict):
        raise ValueError("config/proxy.json 必须是 JSON 对象")

    profiles = proxy_document.get("profiles")
    if not isinstance(profiles, dict) or not profiles:
        raise ValueError("config/proxy.json 必须配置非空 profiles 对象")

    automation_proxy = _require_dict(automation_config, "proxy")
    profile_name = str(automation_proxy.get("profile") or automation_proxy.get("proxy_profile") or "").strip()
    if not profile_name:
        raise ValueError("config/automation.json.proxy.profile 必须配置")
    profile = profiles.get(profile_name)
    if not isinstance(profile, dict):
        raise ValueError(f"config/proxy.json 不存在代理 profile: {profile_name}")

    merged_proxy = dict(profile)
    merged_proxy["enabled"] = _require_bool(automation_proxy, "enabled")
    static_ref = str(merged_proxy.get("static_ref") or "").strip()
    if static_ref:
        if static_ref != "automation.proxy.static" or not isinstance(automation_proxy.get("static"), dict):
            raise ValueError("static profile 必须使用 automation.proxy.static 作为 static_ref")
        merged_proxy["static"] = dict(automation_proxy["static"])
        merged_proxy.pop("static_ref", None)
    api_url_ref = str(merged_proxy.get("api_url_ref") or "").strip()
    if api_url_ref:
        if api_url_ref != "automation.proxy.api_url" or not automation_proxy.get("api_url"):
            raise ValueError("动态 profile 的 api_url_ref 必须指向 automation.proxy.api_url")
        merged_proxy["api_url"] = str(automation_proxy["api_url"]).strip()
        merged_proxy.pop("api_url_ref", None)
    if merged_proxy.get("mode") == "dynamic":
        bootstrap_ref = str(merged_proxy.get("bootstrap_ref") or "").strip()
        if bootstrap_ref:
            if bootstrap_ref != "automation.proxy.bootstrap":
                raise ValueError("dynamic profile 的 bootstrap_ref 必须指向 automation.proxy.bootstrap")
            bootstrap = automation_proxy.get("bootstrap")
        else:
            bootstrap = merged_proxy.get("bootstrap")
        if not isinstance(bootstrap, dict) or not bootstrap.get("host") or not bootstrap.get("port"):
            raise ValueError("动态代理必须配置 proxy.json 中的 bootstrap 白名单出口")
        merged_proxy["bootstrap"] = dict(bootstrap)
        merged_proxy.pop("bootstrap_ref", None)
    if "gost" in proxy_document:
        if not isinstance(proxy_document["gost"], dict):
            raise ValueError("config/proxy.json.gost 必须是对象")
        merged_proxy["gost"] = dict(proxy_document["gost"])

    result = dict(automation_config)
    result["proxy"] = merged_proxy
    return result


def load_proxy_pool(base_dir: Path, proxy_config: dict, profile_name: str = "rotating_single_ip") -> dict:
    """读取固定代理池，保留文件顺序；空行和 # 注释会被忽略。"""
    profile = (proxy_config.get("profiles") or {}).get(profile_name)
    if not isinstance(profile, dict) or profile.get("mode") != "rotating_single_ip":
        raise ValueError(f"代理配置缺少 rotating_single_ip profile: {profile_name}")
    pool_file = str(profile.get("pool_file") or "").strip()
    if not pool_file:
        raise ValueError("rotating_single_ip 必须配置 pool_file")
    path = Path(pool_file)
    if not path.is_absolute():
        path = base_dir / path
    if not path.exists():
        raise FileNotFoundError(f"固定代理池文件不存在: {path}")
    raw_text = path.read_text(encoding="utf-8")
    values = []
    if path.suffix.lower() == ".json":
        try:
            entries = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"固定代理池 JSON 无效: {path}: {exc}") from exc
        if not isinstance(entries, list):
            raise ValueError("固定代理池 JSON 必须是数组")
        for index, entry in enumerate(entries, 1):
            if not isinstance(entry, dict):
                raise ValueError(f"固定代理池第 {index} 项必须是对象")
            scheme = str(entry.get("scheme") or "").strip().lower()
            host = str(entry.get("host") or "").strip()
            port = entry.get("port")
            username = str(entry.get("username") or "")
            password = str(entry.get("password") or "")
            if scheme not in {"http", "https", "socks5", "socks5h"} or not host or not str(port).isdigit() or not (1 <= int(port) <= 65535):
                raise ValueError(f"固定代理池第 {index} 项必须包含有效 scheme/host/port")
            values.append({"scheme": scheme, "host": host, "port": int(port), "username": username, "password": password})
    else:
        for line_number, raw in enumerate(raw_text.splitlines(), 1):
            value = raw.strip()
            if not value or value.startswith("#"):
                continue
            parsed = urlparse(value)
            if parsed.scheme.lower() not in {"http", "https", "socks5", "socks5h"} or not parsed.hostname or not parsed.port:
                raise ValueError(f"固定代理池第 {line_number} 行格式无效，需 scheme://host:port")
            values.append({"scheme": parsed.scheme.lower(), "host": parsed.hostname, "port": parsed.port, "username": parsed.username or "", "password": parsed.password or ""})
    if not values:
        raise ValueError(f"固定代理池为空: {path}")
    # 一个 IP 连续多少个账号最终失败后切换；旧字段仅保留兼容读取。
    threshold_value = profile.get("switch_after_account_failures", profile.get("switch_after_consecutive_account_failures", 3))
    threshold = _positive_int(threshold_value, key="proxy.switch_after_account_failures")
    cooldown_seconds = _positive_int(profile.get("cooldown_seconds", 86400), key="proxy.cooldown_seconds")
    allow_parallel = profile.get("allow_parallel", False)
    if not isinstance(allow_parallel, bool):
        raise ValueError("proxy.allow_parallel 必须是布尔值")
    return {
        "pool_id": DEFAULT_PROXY_POOL_ID,
        "addresses": [urlunparse((entry["scheme"], f"{quote(entry['username'], safe='')}:{quote(entry['password'], safe='')}@{entry['host']}:{entry['port']}" if entry["username"] else f"{entry['host']}:{entry['port']}", "", "", "", "")) for entry in values],
        "entries": values,
        "switch_threshold": threshold,
        "cooldown_seconds": cooldown_seconds,
        "allow_parallel": allow_parallel,
        "path": str(path),
    }


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
    if "start_url" not in register_config or register_config.get("start_url") in (None, ""):
        register_config["start_url"] = "https://accounts.binance.com/zh-CN/register"
    else:
        _require_text(register_config, "start_url")
    if "warmup_url" not in register_config:
        register_config["warmup_url"] = "https://www.binance.com/zh-CN"
    elif register_config["warmup_url"] is None:
        register_config["warmup_url"] = ""
    elif not isinstance(register_config["warmup_url"], str):
        raise ValueError("配置 register.warmup_url 必须是字符串")
    else:
        register_config["warmup_url"] = register_config["warmup_url"].strip()


def _normalize_fingerprint_config(config: dict) -> None:
    fingerprint = config.get("fingerprint")
    if fingerprint is None:
        fingerprint = {}
        config["fingerprint"] = fingerprint
    if not isinstance(fingerprint, dict):
        raise ValueError("配置 fingerprint 必须是对象")
    mode = str(fingerprint.get("mode") or "native").strip().lower()
    if mode not in {"native", "spoofed"}:
        raise ValueError("配置 fingerprint.mode 只支持 native/spoofed")
    fingerprint["mode"] = mode


def _normalize_creator_api_config(config: dict) -> None:
    creator = config.get("creator_api")
    if creator is None:
        creator = {}
        config["creator_api"] = creator
    if not isinstance(creator, dict):
        raise ValueError("配置 creator_api 必须是对象")
    if "enabled" not in creator:
        creator["enabled"] = False
    _require_bool(creator, "enabled")
    creator["max_accounts"] = _positive_int(creator.get("max_accounts", 1), key="creator_api.max_accounts")
    creator["slot_wait_timeout_sec"] = _positive_int(
        creator.get("slot_wait_timeout_sec", 600), key="creator_api.slot_wait_timeout_sec"
    )


def load_config(base_dir: Path, filename: str = "config/automation.json") -> dict:
    """读取自动化角色配置并补齐当前版本的运行默认值。"""
    config_path = base_dir / filename
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)
    if not isinstance(config, dict):
        raise ValueError(f"{filename} 必须是 JSON 对象")

    if filename == "config/automation.json":
        config = _load_proxy_profile(base_dir, config)

    if "mode" not in config:
        raise ValueError("缺少必填配置: mode")
    mode = str(config.get("mode") or "").strip().lower()
    config["mode"] = mode
    if config["mode"] not in {"login", "register"}:
        raise ValueError("配置 mode 只支持 login/register")

    login_config = _require_dict(config, "login")
    _require_text(login_config, "start_url")
    _normalize_register_config(config)
    _normalize_fingerprint_config(config)
    _normalize_creator_api_config(config)

    _normalize_captcha_config(config)

    _require_bool(config, "headless")
    _require_positive_int(config, "max_login_retries")

    cache_config = _require_dict(config, "cache")
    _require_bool(cache_config, "enabled")

    proxy_config = _require_dict(config, "proxy")
    _require_bool(proxy_config, "enabled")

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
        raise ValueError(f"缺少 OpenRouter API Key，请设置 OPENROUTER_API_KEY 或在 {filename} 中配置 openrouter_api_key")

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
