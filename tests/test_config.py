from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from binance_analyzer.config import load_config, load_proxy_pool


def _write_config(base_dir: Path, **overrides):
    config = {
        "openrouter_api_key": "sk-test",
        "models": ["model-a"],
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "accounts_file": "accounts.txt",
        "output_file": "data/results/registered_accounts.json",
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
        "proxy": {"enabled": False, "used_ips_file": "data/runtime/used_proxy_ips.txt"},
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
    def test_load_proxy_pool_preserves_order_and_ignores_comments(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            pool_path = base_dir / "config" / "proxy_pool.txt"
            pool_path.parent.mkdir()
            pool_path.write_text("# first\nsocks5://one:1000\n\nhttp://two:2000\n", encoding="utf-8")
            document = {"profiles": {"rotating_single_ip": {"mode": "rotating_single_ip", "pool_file": "config/proxy_pool.txt", "switch_after_consecutive_account_failures": 5}}}
            pool = load_proxy_pool(base_dir, document)
            self.assertEqual(pool["pool_id"], "default")
            self.assertEqual(pool["addresses"], ["socks5://one:1000", "http://two:2000"])
    def test_load_config_merges_independent_proxy_profile(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(
                base_dir,
                proxy={
                    "enabled": True,
                    "proxy_profile": "dynamic",
                    "bootstrap": {"host": "82.22.69.12", "port": 7219},
                },
            )
            (base_dir / "config" / "proxy.json").write_text(
                json.dumps({
                    "profiles": {
                        "dynamic": {
                            "mode": "dynamic",
                            "api_url": "https://proxy.example/generate",
                            "bootstrap_ref": "automation.proxy.bootstrap",
                        },
                    },
                    "gost": {"binary": "gost", "listen_port": 0},
                }),
                encoding="utf-8",
            )

            config = load_config(base_dir)

            self.assertEqual(config["proxy"]["mode"], "dynamic")
            self.assertEqual(config["proxy"]["api_url"], "https://proxy.example/generate")
            self.assertEqual(config["proxy"]["bootstrap"]["host"], "82.22.69.12")

    def test_load_config_rejects_dynamic_profile_without_automation_bootstrap(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, proxy={"enabled": True, "proxy_profile": "dynamic"})
            (base_dir / "config" / "proxy.json").write_text(
                json.dumps({"profiles": {"dynamic": {"mode": "dynamic", "bootstrap_ref": "automation.proxy.bootstrap"}}}),
                encoding="utf-8",
            )

            with self.assertRaisesRegex(ValueError, "白名单出口"):
                load_config(base_dir)

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
            self.assertEqual(config["register"]["start_url"], "https://accounts.binance.com/zh-CN/register")
            self.assertEqual(config["register"]["warmup_url"], "https://www.binance.com/zh-CN")
            self.assertEqual(config["fingerprint"]["mode"], "native")

    def test_load_config_rejects_invalid_fingerprint_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, fingerprint={"mode": "random"})

            with self.assertRaisesRegex(ValueError, "fingerprint.mode"):
                load_config(base_dir)

    def test_load_config_allows_empty_register_warmup_url(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            _write_config(base_dir, register={"warmup_url": "  ", "submit_error_ack_max_attempts": 3})

            config = load_config(base_dir)

            self.assertEqual(config["register"]["warmup_url"], "")

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
