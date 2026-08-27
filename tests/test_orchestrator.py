from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from binance_analyzer.orchestrator import (
    _build_account_proxy_config,
    _is_static_proxy_mode,
    _record_used_proxy_ip,
    register_account,
)
from binance_analyzer.results import AccountStatus, AutomationResult


class OrchestratorProxyIpTests(unittest.TestCase):
    def test_is_static_proxy_mode_detects_fixed_proxy_config(self) -> None:
        self.assertTrue(_is_static_proxy_mode({"mode": "static"}))
        self.assertTrue(_is_static_proxy_mode({"mode": " STATIC "}))
        self.assertFalse(_is_static_proxy_mode({"mode": "dynamic"}))

    def test_build_account_proxy_config_uses_ephemeral_gost_port(self) -> None:
        proxy_config = {
            "enabled": True,
            "gost": {
                "binary": "gost",
                "listen_host": "127.0.0.1",
                "listen_port": 8888,
            },
        }

        runtime_proxy_config = _build_account_proxy_config(proxy_config)

        self.assertEqual(runtime_proxy_config["gost"]["listen_port"], 0)
        self.assertEqual(proxy_config["gost"]["listen_port"], 8888)

    def test_record_used_proxy_ip_writes_configured_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = {"used_ips_file": "data/runtime/used_proxy_ips.txt"}

            appended = _record_used_proxy_ip(
                base_dir,
                proxy_config,
                {"final_upstream": {"exit_ip": "8.8.8.8"}},
                worker_id=0,
            )

            self.assertTrue(appended)
            self.assertEqual(
                (base_dir / "data" / "runtime" / "used_proxy_ips.txt").read_text(encoding="utf-8"),
                "8.8.8.8\n",
            )

    def test_record_used_proxy_ip_deduplicates_existing_ip(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = {"used_ips_file": "data/runtime/used_proxy_ips.txt"}

            first = _record_used_proxy_ip(base_dir, proxy_config, {"exit_ip": "8.8.8.8"}, worker_id=0)
            second = _record_used_proxy_ip(base_dir, proxy_config, {"exit_ip": "8.8.8.8"}, worker_id=0)

            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(
                (base_dir / "data" / "runtime" / "used_proxy_ips.txt").read_text(encoding="utf-8"),
                "8.8.8.8\n",
            )

    def test_register_account_rejects_unknown_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            with self.assertRaises(ValueError):
                register_account(
                    Path(tmpdir),
                    "alice@example.com",
                    "pass1",
                    {
                        "output_file": "data/results/registered_accounts.json",
                        "mode": "unknown",
                        "proxy": {"enabled": False},
                    },
                )

    @patch("binance_analyzer.orchestrator.build_stealth_context")
    @patch("binance_analyzer.orchestrator.build_proxy_launch_config", return_value=None)
    @patch("binance_analyzer.orchestrator.create_proxy_runtime", return_value=None)
    @patch("binance_analyzer.orchestrator.generate_fingerprint")
    @patch("binance_analyzer.orchestrator.sync_playwright")
    def test_register_account_returns_proxy_failed_when_proxy_runtime_missing(
        self,
        mock_sync_playwright,
        mock_generate_fingerprint,
        mock_create_proxy_runtime,
        _mock_build_proxy_launch_config,
        mock_build_stealth_context,
    ) -> None:
        mock_sync_playwright.return_value.__enter__.return_value = Mock()
        mock_generate_fingerprint.return_value = {
            "user_agent": "Mozilla/5.0 test",
            "timezone_id": "Asia/Tokyo",
            "screen_width": 1280,
            "screen_height": 720,
            "device_pixel_ratio": 1,
            "languages": ["ja-JP", "en-US"],
        }

        result = register_account(
            Path("/tmp/project"),
            "alice@example.com",
            "pass1",
            {
                "output_file": "data/results/registered_accounts.json",
                "mode": "login",
                "proxy": {
                    "enabled": True,
                    "mode": "dynamic",
                    "used_ips_file": "data/runtime/used_proxy_ips.txt",
                    "max_attempts": 1,
                },
            },
        )

        self.assertIsInstance(result, AutomationResult)
        self.assertIs(result.status, AccountStatus.PROXY_FAILED)
        mock_create_proxy_runtime.assert_called_once()
        mock_build_stealth_context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
