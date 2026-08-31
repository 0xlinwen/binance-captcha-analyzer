from __future__ import annotations

import unittest
from unittest.mock import patch

from binance_analyzer.automation.web_actions import (
    click_button,
    click_login_continue_strict,
    click_register_continue_strict,
    click_unobscured_button,
    click_without_scroll,
    input_email,
    is_unobscured_element,
    need_register,
)


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

    def wait_for_selector(self, selector: str, **_kwargs):
        raise TimeoutError(selector)


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


class _OverlayAwareButton:
    def __init__(self, topmost: bool, text: str = "") -> None:
        self.topmost = topmost
        self.text = text
        self.clicked = False
        self.force_clicked = False

    def is_visible(self) -> bool:
        return True

    def inner_text(self) -> str:
        return self.text

    def bounding_box(self):
        return {"x": 20, "y": 40, "width": 100, "height": 40}

    def evaluate(self, _script, _point=None):
        return self.topmost

    def click(self, **kwargs) -> None:
        if kwargs.get("force"):
            self.force_clicked = True
        else:
            self.clicked = True


class _Mouse:
    def __init__(self, page: "_MousePage") -> None:
        self.page = page

    def click(self, x, y) -> None:
        self.page.mouse_clicks.append((x, y))


class _MousePage:
    def __init__(self, buttons: dict[str, list[_OverlayAwareButton]]) -> None:
        self.buttons = buttons
        self.mouse_clicks: list[tuple[float, float]] = []
        self.mouse = _Mouse(self)

    def query_selector_all(self, selector: str):
        for text, buttons in self.buttons.items():
            if f"has-text('{text}')" in selector:
                return buttons
        return []


class UnobscuredDialogClickTests(unittest.TestCase):
    def test_skips_continue_button_hidden_behind_dialog(self) -> None:
        hidden_continue = _OverlayAwareButton(topmost=False, text="继续")
        page = _MousePage({"继续": [hidden_continue], "已知晓": []})

        self.assertFalse(click_unobscured_button(page, ("已知晓", "继续")))
        self.assertEqual(page.mouse_clicks, [])
        self.assertFalse(hidden_continue.clicked)
        self.assertFalse(hidden_continue.force_clicked)

    def test_skips_passkey_button_that_contains_continue_text(self) -> None:
        passkey = _OverlayAwareButton(topmost=True, text="使用通行密钥继续")
        page = _MousePage({"继续": [passkey], "已知晓": []})

        self.assertFalse(click_unobscured_button(page, ("已知晓", "继续")))
        self.assertEqual(page.mouse_clicks, [])
        self.assertFalse(passkey.clicked)

    def test_clicks_topmost_ack_button_without_element_scroll(self) -> None:
        ack = _OverlayAwareButton(topmost=True, text="已知晓")
        hidden_continue = _OverlayAwareButton(topmost=False, text="继续")
        page = _MousePage({"已知晓": [ack], "继续": [hidden_continue]})

        self.assertTrue(click_unobscured_button(page, ("已知晓", "继续")))
        self.assertEqual(page.mouse_clicks, [(70, 60)])
        self.assertFalse(ack.clicked)
        self.assertFalse(hidden_continue.clicked)

    def test_is_unobscured_element_requires_topmost_hit(self) -> None:
        page = object()
        self.assertTrue(is_unobscured_element(page, _OverlayAwareButton(topmost=True)))
        self.assertFalse(is_unobscured_element(page, _OverlayAwareButton(topmost=False)))

    def test_click_without_scroll_uses_mouse_coordinates(self) -> None:
        button = _OverlayAwareButton(topmost=True)
        page = _MousePage({})

        click_without_scroll(page, button)

        self.assertEqual(page.mouse_clicks, [(70, 60)])
        self.assertFalse(button.clicked)
        self.assertFalse(button.force_clicked)


class _LoginButtonPage:
    def __init__(self, submit_text: str = "继续") -> None:
        self.email_input = _FakeElement()
        self.passkey = _FakeElement("使用通行密钥继续")
        self.google = _FakeElement("通过 Google 继续")
        self.submit = _FakeElement(submit_text)

    def query_selector(self, selector: str):
        if "input" in selector:
            return self.email_input
        return None

    def query_selector_all(self, selector: str):
        buttons = [self.passkey, self.google, self.submit]
        if "has-text('继续')" in selector:
            return [button for button in buttons if "继续" in button.text]
        if "has-text('Continue')" in selector:
            return [button for button in buttons if "continue" in button.text.lower()]
        if "submit" in selector or "btn-accounts" in selector or "btn-submit" in selector:
            return [self.submit]
        return buttons


class LoginContinueTests(unittest.TestCase):
    @patch("binance_analyzer.automation.web_actions.dismiss_global_modal", return_value=False)
    def test_login_continue_skips_passkey_and_google(self, _mock_modal) -> None:
        page = _LoginButtonPage()

        self.assertTrue(click_login_continue_strict(page))
        self.assertFalse(page.passkey.clicked)
        self.assertFalse(page.google.clicked)
        self.assertTrue(page.submit.clicked)

    @patch("binance_analyzer.automation.web_actions.dismiss_global_modal", return_value=False)
    def test_login_continue_skips_passkey_when_submit_is_loading(self, _mock_modal) -> None:
        page = _LoginButtonPage(submit_text="")

        self.assertTrue(click_login_continue_strict(page))
        self.assertFalse(page.passkey.clicked)
        self.assertTrue(page.submit.clicked)

    @patch("binance_analyzer.automation.web_actions.dismiss_global_modal", return_value=False)
    def test_generic_continue_click_skips_passkey(self, _mock_modal) -> None:
        page = _LoginButtonPage()

        self.assertTrue(click_button(page, ["继续"]))
        self.assertFalse(page.passkey.clicked)
        self.assertTrue(page.submit.clicked)


if __name__ == "__main__":
    unittest.main()
