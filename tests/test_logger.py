from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from binance_analyzer.runtime.logger import LoggerManager
from binance_analyzer.results import AccountStatus


class LoggerManagerTests(unittest.TestCase):
    def test_record_result_counts_account_status_success(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LoggerManager(base_dir=Path(tmpdir) / "logs")

            with patch("builtins.print"):
                manager.record_result("alice@example.com", AccountStatus.SUCCESS, mode="login", worker_id=0)

            self.assertEqual(manager._stats["success"], 1)
            self.assertEqual(manager._stats["failure"], 0)

    def test_record_result_counts_proxy_failed_outside_account_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LoggerManager(base_dir=Path(tmpdir) / "logs")

            with patch("builtins.print"):
                manager.record_result("alice@example.com", AccountStatus.PROXY_FAILED, mode="login", worker_id=0)

            self.assertEqual(manager._stats["proxy_failed"], 1)
            self.assertEqual(manager._stats["failure"], 0)

    def test_record_result_counts_rate_limited_outside_account_failure(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            manager = LoggerManager(base_dir=Path(tmpdir) / "logs")

            with patch("builtins.print"):
                manager.record_result("alice@example.com", AccountStatus.RATE_LIMITED, mode="login", worker_id=0)

            self.assertEqual(manager._stats["rate_limited"], 1)
            self.assertEqual(manager._stats["failure"], 0)


if __name__ == "__main__":
    unittest.main()
