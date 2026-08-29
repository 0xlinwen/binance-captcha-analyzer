from __future__ import annotations

import unittest

from binance_analyzer.results import AccountResult, AccountStatus, AutomationResult


class ResultModelTests(unittest.TestCase):
    def test_account_status_retry_flags_are_explicit(self) -> None:
        self.assertTrue(AccountStatus.PROXY_FAILED.should_retry_proxy)
        self.assertTrue(AccountStatus.RATE_LIMITED.should_retry_proxy)
        self.assertFalse(AccountStatus.AUTH_FAILED.should_retry_proxy)

    def test_account_result_exports_process_tuple(self) -> None:
        result = AccountResult("alice@example.com", "pass1", AccountStatus.AUTH_FAILED)

        self.assertEqual(result.to_process_tuple(), ("alice@example.com", "pass1", AccountStatus.AUTH_FAILED))

    def test_automation_result_from_failure_status_has_message(self) -> None:
        result = AutomationResult.from_status(AccountStatus.FAILED)

        self.assertEqual(result.error_code, "failed")
        self.assertEqual(result.error_message, "自动化流程失败")


if __name__ == "__main__":
    unittest.main()
