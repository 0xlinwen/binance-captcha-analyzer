from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from binance_analyzer.captcha.browser_actions import _normalize_captcha_positions
from binance_analyzer.captcha.prompts import build_click_captcha_prompt, build_slider_captcha_prompt
from binance_analyzer.captcha.service import CaptchaService
from binance_analyzer.captcha.solvers import (
    CheckboxCaptchaSolver,
    ClickCaptchaSolver,
    CaptchaSolverRegistry,
    SliderCaptchaSolver,
    build_default_solver_registry,
)
from binance_analyzer.captcha.types import CaptchaSolveStatus, CaptchaType


def _png_bytes(width: int, height: int) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + width.to_bytes(4, "big") + height.to_bytes(4, "big") + b"\x08\x02\x00\x00\x00"


class _RecordingSolver:
    """测试用 solver，记录服务层按类型调用的顺序。"""

    def __init__(self, captcha_type: CaptchaType, calls: list[CaptchaType]) -> None:
        self.captcha_type = captcha_type
        self._calls = calls

    def solve(self, page, captcha_element, context, ai_client) -> bool:
        self._calls.append(self.captcha_type)
        return True


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
        self.assertIn("第1行第1列", click_prompt)
        self.assertIn("320px", slider_prompt)
        self.assertIn("缺口左边缘", slider_prompt)

    @patch("binance_analyzer.captcha.service.detect_captcha_type")
    def test_service_returns_status_when_captcha_disappears_stably(self, mock_detect_captcha_type) -> None:
        mock_detect_captcha_type.return_value = (CaptchaType.UNKNOWN, None)
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = ""

        result = CaptchaService().solve(page, "sk-test", "model-x", max_attempts=1)

        self.assertIs(result, CaptchaSolveStatus.PASSED)

    @patch("binance_analyzer.captcha.service.is_checkbox_captcha_checked", return_value=False)
    @patch("binance_analyzer.captcha.service.detect_captcha_type")
    def test_service_keeps_checkbox_attempts_separate_from_click_attempt(
        self, mock_detect_captcha_type, _mock_checked
    ) -> None:
        calls: list[CaptchaType] = []
        registry = CaptchaSolverRegistry()
        registry.register(_RecordingSolver(CaptchaType.CHECKBOX, calls))
        registry.register(_RecordingSolver(CaptchaType.CLICK, calls))
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = ""
        checkbox_element = Mock()
        click_element = Mock()
        mock_detect_captcha_type.side_effect = [
            (CaptchaType.CHECKBOX, checkbox_element),
            (CaptchaType.CLICK, click_element),
            (CaptchaType.CLICK, click_element),
            (CaptchaType.CLICK, click_element),
            (CaptchaType.UNKNOWN, None),
            (CaptchaType.UNKNOWN, None),
            (CaptchaType.UNKNOWN, None),
        ]

        result = CaptchaService(registry).solve(page, "sk-test", "model-x", max_attempts=1)

        self.assertIs(result, CaptchaSolveStatus.PASSED)
        self.assertEqual(calls, [CaptchaType.CHECKBOX, CaptchaType.CLICK])

    def test_click_solver_returns_false_when_prompt_text_not_ready(self) -> None:
        page = Mock()
        page.query_selector.return_value = None
        page.wait_for_function.side_effect = TimeoutError("timeout")

        solved = ClickCaptchaSolver().solve(page, Mock(), Mock(click_retry_per_cell=3), Mock())

        self.assertFalse(solved)

    def test_click_solver_uses_enter_when_confirm_button_missing(self) -> None:
        page = Mock()
        page.query_selector.return_value = None

        ClickCaptchaSolver()._submit_click_captcha(page)

        page.keyboard.press.assert_called_once_with("Enter")

    def test_click_positions_support_legacy_zero_based_prompt(self) -> None:
        positions = _normalize_captcha_positions([[0, 2], [1, 0], [2, 0], [2, 1]])

        self.assertEqual(positions, [(1, 3), (2, 1), (3, 1), (3, 2)])

    def test_checkbox_solver_rejects_found_without_coordinates(self) -> None:
        captcha_element = Mock()
        captcha_element.bounding_box.return_value = {"x": 10, "y": 20, "width": 200, "height": 100}
        captcha_element.screenshot.return_value = _png_bytes(400, 200)
        ai_client = Mock()
        ai_client.analyze_checkbox_captcha.return_value = '{"found": true}'

        result = CheckboxCaptchaSolver().solve(Mock(), captcha_element, Mock(), ai_client)

        self.assertFalse(result)

    @patch("binance_analyzer.captcha.solvers.is_checkbox_captcha_checked", return_value=True)
    @patch("binance_analyzer.captcha.solvers.click_at_page_coordinate")
    def test_checkbox_solver_clicks_detected_square_when_ai_points_to_text(self, mock_click, _mock_checked) -> None:
        captcha_element = Mock()
        captcha_element.bounding_box.return_value = {"x": 100, "y": 50, "width": 400, "height": 200}
        captcha_element.screenshot.return_value = _png_bytes(800, 400)
        page = Mock()
        page.evaluate.return_value = {"x": 120, "y": 80, "width": 80, "height": 80}
        ai_client = Mock()
        ai_client.analyze_checkbox_captcha.return_value = '{"found": true, "x": 300, "y": 100}'

        result = CheckboxCaptchaSolver().solve(page, captcha_element, Mock(), ai_client)

        self.assertTrue(result)
        mock_click.assert_called_once_with(page, 160, 120)

    @patch("binance_analyzer.captcha.service.is_checkbox_captcha_checked", return_value=True)
    @patch("binance_analyzer.captcha.service.detect_captcha_type")
    def test_service_accepts_checked_checkbox_as_passed(self, mock_detect_captcha_type, _mock_checked) -> None:
        calls: list[CaptchaType] = []
        registry = CaptchaSolverRegistry()
        registry.register(_RecordingSolver(CaptchaType.CHECKBOX, calls))
        page = Mock()
        page.query_selector.return_value = None
        page.inner_text.return_value = ""
        mock_detect_captcha_type.return_value = (CaptchaType.CHECKBOX, Mock())

        result = CaptchaService(registry).solve(page, "sk-test", "model-x", max_attempts=1)

        self.assertIs(result, CaptchaSolveStatus.PASSED)
        self.assertEqual(calls, [CaptchaType.CHECKBOX])

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
