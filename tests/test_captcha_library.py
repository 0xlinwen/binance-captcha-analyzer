from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from binance_analyzer.captcha.prompts import build_click_captcha_prompt, build_slider_captcha_prompt
from binance_analyzer.captcha.service import CaptchaService
from binance_analyzer.captcha.solvers import (
    CheckboxCaptchaSolver,
    ClickCaptchaSolver,
    SliderCaptchaSolver,
    build_default_solver_registry,
)
from binance_analyzer.captcha.types import CaptchaSolveStatus, CaptchaType


def _png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


class CaptchaLibraryTests(unittest.TestCase):
    def test_default_registry_contains_existing_solver_types(self) -> None:
        registry = build_default_solver_registry()

        self.assertIsNotNone(registry.get(CaptchaType.CHECKBOX))
        self.assertIsNotNone(registry.get(CaptchaType.CLICK))
        self.assertIsNotNone(registry.get(CaptchaType.SLIDER))

    def test_prompts_are_built_from_independent_template_module(self) -> None:
        click_prompt = build_click_captcha_prompt("选择猫")
        slider_prompt = build_slider_captcha_prompt(320)

        self.assertIn("选择猫", click_prompt)
        self.assertIn("3x3", click_prompt)
        self.assertIn("320px", slider_prompt)

    @patch("binance_analyzer.captcha.service.detect_captcha_type")
    def test_service_returns_status_when_captcha_disappears_stably(self, mock_detect_captcha_type) -> None:
        mock_detect_captcha_type.return_value = (CaptchaType.UNKNOWN, None)
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = ""

        result = CaptchaService().solve(page, "sk-test", "model-x", max_attempts=1)

        self.assertIs(result, CaptchaSolveStatus.PASSED)

    def test_click_solver_requires_prompt_text(self) -> None:
        page = Mock()
        page.query_selector.return_value = None

        with self.assertRaisesRegex(RuntimeError, "提示文案"):
            ClickCaptchaSolver().solve(page, Mock(), Mock(click_retry_per_cell=3), Mock())

    def test_click_solver_uses_enter_when_confirm_button_missing(self) -> None:
        page = Mock()
        page.query_selector.return_value = None

        ClickCaptchaSolver()._submit_click_captcha(page)

        page.keyboard.press.assert_called_once_with("Enter")

    def test_checkbox_solver_rejects_found_without_coordinates(self) -> None:
        captcha_element = Mock()
        captcha_element.bounding_box.return_value = {"x": 10, "y": 20, "width": 200, "height": 100}
        captcha_element.screenshot.return_value = _png_bytes(400, 200)
        ai_client = Mock()
        ai_client.analyze_checkbox_captcha.return_value = '{"found": true}'

        result = CheckboxCaptchaSolver().solve(Mock(), captcha_element, Mock(), ai_client)

        self.assertFalse(result)

    @patch("binance_analyzer.captcha.solvers.simulate_human_drag", return_value=True)
    def test_slider_solver_converts_ai_pixels_to_css_distance(self, mock_drag) -> None:
        slider_bg = Mock()
        slider_bg.screenshot.return_value = _png_bytes(600, 300)
        slider_bg.bounding_box.return_value = {"x": 0, "y": 0, "width": 300, "height": 150}
        slider_button = Mock()
        slider_button.is_visible.return_value = True
        slider_button.bounding_box.return_value = {"x": 10, "y": 20, "width": 40, "height": 40}

        page = Mock()
        page.query_selector.side_effect = [slider_bg, slider_button]
        page.inner_text.return_value = ""
        ai_client = Mock()
        ai_client.analyze_slider_captcha.return_value = '{"gap_x": 200}'

        result = SliderCaptchaSolver().solve(page, Mock(), Mock(), ai_client)

        self.assertTrue(result)
        mock_drag.assert_called_once()
        self.assertEqual(mock_drag.call_args.args[2], 100)


if __name__ == "__main__":
    unittest.main()
