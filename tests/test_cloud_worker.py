import unittest
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from binance_cloud.windows import worker


class CloudWorkerConfigTests(unittest.TestCase):
    @patch("binance_cloud.windows.worker.load_config", return_value={"proxy": {"enabled": True}, "mode": "login"})
    def test_task_mode_is_not_overwritten_by_proxy_mode(self, _load):
        worker.CONFIG = None
        config = worker._config_for_task({"mode": "direct"}, "register")
        self.assertEqual(config["mode"], "register")
        self.assertFalse(config["proxy"]["enabled"])

    @patch("binance_cloud.windows.worker.load_config", return_value={"proxy": {}, "mode": "login"})
    def test_unknown_task_mode_fails_fast(self, _load):
        worker.CONFIG = None
        with self.assertRaises(ValueError):
            worker._config_for_task({"mode": "direct"}, "unknown")

    def test_dynamic_task_requires_local_dynamic_profile(self):
        with patch("binance_cloud.windows.worker.load_config", return_value={"proxy": {"enabled": True, "mode": "static"}, "mode": "login"}):
            worker.CONFIG = None
            with self.assertRaisesRegex(ValueError, "dynamic profile"):
                worker._config_for_task({"mode": "dynamic"}, "login")

    def test_fixed_cloud_lease_strips_local_pool_fields(self):
        profile = {
            "enabled": True,
            "mode": "rotating_single_ip",
            "pool_file": "config/proxy_pool.txt",
            "allow_parallel": True,
            "cooldown_seconds": 3600,
            "switch_after_account_failures": 3,
            "gost": {"binary": "gost"},
        }
        with patch("binance_cloud.windows.worker.load_config", return_value={"proxy": profile, "mode": "login"}):
            worker.CONFIG = None
            config = worker._config_for_task(
                {"mode": "fixed", "address": "socks5://user:pass@127.0.0.1:1080"},
                "register",
            )
        self.assertEqual(config["proxy"]["mode"], "static")
        self.assertEqual(config["proxy"]["static"]["host"], "127.0.0.1")
        for key in ("pool_file", "allow_parallel", "cooldown_seconds", "switch_after_account_failures"):
            self.assertNotIn(key, config["proxy"])

    def test_lease_metadata_requires_entry_and_profile(self):
        with self.assertRaisesRegex(ValueError, "proxy_entry_id"):
            worker._validate_lease_metadata(
                {"proxy": {"mode": "direct"}},
                {"lease_id": "lease-1"},
                {"mode": "direct"},
            )
        metadata = worker._validate_lease_metadata(
            {"proxy": {"mode": "direct"}, "proxy_profile": "direct"},
            {"lease_id": "lease-1", "proxy_entry_id": "direct"},
            {"mode": "direct"},
        )
        self.assertEqual(metadata["lease_id"], "lease-1")

    def test_concurrency_update_persists_and_validates(self):
        with TemporaryDirectory() as temp:
            root = Path(temp)
            config_dir = root / "config"
            config_dir.mkdir()
            (config_dir / "worker.json").write_text(json.dumps({"protocol_version": "1", "worker_id": "w", "callback_url": "", "worker_max_workers": 1}), encoding="utf-8")
            with patch.object(worker, "BASE_DIR", root):
                result = worker._set_worker_concurrency(2)
            self.assertEqual(result["previous_worker_max_workers"], 1)
            self.assertEqual(json.loads((config_dir / "worker.json").read_text())["worker_max_workers"], 2)
            with self.assertRaises(ValueError):
                worker._set_worker_concurrency(0)

    def test_concurrency_payload_requires_worker_id(self):
        with self.assertRaises(Exception):
            worker.ConcurrencyUpdate(worker_max_workers=2)


if __name__ == "__main__":
    unittest.main()
