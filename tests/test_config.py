from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from binance_analyzer.config import load_config


def _write_config(base_dir: Path, **overrides):
    config = {
        "openrouter_api_key": "sk-test",
        "models": ["model-a"],
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "accounts_file": "accounts.txt",
        "output_file": "output/registered_accounts.json",
        "mode": "login",
        "headless": False,
        "max_workers": 1,
        "max_login_retries": 3,
        "login": {"start_url": "https://accounts.binance.com/zh-CN/login"},
        "captcha": {
            "retry_mode": "fast",
            "max_attempts_per_round": 2,
            "max_rounds": 3,
            "cooldown_on_risk_min_sec": 30,
            "cooldown_on_risk_max_sec": 90,
            "click_retry_per_cell": 3,
        },
        "cache": {"enabled": True},
        "proxy": {"enabled": False, "used_ips_file": "output/used_proxy_ips.txt"},
        "runtime": {
            "max_workers_default": 2,
            "retry_delay_min_sec": 20,
            "retry_delay_max_sec": 60,
            "proxy_retry_delay_min_sec": 10,
            "proxy_retry_delay_max_sec": 30,
        },
        "mfa": {
            "submit_retry": 2,
            "not_registered_keywords": ["未注册", "account does not exist"],
        },
    }
    config.update(overrides)
    config_dir = base_dir / "config"
    config_dir.mkdir()
    (config_dir / "automation.json").write_text(json.dumps(config), encoding="utf-8")


class ConfigTests(unittest.TestCase):
    def test_load_config_uses_automation_config_not_root_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            (base_dir / "config.json").write_text("not json", encoding="utf-8")

            config = load_config(base_dir)

            self.assertEqual(config["imap_host"], "imap.example.com")
            self.assertNotIn("debug_mode", config)

    def test_load_config_rejects_removed_captcha_cooldown_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(
                base_dir,
                captcha={
                    "max_attempts_per_round": 2,
                    "max_rounds": 4,
                    "cooldown_min_sec": 30,
                    "cooldown_max_sec": 90,
                },
            )

            with self.assertRaisesRegex(ValueError, "cooldown"):
                load_config(base_dir)

    def test_load_config_rejects_empty_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, models=[])

            with self.assertRaisesRegex(ValueError, "models"):
                load_config(base_dir)

    def test_load_config_rejects_string_models(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, models="model-a")

            with self.assertRaisesRegex(ValueError, "models"):
                load_config(base_dir)

    def test_load_config_rejects_invalid_max_workers(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, max_workers=0)

            with self.assertRaisesRegex(ValueError, "max_workers"):
                load_config(base_dir)

    def test_load_config_defaults_email_verification_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)

            config = load_config(base_dir)

            self.assertTrue(config["mfa"]["email_verification_enabled"])

    def test_load_config_defaults_creator_api_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            config = load_config(base_dir)
            self.assertFalse(config["creator_api"]["enabled"])
            self.assertEqual(config["creator_api"]["max_accounts"], 1)

    def test_load_config_rejects_invalid_creator_api_max_accounts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, creator_api={"enabled": True, "max_accounts": 0})
            with self.assertRaisesRegex(ValueError, "creator_api.max_accounts"):
                load_config(base_dir)

    def test_load_config_rejects_non_bool_email_verification_flag(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(
                base_dir,
                mfa={
                    "submit_retry": 2,
                    "email_verification_enabled": "false",
                    "not_registered_keywords": ["未注册"],
                },
            )

            with self.assertRaisesRegex(ValueError, "email_verification_enabled"):
                load_config(base_dir)

    def test_load_config_defaults_register_submit_error_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)

            config = load_config(base_dir)

            self.assertEqual(config["register"]["submit_error_ack_max_attempts"], 3)

    def test_load_config_rejects_invalid_register_submit_error_attempts(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, register={"submit_error_ack_max_attempts": 0})

            with self.assertRaisesRegex(ValueError, "register.submit_error_ack_max_attempts"):
                load_config(base_dir)

    def test_load_config_requires_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            config_path = base_dir / "config" / "automation.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.pop("mode")
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "mode"):
                load_config(base_dir)

    def test_load_config_requires_runtime_defaults(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            config_path = base_dir / "config" / "automation.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config["runtime"].pop("max_workers_default")
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "runtime.max_workers_default"):
                load_config(base_dir)

    def test_load_config_requires_headless(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            config_path = base_dir / "config" / "automation.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.pop("headless")
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "headless"):
                load_config(base_dir)

    def test_load_config_requires_max_login_retries(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir)
            config_path = base_dir / "config" / "automation.json"
            config = json.loads(config_path.read_text(encoding="utf-8"))
            config.pop("max_login_retries")
            config_path.write_text(json.dumps(config), encoding="utf-8")

            with self.assertRaisesRegex(ValueError, "max_login_retries"):
                load_config(base_dir)


if __name__ == "__main__":
    unittest.main()
