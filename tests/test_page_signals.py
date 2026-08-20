from __future__ import annotations

import unittest

from binance_analyzer.page_signals import (
    UrlState,
    assess_risk_text,
    detect_login_url_state,
    detect_register_url_state,
    is_logged_in_url,
)


class PageSignalTests(unittest.TestCase):
    def test_detect_login_url_state(self) -> None:
        self.assertIs(
            detect_login_url_state("https://accounts.binance.com/zh-CN/login/password"),
            UrlState.LOGIN_PASSWORD,
        )
        self.assertIs(
            detect_login_url_state("https://accounts.binance.com/zh-CN/login/mfa"),
            UrlState.LOGIN_MFA,
        )
        self.assertIs(
            detect_login_url_state("https://www.binance.com/zh-CN/my/dashboard"),
            UrlState.DASHBOARD,
        )

    def test_detect_register_url_state(self) -> None:
        self.assertIs(
            detect_register_url_state("https://accounts.binance.com/zh-CN/register/verification"),
            UrlState.REGISTER_VERIFICATION,
        )
        self.assertIs(
            detect_register_url_state("https://accounts.binance.com/zh-CN/register/register-set-password"),
            UrlState.REGISTER_PASSWORD,
        )

    def test_assess_risk_text_classifies_known_signals(self) -> None:
        fatal = assess_risk_text("403 ERROR The request could not be satisfied")
        self.assertTrue(fatal.has_risk)
        self.assertTrue(fatal.is_fatal)

        proxy = assess_risk_text("ERR_PROXY_CONNECTION_FAILED")
        self.assertTrue(proxy.has_risk)
        self.assertTrue(proxy.is_proxy_failure)

        retriable = assess_risk_text("错误码 300010")
        self.assertTrue(retriable.has_risk)
        self.assertTrue(retriable.is_retriable)

    def test_is_logged_in_url_requires_binance_domain(self) -> None:
        self.assertTrue(is_logged_in_url("https://www.binance.com/zh-CN/my/dashboard"))
        self.assertFalse(is_logged_in_url("https://example.com/my/dashboard"))
        self.assertFalse(
            is_logged_in_url("https://accounts.binance.com/zh-CN/login?return_to=/my/dashboard")
        )


if __name__ == "__main__":
    unittest.main()
