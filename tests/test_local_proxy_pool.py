from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import Mock, patch

from binance_analyzer.automation.orchestrator import register_account
from binance_analyzer.integrations.local_proxy_pool import (
    LocalFixedProxyPool,
    bind_local_rotating_proxy,
)
from binance_analyzer.results import AccountStatus, AutomationResult


def _write_pool(base_dir: Path, lines: list[str]) -> dict:
    pool_path = base_dir / "config" / "proxy_pool.txt"
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    pool_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {
        "enabled": True,
        "mode": "rotating_single_ip",
        "pool_file": "config/proxy_pool.txt",
        "cooldown_seconds": 3600,
        "allow_parallel": True,
        "switch_after_account_failures": 3,
        "gost": {"binary": "gost", "listen_host": "127.0.0.1", "listen_port": 8888},
    }


class LocalFixedProxyPoolTests(unittest.TestCase):
    def test_acquire_assigns_distinct_entries_in_file_order(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(
                base_dir,
                ["socks5://user:pass@1.1.1.1:1000", "socks5://user:pass@2.2.2.2:2000"],
            )
            pool = LocalFixedProxyPool(base_dir, proxy_config)

            first = pool.acquire()
            second = pool.acquire()
            third = pool.acquire()

            self.assertIsNotNone(first)
            self.assertIsNotNone(second)
            self.assertEqual(first.host, "1.1.1.1")
            self.assertEqual(second.host, "2.2.2.2")
            self.assertIsNone(third)
            static = first.to_static_proxy_config(proxy_config)
            self.assertEqual(static["mode"], "static")
            self.assertEqual(static["static"]["host"], "1.1.1.1")
            self.assertEqual(static["gost"]["listen_port"], 0)
            self.assertNotIn("pool_file", static)

    def test_cooling_entry_is_not_selected_even_when_it_is_the_only_one(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(base_dir, ["socks5://user:pass@1.1.1.1:1000"])
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            for _ in range(3):
                lease = pool.acquire()
                self.assertIsNotNone(lease)
                pool.release(lease.lease_id, result_status="failed")
            # 连续失败达到阈值后进入冷却；没有可用条目时不能提前复活。
            self.assertIsNone(pool.acquire())

    def test_new_pool_entries_keep_existing_cooldown(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(base_dir, ["socks5://user:pass@1.1.1.1:1000"])
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            lease = pool.acquire()
            for _ in range(3):
                pool.release(lease.lease_id, result_status="failed")
                nxt = pool.acquire()
                if nxt is None:
                    break
                lease = nxt
            (base_dir / "config" / "proxy_pool.txt").write_text(
                "socks5://user:pass@1.1.1.1:1000\nsocks5://user:pass@3.3.3.3:3000\n",
                encoding="utf-8",
            )
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            leased = pool.acquire()
            self.assertIsNotNone(leased)
            self.assertEqual(leased.host, "3.3.3.3")

    def test_success_resets_failure_count(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(base_dir, ["socks5://user:pass@1.1.1.1:1000"])
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            lease = pool.acquire()
            pool.release(lease.lease_id, result_status="failed")
            lease = pool.acquire()
            self.assertIsNotNone(lease)
            pool.release(lease.lease_id, result_status="success")
            again = pool.acquire()
            self.assertIsNotNone(again)
            self.assertEqual(again.host, "1.1.1.1")

    def test_expired_cooling_returns_to_available(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(base_dir, ["socks5://user:pass@1.1.1.1:1000"])
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            state_path = pool.state_path
            state_path.parent.mkdir(parents=True, exist_ok=True)
            past = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
            state_path.write_text(
                json.dumps(
                    {
                        "entries": {
                            "socks5://1.1.1.1:1000": {
                                "status": "cooling",
                                "consecutive_failures": 3,
                                "cooldown_until": past,
                            }
                        }
                    }
                ),
                encoding="utf-8",
            )
            lease = pool.acquire()
            self.assertIsNotNone(lease)
            self.assertEqual(lease.host, "1.1.1.1")

    def test_bind_passthrough_for_static_mode(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            bound, pool, lease_id = bind_local_rotating_proxy(
                Path(tmpdir),
                {"enabled": True, "mode": "static", "gost": {"listen_port": 9}},
            )
            self.assertEqual(bound["mode"], "static")
            self.assertEqual(bound["gost"]["listen_port"], 0)
            self.assertIsNone(pool)
            self.assertIsNone(lease_id)


class OrchestratorRotatingPoolTests(unittest.TestCase):
    @patch("binance_analyzer.automation.orchestrator.build_stealth_context")
    @patch("binance_analyzer.automation.orchestrator.create_proxy_runtime")
    @patch("binance_analyzer.automation.orchestrator.generate_fingerprint")
    @patch("binance_analyzer.automation.orchestrator.sync_playwright")
    def test_register_account_returns_proxy_failed_when_pool_empty(
        self,
        mock_sync_playwright,
        mock_generate_fingerprint,
        mock_create_proxy_runtime,
        mock_build_stealth_context,
    ) -> None:
        mock_sync_playwright.return_value.__enter__.return_value = Mock()
        mock_generate_fingerprint.return_value = {
            "mode": "native",
            "user_agent": "",
            "timezone_id": "",
            "screen_width": 0,
            "screen_height": 0,
            "device_pixel_ratio": 0,
            "languages": [],
        }
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            proxy_config = _write_pool(base_dir, ["socks5://user:pass@1.1.1.1:1000"])
            pool = LocalFixedProxyPool(base_dir, proxy_config)
            first = pool.acquire()
            self.assertIsNotNone(first)

            result = register_account(
                base_dir,
                "alice@example.com",
                "pass1",
                {
                    "output_file": "data/results/registered_accounts.json",
                    "mode": "login",
                    "proxy": proxy_config,
                },
            )

        self.assertIsInstance(result, AutomationResult)
        self.assertIs(result.status, AccountStatus.PROXY_FAILED)
        mock_create_proxy_runtime.assert_not_called()
        mock_build_stealth_context.assert_not_called()


if __name__ == "__main__":
    unittest.main()
