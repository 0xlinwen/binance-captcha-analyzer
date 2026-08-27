from __future__ import annotations

import unittest
from unittest.mock import patch

from binance_analyzer.automation.web_actions import click_register_continue_strict, input_email, need_register


class _FakePage:
    def __init__(self, text: str):
        self.text = text

    def query_selector(self, selector: str):
        return object() if selector == "body" else None

    def inner_text(self, selector: str):
        return self.text


class NeedRegisterTests(unittest.TestCase):
    def test_does_not_match_generic_not_found_text(self) -> None:
        self.assertFalse(need_register(_FakePage("页面资源 not found，请稍后重试")))
        self.assertFalse(need_register(_FakePage("没有找到对应页面")))

    def test_matches_account_not_registered_text(self) -> None:
        self.assertTrue(need_register(_FakePage("该邮箱未注册")))
        self.assertTrue(need_register(_FakePage("account does not exist")))


class _NoEmailInputPage:
    def query_selector(self, selector: str):
        return None

    def query_selector_all(self, selector: str):
        raise AssertionError("不应使用宽泛输入框兜底")

    def wait_for_timeout(self, timeout_ms: int) -> None:
        return None


class InputEmailTests(unittest.TestCase):
    @patch("binance_analyzer.automation.web_actions.dismiss_cookie_popup", return_value=False)
    @patch("binance_analyzer.automation.web_actions.dismiss_global_modal", return_value=False)
    def test_input_email_fails_without_explicit_email_field(self, _mock_modal, _mock_cookie) -> None:
        self.assertFalse(input_email(_NoEmailInputPage(), "alice@example.com"))


class _FakeElement:
    def __init__(self, text: str = "", visible: bool = True):
        self.text = text
        self.visible = visible
        self.clicked = False

    def is_visible(self) -> bool:
        return self.visible

    def inner_text(self) -> str:
        return self.text

    def click(self, **_kwargs) -> None:
        self.clicked = True


class _RegisterButtonPage:
    def __init__(self) -> None:
        self.email_input = _FakeElement()
        self.google_button = _FakeElement("Continue with Google")
        self.submit_button = _FakeElement("继续")

    def query_selector(self, selector: str):
        if "input" in selector:
            return self.email_input
        return None

    def query_selector_all(self, _selector: str):
        return [self.google_button, self.submit_button]


class RegisterContinueTests(unittest.TestCase):
    @patch("binance_analyzer.automation.web_actions.dismiss_global_modal", return_value=False)
    def test_register_continue_skips_google_button(self, _mock_modal) -> None:
        page = _RegisterButtonPage()

        self.assertTrue(click_register_continue_strict(page))
        self.assertFalse(page.google_button.clicked)
        self.assertTrue(page.submit_button.clicked)


if __name__ == "__main__":
    unittest.main()
