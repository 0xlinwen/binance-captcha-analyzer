from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from binance_analyzer.cli import build_account_tasks, finalize_account_result, process_account
from binance_analyzer.results import AccountStatus


def _runtime_config(max_login_retries: int = 3) -> dict:
    return {
        "max_login_retries": max_login_retries,
        "proxy": {"mode": "dynamic"},
        "runtime": {
            "proxy_retry_delay_min_sec": 10,
            "proxy_retry_delay_max_sec": 30,
        },
    }


class CliTests(unittest.TestCase):
    def test_build_account_tasks_keeps_all_accounts_when_creator_api_is_limited(self) -> None:
        config = _runtime_config()
        config["creator_api"] = {"enabled": True, "max_accounts": 1}
        tasks = build_account_tasks(Path("/tmp/project"), [("a@example.com", "p1"), ("b@example.com", "p2")], config)
        self.assertEqual(len(tasks), 2)
        self.assertIs(tasks[0][2], config)
        self.assertIs(tasks[1][2], config)

    def test_build_account_tasks_assigns_unique_worker_ids(self) -> None:
        accounts = [(f"user{i}@example.com", "pass") for i in range(8)]

        tasks = build_account_tasks(Path("/tmp/project"), accounts, {"max_workers": 3})
        worker_ids = [task[3] for task in tasks]

        self.assertEqual(worker_ids, list(range(8)))
        self.assertEqual(len(worker_ids), len(set(worker_ids)))

    def test_finalize_already_registered_removes_account_from_source_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text(
                "alice@example.com:pass1\nbob@example.com:pass2\n",
                encoding="utf-8",
            )

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.ALREADY_REGISTERED,
            )

            self.assertEqual(outcome, "already_registered")
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "bob@example.com:pass2\n")
            self.assertEqual(success_path.read_text(encoding="utf-8"), "alice@example.com----pass1\n")
            self.assertFalse(failed_path.exists())

    def test_finalize_success_uses_dash_delimiter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("alice@example.com:pass1\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.SUCCESS,
            )

            self.assertEqual(outcome, "success")
            self.assertEqual(success_path.read_text(encoding="utf-8"), "alice@example.com----pass1\n")
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "")
            self.assertFalse(failed_path.exists())

    def test_finalize_failures_use_dash_delimiter(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("cannmz646@outlook.com:JNx6776697\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "cannmz646@outlook.com",
                "JNx6776697",
                AccountStatus.FAILED,
            )

            self.assertEqual(outcome, "failed")
            self.assertEqual(
                failed_path.read_text(encoding="utf-8"),
                "cannmz646@outlook.com----JNx6776697\n",
            )
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "")
            self.assertFalse(success_path.exists())

    def test_finalize_auth_failed_keeps_distinct_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("alice@example.com:pass1\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.AUTH_FAILED,
            )

            self.assertEqual(outcome, "auth_failed")
            self.assertEqual(failed_path.read_text(encoding="utf-8"), "alice@example.com----pass1\n")
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "")
            self.assertFalse(success_path.exists())

    def test_finalize_rate_limited_keeps_distinct_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("alice@example.com:pass1\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.RATE_LIMITED,
            )

            self.assertEqual(outcome, "rate_limited")
            self.assertFalse(failed_path.exists())
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "alice@example.com:pass1\n")
            self.assertFalse(success_path.exists())

    def test_finalize_proxy_failed_keeps_account_in_source_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("alice@example.com:pass1\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.PROXY_FAILED,
            )

            self.assertEqual(outcome, "proxy_failed")
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "alice@example.com:pass1\n")
            self.assertFalse(success_path.exists())
            self.assertFalse(failed_path.exists())

    def test_finalize_email_verification_required_keeps_account_in_source_queue(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            success_path = base_dir / "output" / "success_accounts.txt"
            failed_path = base_dir / "output" / "failed_accounts.txt"
            accounts_path.write_text("alice@example.com:pass1\n", encoding="utf-8")

            outcome = finalize_account_result(
                base_dir,
                "accounts.txt",
                success_path,
                failed_path,
                "alice@example.com",
                "pass1",
                AccountStatus.EMAIL_VERIFICATION_REQUIRED,
            )

            self.assertEqual(outcome, "email_verification_required")
            self.assertEqual(accounts_path.read_text(encoding="utf-8"), "alice@example.com:pass1\n")
            self.assertFalse(success_path.exists())
            self.assertFalse(failed_path.exists())

    @patch("binance_analyzer.cli.register_account")
    def test_process_account_does_not_retry_auth_failed(self, mock_register_account) -> None:
        mock_register_account.return_value = AccountStatus.AUTH_FAILED

        result = process_account(
            (
                Path("/tmp/project"),
                ("alice@example.com", "pass1"),
                _runtime_config(max_login_retries=3),
                0,
            )
        )

        self.assertEqual(result, ("alice@example.com", "pass1", AccountStatus.AUTH_FAILED))
        self.assertEqual(mock_register_account.call_count, 1)

    @patch("binance_analyzer.cli.time.sleep")
    @patch("binance_analyzer.cli.register_account")
    def test_process_account_retries_rate_limited_as_proxy_session_failure(self, mock_register_account, mock_sleep) -> None:
        mock_register_account.side_effect = [AccountStatus.RATE_LIMITED, AccountStatus.SUCCESS]

        result = process_account(
            (
                Path("/tmp/project"),
                ("alice@example.com", "pass1"),
                _runtime_config(max_login_retries=3),
                0,
            )
        )

        self.assertEqual(result, ("alice@example.com", "pass1", AccountStatus.SUCCESS))
        self.assertEqual(mock_register_account.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("binance_analyzer.cli.time.sleep")
    @patch("binance_analyzer.cli.register_account")
    def test_process_account_preserves_proxy_failed_after_retry_exhaustion(
        self,
        mock_register_account,
        mock_sleep,
    ) -> None:
        mock_register_account.return_value = AccountStatus.PROXY_FAILED

        result = process_account(
            (
                Path("/tmp/project"),
                ("alice@example.com", "pass1"),
                _runtime_config(max_login_retries=2),
                0,
            )
        )

        self.assertEqual(result, ("alice@example.com", "pass1", AccountStatus.PROXY_FAILED))
        self.assertEqual(mock_register_account.call_count, 2)
        self.assertEqual(mock_sleep.call_count, 0)

    @patch("binance_analyzer.cli.time.sleep")
    @patch("binance_analyzer.cli.register_account")
    def test_process_account_does_not_start_stagger_for_later_accounts(self, mock_register_account, mock_sleep) -> None:
        mock_register_account.return_value = AccountStatus.SUCCESS

        result = process_account(
            (
                Path("/tmp/project"),
                ("alice@example.com", "pass1"),
                _runtime_config(max_login_retries=3),
                3,
            )
        )

        self.assertEqual(result, ("alice@example.com", "pass1", AccountStatus.SUCCESS))
        mock_sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main()
