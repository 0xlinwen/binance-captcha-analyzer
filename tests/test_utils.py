from __future__ import annotations

import unittest

from binance_analyzer.utils import dismiss_global_modal


class _FakeButton:
    def __init__(self):
        self.clicked = False

    def is_visible(self):
        return True

    def click(self, *args, **kwargs):
        self.clicked = True


class _FakePage:
    def __init__(self, body_text: str):
        self.body_text = body_text
        self.button = _FakeButton()
        self.waits = []

    def query_selector(self, selector: str):
        if selector == "button:has-text('I Understand')":
            return self.button
        return None

    def inner_text(self, selector: str):
        if selector == "body":
            return self.body_text
        return ""

    def wait_for_timeout(self, timeout_ms: int):
        self.waits.append(timeout_ms)

    def evaluate(self, *_args, **_kwargs):
        return False


class DismissGlobalModalTests(unittest.TestCase):
    def test_clicks_hong_kong_region_notice_understand_button(self) -> None:
        page = _FakePage(
            "The products and services on this website are not intended for individuals in Hong Kong."
        )

        dismissed = dismiss_global_modal(page)

        self.assertTrue(dismissed)
        self.assertTrue(page.button.clicked)
        self.assertEqual(page.waits, [300])


if __name__ == "__main__":
    unittest.main()
