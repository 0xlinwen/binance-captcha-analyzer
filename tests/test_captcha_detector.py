from __future__ import annotations

import unittest
from unittest.mock import Mock, patch

from binance_analyzer.captcha import detector
from binance_analyzer.captcha.types import CaptchaType


class CaptchaDetectorTests(unittest.TestCase):
    def test_click_challenge_takes_priority_over_checkbox_text(self) -> None:
        modal = Mock()
        modal.is_visible.return_value = True
        modal.query_selector.side_effect = [Mock(), None]
        page = Mock()
        page.query_selector.side_effect = lambda selector: modal if selector == ".bcap-modal, .bcapc-popup" else None

        captcha_type, element = detector.detect_captcha_type(page)

        self.assertIs(captcha_type, CaptchaType.CLICK)
        self.assertIs(element, modal)

    def test_bcapc_popup_with_grid_is_click_challenge(self) -> None:
        popup = Mock()
        popup.is_visible.return_value = True
        popup.query_selector.side_effect = [None, Mock()]
        page = Mock()
        page.query_selector.side_effect = lambda selector: popup if selector == ".bcap-modal, .bcapc-popup" else None

        captcha_type, element = detector.detect_captcha_type(page)

        self.assertIs(captcha_type, CaptchaType.CLICK)
        self.assertIs(element, popup)

    def test_empty_checkbox_handle_is_disposed(self) -> None:
        handle = Mock()
        handle.as_element.return_value = None
        page = Mock()
        page.evaluate_handle.return_value = handle

        result = detector._find_checkbox_container(page)

        self.assertIsNone(result)
        handle.dispose.assert_called_once()

    @patch("binance_analyzer.captcha.detector._find_click_modal", return_value=None)
    @patch("binance_analyzer.captcha.detector._find_slider_container", return_value=None)
    def test_checkbox_detection_runs_after_specific_challenges(self, _mock_slider, _mock_click) -> None:
        checkbox = Mock()
        with patch("binance_analyzer.captcha.detector._find_checkbox_container", return_value=checkbox):
            captcha_type, element = detector.detect_captcha_type(Mock())

        self.assertIs(captcha_type, CaptchaType.CHECKBOX)
        self.assertIs(element, checkbox)

    @patch("binance_analyzer.captcha.detector._find_click_modal", return_value=None)
    @patch("binance_analyzer.captcha.detector._find_slider_container", return_value=None)
    @patch("binance_analyzer.captcha.detector._find_checkbox_container", return_value=None)
    def test_visible_bcapc_popup_is_not_treated_as_passed(self, _mock_checkbox, _mock_slider, _mock_click) -> None:
        popup = Mock()
        popup.is_visible.return_value = True
        page = Mock()
        page.query_selector.return_value = popup

        captcha_type, element = detector.detect_captcha_type(page)

        self.assertIs(captcha_type, CaptchaType.CHECKBOX)
        self.assertIs(element, popup)


if __name__ == "__main__":
    unittest.main()
