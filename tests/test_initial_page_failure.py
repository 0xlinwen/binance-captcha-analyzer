from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from binance_analyzer import login_flow, register_flow
from binance_analyzer.results import AccountStatus


def _config() -> dict:
    return {
        "openrouter_api_key": "sk-test",
        "models": ["model-a"],
        "imap_host": "imap.example.com",
        "imap_port": 993,
        "login": {"start_url": "https://accounts.binance.com/zh-CN/login"},
        "captcha": {
            "retry_mode": "fast",
            "max_attempts_per_round": 1,
            "max_rounds": 1,
            "cooldown_on_risk_min_sec": 10,
            "cooldown_on_risk_max_sec": 30,
            "click_retry_per_cell": 1,
        },
        "mfa": {"submit_retry": 2, "not_registered_keywords": ["未注册"]},
    }


class InitialPageFailureTests(unittest.TestCase):
    @patch("binance_analyzer.login_flow.save_failure_log")
    @patch("binance_analyzer.login_flow.log_summary")
    @patch("binance_analyzer.login_flow.goto_with_retry", return_value=False)
    @patch("binance_analyzer.login_flow.get_initial_mail_count", return_value=0)
    @patch("binance_analyzer.login_flow.console_log")
    @patch("binance_analyzer.login_flow.setup_logger")
    def test_login_initial_page_load_failure_is_proxy_failed(
        self,
        mock_setup_logger,
        _mock_console_log,
        _mock_get_initial_mail_count,
        _mock_goto_with_retry,
        _mock_log_summary,
        _mock_save_failure_log,
    ) -> None:
        mock_setup_logger.return_value = Mock()

        status = login_flow.login_with_url_state(Mock(), "alice@example.com", "pass", _config())

        self.assertIs(status, AccountStatus.PROXY_FAILED)

    @patch("binance_analyzer.register_flow.save_failure_log")
    @patch("binance_analyzer.register_flow.goto_with_retry", return_value=False)
    @patch("binance_analyzer.register_flow.console_log")
    @patch("binance_analyzer.register_flow.setup_logger")
    def test_register_initial_page_load_failure_is_proxy_failed(
        self,
        mock_setup_logger,
        _mock_console_log,
        _mock_goto_with_retry,
        _mock_save_failure_log,
    ) -> None:
        mock_setup_logger.return_value = Mock()
        page = Mock()

        status = register_flow.register_with_url_state(page, "alice@example.com", "pass", _config())

        self.assertIs(status, AccountStatus.PROXY_FAILED)

    @patch("binance_analyzer.login_flow.save_failure_log")
    @patch("binance_analyzer.login_flow._continue_login_after_auth_failure", return_value=False)
    @patch("binance_analyzer.login_flow._get_body_text", return_value="认证失败，请刷新页面后重试。(208075-838bc7b0)")
    @patch("binance_analyzer.login_flow._wait_for_page_response", return_value=("timeout", "https://accounts.binance.com/zh-CN/login"))
    @patch("binance_analyzer.login_flow.click_login_continue_strict", return_value=True)
    @patch("binance_analyzer.login_flow.input_email", return_value=True)
    @patch("binance_analyzer.login_flow._has_risk_error", return_value=(False, ""))
    @patch("binance_analyzer.login_flow._is_page_blank", return_value=False)
    @patch("binance_analyzer.login_flow._wait_for_url_change", return_value=(False, "https://accounts.binance.com/zh-CN/login"))
    @patch("binance_analyzer.login_flow._CAPTCHA_SERVICE")
    @patch("binance_analyzer.login_flow.goto_with_retry", return_value=True)
    @patch("binance_analyzer.login_flow.get_initial_mail_count", return_value=0)
    @patch("binance_analyzer.login_flow.console_log")
    @patch("binance_analyzer.login_flow.setup_logger")
    def test_login_auth_failure_after_email_submit_is_auth_failed(
        self,
        mock_setup_logger,
        _mock_console_log,
        _mock_get_initial_mail_count,
        _mock_goto_with_retry,
        mock_captcha_service,
        _mock_wait_for_url_change,
        _mock_is_page_blank,
        _mock_has_risk_error,
        _mock_input_email,
        _mock_click_login_continue,
        _mock_wait_for_page_response,
        _mock_get_body_text,
        mock_continue_auth_failure,
        _mock_save_failure_log,
    ) -> None:
        mock_setup_logger.return_value = Mock()
        mock_captcha_service.solve_if_present.return_value = login_flow.CaptchaSolveStatus.PASSED
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/login"
        page.query_selector.return_value = None

        status = login_flow.login_with_url_state(page, "alice@example.com", "pass", _config())

        self.assertIs(status, AccountStatus.AUTH_FAILED)
        mock_continue_auth_failure.assert_called_once()

    @patch("binance_analyzer.register_flow.handle_email_verification")
    @patch("binance_analyzer.register_flow._has_risk_error", return_value=(False, ""))
    @patch("binance_analyzer.register_flow._is_page_blank", return_value=False)
    @patch("binance_analyzer.register_flow.goto_with_retry", return_value=True)
    @patch("binance_analyzer.register_flow.console_log")
    @patch("binance_analyzer.register_flow.setup_logger")
    def test_register_stops_at_verification_when_email_fetch_disabled(
        self,
        mock_setup_logger,
        _mock_console_log,
        _mock_goto_with_retry,
        _mock_is_page_blank,
        _mock_has_risk_error,
        mock_handle_email_verification,
    ) -> None:
        mock_setup_logger.return_value = Mock()
        config = _config()
        config["mfa"]["email_verification_enabled"] = False
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/register/verification"
        page.inner_text.return_value = ""
        page.query_selector.return_value = None

        status = register_flow.register_with_url_state(page, "alice@example.com", "", config)

        self.assertIs(status, AccountStatus.EMAIL_VERIFICATION_REQUIRED)
        mock_handle_email_verification.assert_not_called()

    @patch("binance_analyzer.register_flow.handle_email_verification")
    @patch("binance_analyzer.register_flow._CAPTCHA_SERVICE")
    @patch("binance_analyzer.register_flow._wait_for_page_response")
    @patch("binance_analyzer.register_flow.click_register_continue_strict", return_value=True)
    @patch("binance_analyzer.register_flow._tick_agreement_checkbox", return_value=True)
    @patch("binance_analyzer.register_flow.input_email", return_value=True)
    @patch("binance_analyzer.register_flow._has_risk_error", return_value=(False, ""))
    @patch("binance_analyzer.register_flow._is_page_blank", return_value=False)
    @patch("binance_analyzer.register_flow.goto_with_retry", return_value=True)
    @patch("binance_analyzer.register_flow.console_log")
    @patch("binance_analyzer.register_flow.setup_logger")
    def test_register_url_changed_to_verification_without_captcha_stops_when_email_fetch_disabled(
        self,
        mock_setup_logger,
        _mock_console_log,
        _mock_goto_with_retry,
        _mock_is_page_blank,
        _mock_has_risk_error,
        _mock_input_email,
        _mock_tick_agreement,
        _mock_click_continue,
        mock_wait_for_page_response,
        mock_captcha_service,
        mock_handle_email_verification,
    ) -> None:
        mock_setup_logger.return_value = Mock()
        config = _config()
        config["mfa"]["email_verification_enabled"] = False
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/register"
        page.inner_text.return_value = ""
        page.query_selector.return_value = None

        def move_to_verification(*_args, **_kwargs):
            page.url = "https://accounts.binance.com/zh-CN/register/verification-new-register?accountType=email"
            return "url_changed", page.url

        mock_wait_for_page_response.side_effect = move_to_verification

        status = register_flow.register_with_url_state(page, "alice@example.com", "", config)

        self.assertIs(status, AccountStatus.EMAIL_VERIFICATION_REQUIRED)
        mock_captcha_service.solve_if_present.assert_not_called()
        mock_handle_email_verification.assert_not_called()

    @patch("binance_analyzer.register_flow._wait_for_page_response", return_value=("timeout", "https://accounts.binance.com/zh-CN/register"))
    @patch("binance_analyzer.register_flow.click_register_continue_strict", return_value=True)
    @patch("binance_analyzer.register_flow._dismiss_error_popup", return_value=True)
    def test_register_submit_ack_error_retries_three_times_before_fail(
        self,
        mock_dismiss,
        mock_click_continue,
        _mock_wait_for_page_response,
    ) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/register"
        body = Mock()
        body.inner_text.return_value = "我们无法处理您的请求，请稍后重试。 (600010)"
        page.query_selector.return_value = body

        recovered = register_flow._retry_register_submit_ack_error(page, "alice@example.com", Mock(), 3)

        self.assertFalse(recovered)
        self.assertEqual(mock_dismiss.call_count, 3)
        self.assertEqual(mock_click_continue.call_count, 3)

    @patch("binance_analyzer.register_flow._wait_for_page_response", return_value=("url_changed", "https://accounts.binance.com/zh-CN/register/verification-new-register?accountType=email"))
    @patch("binance_analyzer.register_flow.click_register_continue_strict", return_value=True)
    @patch("binance_analyzer.register_flow._dismiss_error_popup", return_value=True)
    def test_register_submit_ack_error_recovers_on_url_change(
        self,
        mock_dismiss,
        mock_click_continue,
        _mock_wait_for_page_response,
    ) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/register"
        body = Mock()
        body.inner_text.return_value = "无法处理您的请求 (600010)"
        page.query_selector.return_value = body

        recovered = register_flow._retry_register_submit_ack_error(page, "alice@example.com", Mock(), 3)

        self.assertTrue(recovered)
        self.assertEqual(mock_dismiss.call_count, 1)
        self.assertEqual(mock_click_continue.call_count, 1)

    def test_register_submit_ack_error_includes_auth_failure_code(self) -> None:
        self.assertTrue(
            register_flow._has_register_submit_ack_error(
                "认证失败，请刷新页面后重试。(208075-6946efee)"
            )
        )
        self.assertTrue(
            register_flow._has_register_submit_ack_error(
                "我们无法处理您的请求，请稍后重试。 (600010)"
            )
        )

    @patch("binance_analyzer.register_flow._wait_for_page_response", return_value=("timeout", "https://accounts.binance.com/zh-CN/register"))
    @patch("binance_analyzer.register_flow.click_register_continue_strict", return_value=True)
    @patch("binance_analyzer.register_flow._dismiss_error_popup", return_value=True)
    def test_register_submit_visible_ack_button_retries_without_error_text(
        self,
        mock_dismiss,
        mock_click_continue,
        _mock_wait_for_page_response,
    ) -> None:
        page = Mock()
        page.url = "https://accounts.binance.com/zh-CN/register"
        body = Mock()
        body.inner_text.return_value = ""
        ack_button = Mock()
        ack_button.is_visible.return_value = True
        page.query_selector.side_effect = lambda selector: body if selector == "body" else ack_button

        recovered = register_flow._retry_register_submit_ack_error(page, "alice@example.com", Mock(), 3)

        self.assertFalse(recovered)
        self.assertEqual(mock_dismiss.call_count, 3)
        self.assertEqual(mock_click_continue.call_count, 3)


if __name__ == "__main__":
    unittest.main()
