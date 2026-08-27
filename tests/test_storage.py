from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from binance_analyzer.storage.account_storage import (
    append_account_result,
    load_accounts,
    remove_account_from_file,
)
from binance_analyzer.storage.proxy_ip_storage import append_used_proxy_ip, load_used_proxy_ips
from binance_analyzer.storage.registered_account_storage import save_registered_account


class StorageTests(unittest.TestCase):
    def test_load_accounts_supports_multiple_delimiters(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            accounts_path.write_text(
                "alice@example.com:pass1\nbob@example.com----pass2\ninvalid-line\n",
                encoding="utf-8",
            )

            accounts = load_accounts(base_dir, "accounts.txt")

            self.assertEqual(
                accounts,
                [
                    ("alice@example.com", "pass1"),
                    ("bob@example.com", "pass2"),
                ],
            )

    def test_remove_account_from_file_updates_accounts_immediately(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            accounts_path = base_dir / "accounts.txt"
            accounts_path.write_text(
                "alice@example.com:pass1\nbob@example.com----pass2\n",
                encoding="utf-8",
            )

            removed = remove_account_from_file(base_dir, "accounts.txt", "alice@example.com", "pass1")

            self.assertTrue(removed)
            self.assertEqual(
                accounts_path.read_text(encoding="utf-8"),
                "bob@example.com----pass2\n",
            )

    def test_append_account_result_deduplicates_by_email(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            result_path = Path(tmpdir) / "output" / "success_accounts.txt"

            first_append = append_account_result(result_path, "alice@example.com", "pass1")
            second_append = append_account_result(result_path, "alice@example.com", "pass2")
            third_append = append_account_result(result_path, "bob@example.com", "pass3")

            self.assertTrue(first_append)
            self.assertFalse(second_append)
            self.assertTrue(third_append)
            self.assertEqual(
                result_path.read_text(encoding="utf-8"),
                "alice@example.com:pass1\nbob@example.com:pass3\n",
            )

    def test_used_proxy_ip_storage_deduplicates_and_loads(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            first_append = append_used_proxy_ip(base_dir, "data/runtime/used_proxy_ips.txt", "1.1.1.1")
            second_append = append_used_proxy_ip(base_dir, "data/runtime/used_proxy_ips.txt", "1.1.1.1")
            third_append = append_used_proxy_ip(base_dir, "data/runtime/used_proxy_ips.txt", "2.2.2.2")

            self.assertTrue(first_append)
            self.assertFalse(second_append)
            self.assertTrue(third_append)
            self.assertEqual(
                load_used_proxy_ips(base_dir, "data/runtime/used_proxy_ips.txt"),
                {"1.1.1.1", "2.2.2.2"},
            )

    def test_save_registered_account_matches_email_password_identity_and_updates_password(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            output_path = base_dir / "data" / "results" / "registered_accounts.json"
            output_path.parent.mkdir(parents=True)
            output_path.write_text(
                """
{
  "accounts": [
    {
      "email": "alice@example.com----oldpass",
      "password": "oldpass",
      "cookie": "oldcookie",
      "csrftoken": "oldtoken",
      "display_name": "keep-me"
    }
  ]
}
""".strip(),
                encoding="utf-8",
            )

            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com",
                    "password": "newpass",
                    "cookie": "newcookie",
                    "csrftoken": "newtoken",
                    "credential_exported_at": "2026-08-27T00:00:00+00:00",
                    "enabled": True,
                },
            )

            data = __import__("json").loads(output_path.read_text(encoding="utf-8"))
            self.assertEqual(len(data["accounts"]), 1)
            self.assertEqual(data["accounts"][0]["email"], "alice@example.com")
            self.assertEqual(data["accounts"][0]["password"], "newpass")
            self.assertEqual(data["accounts"][0]["cookie"], "newcookie")
            self.assertEqual(data["accounts"][0]["credential_exported_at"], "2026-08-27T00:00:00+00:00")
            self.assertEqual(data["accounts"][0]["display_name"], "keep-me")

    def test_save_registered_account_updates_display_name_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com----pass1",
                    "password": "pass1",
                    "cookie": "cookie",
                    "csrftoken": "token",
                    "display_name": "",
                },
            )
            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com----pass1",
                    "api_key": "abcdEFGH1234567890key",
                    "display_name": "Alan Searchfield diwl",
                },
            )

            data = __import__("json").loads(
                (base_dir / "data" / "results" / "registered_accounts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["accounts"][0]["display_name"], "Alan Searchfield diwl")
            self.assertEqual(data["accounts"][0]["api_key"], "abcdEFGH1234567890key")

    def test_save_registered_account_updates_username_when_provided(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com----pass1",
                    "password": "pass1",
                    "cookie": "cookie",
                    "csrftoken": "token",
                },
            )
            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com----pass1",
                    "username": "Square-Creator-8f524cbdf4d47",
                    "display_name": "Alan Searchfield diwl",
                },
            )

            data = __import__("json").loads(
                (base_dir / "data" / "results" / "registered_accounts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["accounts"][0]["username"], "Square-Creator-8f524cbdf4d47")
            self.assertEqual(data["accounts"][0]["display_name"], "Alan Searchfield diwl")

    def test_save_registered_account_preserves_email_password_output_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)

            save_registered_account(
                base_dir,
                "data/results/registered_accounts.json",
                {
                    "email": "alice@example.com----pass1",
                    "cookie": "cookie",
                    "csrftoken": "token",
                },
            )

            data = __import__("json").loads(
                (base_dir / "data" / "results" / "registered_accounts.json").read_text(encoding="utf-8")
            )
            self.assertEqual(data["accounts"][0]["email"], "alice@example.com----pass1")

    def test_save_registered_account_fails_after_backing_up_invalid_json(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            base_dir = Path(tmpdir)
            output_path = base_dir / "data" / "results" / "registered_accounts.json"
            output_path.parent.mkdir(parents=True)
            output_path.write_text("not json", encoding="utf-8")

            with self.assertRaisesRegex(RuntimeError, "凭证文件无效"):
                save_registered_account(
                    base_dir,
                    "data/results/registered_accounts.json",
                    {"email": "alice@example.com", "password": "pass1"},
                )

            self.assertEqual(output_path.read_text(encoding="utf-8"), "not json")
            self.assertEqual(len(list(output_path.parent.glob("registered_accounts.json.corrupt.*"))), 1)


if __name__ == "__main__":
    unittest.main()
