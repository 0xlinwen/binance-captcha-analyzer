from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

from binance_analyzer.captcha.types import CaptchaSolveStatus
from binance_analyzer.flows import (
    DASHBOARD_URL,
    _ensure_dashboard_page,
    _handle_captcha_result,
    _has_auth_failure_error,
    _has_proxy_failure_error,
    _continue_login_after_auth_failure,
    _is_logged_in_url,
    _retry_auth_failure_continue,
    _tick_agreement_checkbox,
    _wait_for_page_response,
)
from binance_analyzer.results import AccountStatus


class FlowUrlTests(unittest.TestCase):
    def test_logged_in_url_requires_account_area(self) -> None:
        self.assertTrue(_is_logged_in_url("https://www.binance.com/zh-CN/my/dashboard"))
        self.assertTrue(_is_logged_in_url("https://www.binance.com/zh-CN/my/settings/kyc"))
        self.assertTrue(_is_logged_in_url("https://www.binance.com/zh-CN/identity"))
        self.assertFalse(_is_logged_in_url("https://www.binance.com/zh-CN/support"))
        self.assertFalse(_is_logged_in_url("https://accounts.binance.com/zh-CN/login/password"))

    def test_ensure_dashboard_page_redirects_from_identity_page(self) -> None:
        page = Mock()
        page.url = "https://www.binance.com/zh-CN/my/settings/kyc"

        def goto_dashboard(url, **_kwargs):
            page.url = url

        page.goto.side_effect = goto_dashboard

        self.assertTrue(_ensure_dashboard_page(page, logger=Mock(), page_timeout=1000))
        page.goto.assert_called_once_with(DASHBOARD_URL, wait_until="domcontentloaded", timeout=1000)

    def test_auth_failure_page_is_not_proxy_failure(self) -> None:
        self.assertTrue(_has_auth_failure_error("认证失败，请刷新页面后重试"))
        self.assertFalse(_has_proxy_failure_error("认证失败，请刷新页面后重试"))
        self.assertFalse(_has_proxy_failure_error("IMAP 认证失败，邮箱未开启 IMAP 或密码错误"))

    def test_proxy_failure_signatures_are_still_proxy_failures(self) -> None:
        self.assertTrue(_has_proxy_failure_error("407 Proxy Authentication Required"))

    def test_captcha_auth_failure_stops_without_proxy_retry(self) -> None:
        should_stop, fail_count, reason = _handle_captcha_result(
            CaptchaSolveStatus.AUTH_FAILED,
            0,
            "alice@example.com",
            Mock(),
        )

        self.assertTrue(should_stop)
        self.assertEqual(fail_count, 0)
        self.assertIs(reason, AccountStatus.AUTH_FAILED)

    @patch("binance_analyzer.flows._wait_for_page_response", return_value=("timeout", "https://accounts.binance.com/zh-CN/login"))
    @patch("binance_analyzer.flows.click_button", return_value=True)
    def test_auth_failure_continue_retries_three_times_before_fail(self, mock_click_button, _mock_wait) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        body = Mock()
        body.inner_text.return_value = "认证失败，请刷新页面后重试"
        page.query_selector.return_value = body

        recovered = _retry_auth_failure_continue(page, "alice@example.com", Mock())

        self.assertFalse(recovered)
        self.assertEqual(mock_click_button.call_count, 3)

    @patch("binance_analyzer.flows._wait_for_page_response", return_value=("url_changed", "https://accounts.binance.com/zh-CN/login/password"))
    @patch("binance_analyzer.flows.click_button", return_value=True)
    def test_auth_failure_continue_recovers_when_next_step_loads(self, mock_click_button, _mock_wait) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        body = Mock()
        body.inner_text.return_value = "认证失败，请刷新页面后重试"
        page.query_selector.return_value = body

        recovered = _retry_auth_failure_continue(page, "alice@example.com", Mock())

        self.assertTrue(recovered)
        self.assertEqual(mock_click_button.call_count, 1)

    @patch("binance_analyzer.flows._wait_for_page_response", return_value=("timeout", "https://accounts.binance.com/zh-CN/login"))
    @patch("binance_analyzer.flows.click_login_continue_strict", return_value=True)
    @patch("binance_analyzer.flows._dismiss_error_popup", return_value=True)
    def test_login_auth_failure_retries_submit_three_times_before_fail(
        self,
        mock_dismiss,
        mock_click_continue,
        _mock_wait,
    ) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        body = Mock()
        body.inner_text.return_value = "认证失败，请刷新页面后重试。(208075-838bc7b0)"
        page.query_selector.return_value = body

        recovered = _continue_login_after_auth_failure(page, "alice@example.com", Mock())

        self.assertFalse(recovered)
        self.assertEqual(mock_dismiss.call_count, 3)
        self.assertEqual(mock_click_continue.call_count, 3)

    @patch("binance_analyzer.flows._wait_for_page_response", return_value=("url_changed", "https://accounts.binance.com/zh-CN/login/password"))
    @patch("binance_analyzer.flows.click_login_continue_strict", return_value=True)
    @patch("binance_analyzer.flows._dismiss_error_popup", return_value=True)
    def test_login_auth_failure_recovers_when_password_page_loads(
        self,
        mock_dismiss,
        mock_click_continue,
        _mock_wait,
    ) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        body = Mock()
        body.inner_text.return_value = "认证失败，请刷新页面后重试"
        page.query_selector.return_value = body

        recovered = _continue_login_after_auth_failure(page, "alice@example.com", Mock())

        self.assertTrue(recovered)
        self.assertEqual(mock_dismiss.call_count, 1)
        self.assertEqual(mock_click_continue.call_count, 1)

    def test_page_response_does_not_treat_invisible_recaptcha_badge_as_captcha(self) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        page.query_selector.return_value = None

        response_type, url = _wait_for_page_response(
            page,
            "https://accounts.binance.com/zh-CN/login",
            timeout_ms=200,
            logger=Mock(),
        )

        queried_selectors = [call.args[0] for call in page.query_selector.call_args_list]
        self.assertEqual(response_type, "timeout")
        self.assertEqual(url, "https://accounts.binance.com/zh-CN/login")
        self.assertNotIn("[class*='captcha']", queried_selectors)
        self.assertNotIn("iframe[src*='captcha']", queried_selectors)

    def test_agreement_checkbox_already_checked_does_not_click_again(self) -> None:
        page = Mock()
        page.evaluate.return_value = "already_checked"

        self.assertTrue(_tick_agreement_checkbox(page, "alice@example.com", Mock()))
        page.query_selector_all.assert_not_called()


if __name__ == "__main__":
    unittest.main()
