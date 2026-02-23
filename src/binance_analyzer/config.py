import json
import os
from pathlib import Path


def load_config(base_dir: Path) -> dict:
    """Read config and apply backwards-compatible defaults."""
    config_path = base_dir / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    config.setdefault("login", {})
    config["login"].setdefault("start_url", "https://accounts.binance.com/zh-CN/login")

    config.setdefault("captcha", {})
    config["captcha"].setdefault("retry_mode", "fast")
    config["captcha"].setdefault("max_attempts_per_round", 5)
    config["captcha"].setdefault("max_rounds", 3)
    config["captcha"].setdefault("cooldown_on_risk_min_sec", 20)
    config["captcha"].setdefault("cooldown_on_risk_max_sec", 60)
    config["captcha"].setdefault("click_retry_per_cell", 3)

    config.setdefault("cache", {})
    config["cache"].setdefault("enabled", True)

    config.setdefault("runtime", {})
    config["runtime"].setdefault("max_workers_default", 2)
    config["runtime"].setdefault("start_delay_min_sec", 8)
    config["runtime"].setdefault("start_delay_max_sec", 20)

    config.setdefault("mfa", {})
    config["mfa"].setdefault("submit_retry", 2)
    config["mfa"].setdefault(
        "not_registered_keywords",
        ["未注册", "账号不存在", "account does not exist", "not registered", "没有账号"],
    )

    env_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if env_api_key:
        config["openrouter_api_key"] = env_api_key

    if not config.get("openrouter_api_key"):
        raise ValueError("缺少 OpenRouter API Key，请设置 OPENROUTER_API_KEY 或在 config.json 中配置 openrouter_api_key")

    return config
