from __future__ import annotations

import unittest

from binance_analyzer.results import AccountResult, AccountStatus


class ResultModelTests(unittest.TestCase):
    def test_account_status_retry_flags_are_explicit(self) -> None:
        self.assertTrue(AccountStatus.PROXY_FAILED.should_retry_proxy)
        self.assertTrue(AccountStatus.RATE_LIMITED.should_retry_proxy)
        self.assertFalse(AccountStatus.AUTH_FAILED.should_retry_proxy)

    def test_account_result_exports_process_tuple(self) -> None:
        result = AccountResult("alice@example.com", "pass1", AccountStatus.AUTH_FAILED)

        self.assertEqual(result.to_process_tuple(), ("alice@example.com", "pass1", AccountStatus.AUTH_FAILED))


if __name__ == "__main__":
    unittest.main()
