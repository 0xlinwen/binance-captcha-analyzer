from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from binance_analyzer.automation.orchestrator import (
    PROJECT_ROOT,
    _build_account_proxy_config,
    register_account,
)
from binance_analyzer.results import AccountStatus, AutomationResult


class OrchestratorProxyIpTests(unittest.TestCase):
    def test_project_root_points_at_repo_not_src(self) -> None:
        self.assertTrue((PROJECT_ROOT / "main.py").is_file())
        self.assertTrue((PROJECT_ROOT / "config").is_dir())
        self.assertNotEqual(PROJECT_ROOT.name, "src")

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

    @patch("binance_analyzer.automation.orchestrator.build_stealth_context")
    @patch("binance_analyzer.automation.orchestrator.build_proxy_launch_config", return_value=None)
    @patch("binance_analyzer.automation.orchestrator.create_proxy_runtime", return_value=None)
    @patch("binance_analyzer.automation.orchestrator.generate_fingerprint")
    @patch("binance_analyzer.automation.orchestrator.sync_playwright")
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

    @patch("binance_analyzer.automation.orchestrator.stop_managed_proxy_runtime")
    @patch("binance_analyzer.automation.orchestrator.build_stealth_context", side_effect=RuntimeError("Chrome 启动失败"))
    @patch("binance_analyzer.automation.orchestrator.build_proxy_launch_config", return_value={"server": "http://127.0.0.1:8888"})
    @patch("binance_analyzer.automation.orchestrator.create_proxy_runtime", return_value={"managed": True})
    @patch("binance_analyzer.automation.orchestrator.bind_local_rotating_proxy", return_value=({"mode": "static"}, None, None))
    @patch("binance_analyzer.automation.orchestrator.generate_fingerprint")
    @patch("binance_analyzer.automation.orchestrator.sync_playwright")
    def test_register_account_stops_proxy_when_browser_startup_fails(
        self,
        mock_sync_playwright,
        mock_generate_fingerprint,
        _mock_bind_local_rotating_proxy,
        _mock_create_proxy_runtime,
        _mock_build_proxy_launch_config,
        _mock_build_stealth_context,
        mock_stop_managed_proxy_runtime,
    ) -> None:
        mock_sync_playwright.return_value.__enter__.return_value = Mock()
        mock_generate_fingerprint.return_value = {
            "mode": "native",
            "user_agent": "Mozilla/5.0 test",
            "timezone_id": "Asia/Tokyo",
            "screen_width": 1280,
            "screen_height": 720,
            "device_pixel_ratio": 1,
            "languages": ["ja-JP", "en-US"],
        }

        with self.assertRaisesRegex(RuntimeError, "Chrome 启动失败"):
            register_account(
                Path("/tmp/project"),
                "alice@example.com",
                "pass1",
                {
                    "output_file": "data/results/registered_accounts.json",
                    "mode": "login",
                    "proxy": {"enabled": True, "mode": "static", "max_attempts": 1},
                },
            )

        mock_stop_managed_proxy_runtime.assert_called_once_with({"managed": True})


if __name__ == "__main__":
    unittest.main()
