"""验证码 solver 实现与注册表。"""

from __future__ import annotations

import random
from abc import ABC, abstractmethod
from math import isfinite
from numbers import Real

from ..utils import dismiss_global_modal
from .ai_client import OpenRouterCaptchaClient, parse_json_response, png_dimensions, screenshot_to_base64
from .browser_actions import click_at_page_coordinate, click_captcha_images, simulate_human_drag
from .types import CaptchaSolveContext, CaptchaType


def _is_finite_number(value) -> bool:
    return isinstance(value, Real) and not isinstance(value, bool) and isfinite(float(value))


class BaseCaptchaSolver(ABC):
    """验证码 solver 基类。"""

    captcha_type: CaptchaType

    @abstractmethod
    def solve(self, page, captcha_element, context: CaptchaSolveContext, ai_client: OpenRouterCaptchaClient) -> bool:
        """执行验证码求解，成功返回 True，失败返回 False。"""


class CheckboxCaptchaSolver(BaseCaptchaSolver):
    """人机身份验证复选框 solver。

    BCaptcha 初始入口：勾选"进行人机身份验证"复选框后，才会弹出后续的
    点击网格或滑块挑战。用 AI 返回复选框中心的截图相对坐标，换算为页面
    绝对坐标后模拟点击——不依赖被混淆的内部 DOM 选择器。
    """

    captcha_type = CaptchaType.CHECKBOX

    def solve(self, page, captcha_element, context: CaptchaSolveContext, ai_client: OpenRouterCaptchaClient) -> bool:
        box = captcha_element.bounding_box()
        if not box or box["width"] <= 0 or box["height"] <= 0:
            print("[WARNING] 人机验证复选框无法获取容器位置，进入下一次尝试")
            return False

        screenshot_bytes = captcha_element.screenshot()
        img_width, img_height = png_dimensions(screenshot_bytes)
        screenshot_base64 = screenshot_to_base64(screenshot_bytes)

        result = ai_client.analyze_checkbox_captcha(screenshot_base64, img_width, img_height)
        data = parse_json_response(result)
        if not data.get("found"):
            print("[WARNING] AI 未在弹窗中识别到人机验证复选框，进入下一次尝试")
            return False

        raw_x = data.get("x")
        raw_y = data.get("y")
        if not _is_finite_number(raw_x) or not _is_finite_number(raw_y):
            print("[WARNING] AI 未返回有效复选框坐标，进入下一次尝试")
            return False

        rel_x = float(raw_x)
        rel_y = float(raw_y)
        if not (0 <= rel_x <= img_width and 0 <= rel_y <= img_height):
            print(f"[WARNING] AI 返回坐标越界: ({rel_x}, {rel_y})，截图尺寸 {img_width}x{img_height}")
            return False

        # AI 坐标基于截图真实像素；按 CSS/真实像素比例还原，再加容器偏移得到页面绝对坐标
        scale_x = box["width"] / img_width
        scale_y = box["height"] / img_height
        page_x = box["x"] + rel_x * scale_x
        page_y = box["y"] + rel_y * scale_y
        print(f"[复选框] AI 坐标({rel_x:.0f},{rel_y:.0f}) -> 页面({page_x:.1f},{page_y:.1f})")

        click_at_page_coordinate(page, page_x, page_y)
        page.wait_for_timeout(random.randint(1000, 1600))
        return True


class ClickCaptchaSolver(BaseCaptchaSolver):
    """点击图片验证码 solver。"""

    captcha_type = CaptchaType.CLICK

    def solve(self, page, captcha_element, context: CaptchaSolveContext, ai_client: OpenRouterCaptchaClient) -> bool:
        prompt_element = page.query_selector("#tagLabel, .bcap-text-message-title2")
        if not prompt_element:
            raise RuntimeError("点击验证码缺少提示文案元素: #tagLabel, .bcap-text-message-title2")
        prompt_text = prompt_element.inner_text().strip()
        if not prompt_text:
            raise RuntimeError("点击验证码提示文案为空")
        screenshot_base64 = screenshot_to_base64(captcha_element.screenshot())

        result = ai_client.analyze_click_captcha(screenshot_base64, prompt_text)
        positions = parse_json_response(result).get("positions", [])
        if not positions:
            print("[WARNING] 点击验证码未识别到有效位置，进入下一次尝试")
            return False

        clicked = click_captcha_images(page, positions, click_retry_per_cell=context.click_retry_per_cell)
        if not clicked:
            print("[WARNING] 点击验证码本轮未成功点击任何格子，进入下一次尝试")
            return False

        page.wait_for_timeout(random.randint(800, 1200))
        self._submit_click_captcha(page)
        page.wait_for_timeout(random.randint(1000, 1500))
        return True

    def _submit_click_captcha(self, page) -> None:
        verify_selectors = [
            ".bcap-verify-button",
            "button:has-text('验证')",
            "button:has-text('确认')",
            "button:has-text('提交')",
            "button:has-text('Verify')",
            "button:has-text('Confirm')",
            "[class*='verify']",
        ]
        for selector in verify_selectors:
            try:
                dismiss_global_modal(page)
                verify_btn = page.query_selector(selector)
                if verify_btn and verify_btn.is_visible():
                    verify_btn.click()
                    print(f"点击了验证码确认按钮: {selector}")
                    return
            except Exception:
                pass

        try:
            page.keyboard.press("Enter")
            print("未找到验证码确认按钮，使用 Enter 提交")
            return
        except Exception as exc:
            raise RuntimeError(f"点击验证码缺少确认按钮，已检查 selectors: {', '.join(verify_selectors)}") from exc


class SliderCaptchaSolver(BaseCaptchaSolver):
    """滑块验证码 solver。"""

    captcha_type = CaptchaType.SLIDER

    def solve(self, page, captcha_element, context: CaptchaSolveContext, ai_client: OpenRouterCaptchaClient) -> bool:
        screenshot_bytes, image_width, scale_x = self._capture_slider_image(page, captcha_element)
        screenshot_base64 = screenshot_to_base64(screenshot_bytes)
        page.wait_for_timeout(random.randint(500, 800))

        slider_btn = self._find_slider_button(page)
        if not slider_btn:
            print("[ERROR] 未找到滑块按钮")
            self._log_slider_candidates(page)
            return False

        ai_result = ai_client.analyze_slider_captcha(screenshot_base64, image_width)
        ai_data = parse_json_response(ai_result)
        raw_gap_x = ai_data.get("gap_x")
        if not _is_finite_number(raw_gap_x) or raw_gap_x <= 0:
            print("[AI] 未获取到有效的缺口位置")
            return False
        ai_gap_x = float(raw_gap_x)
        drag_distance = int(round(ai_gap_x * scale_x))

        print(f"[AI] 识别结果: gap_x={ai_gap_x:.0f}, css_distance={drag_distance}")
        dismiss_global_modal(page)
        page.wait_for_timeout(200)

        if not slider_btn.is_visible():
            slider_btn = self._find_slider_button(page)
            if not slider_btn:
                print("[AI] 错误: 无法重新找到滑块按钮")
                return False

        if not simulate_human_drag(page, slider_btn, drag_distance):
            print("[AI] 滑动执行失败")
            return False

        page.wait_for_timeout(random.randint(1500, 2500))
        return True

    def _capture_slider_image(self, page, captcha_element) -> tuple[bytes, int, float]:
        slider_bg = page.query_selector(
            ".bs-main-image, [class*='slider-bg'], [class*='captcha-bg'], .bcap-bg, [class*='verify-img']"
        )
        if slider_bg:
            screenshot_bytes = slider_bg.screenshot()
            box = slider_bg.bounding_box()
        else:
            screenshot_bytes = captcha_element.screenshot()
            box = captcha_element.bounding_box()
        image_width, _image_height = png_dimensions(screenshot_bytes)
        css_width = float(box["width"]) if box and box.get("width") else float(image_width)
        return screenshot_bytes, image_width, css_width / image_width

    def _find_slider_button(self, page):
        slider_selectors = [
            ".bs-slide-thumb",
            ".bcap-slider-btn",
            "[class*='slider-button']",
            "[class*='drag-btn']",
            "[class*='slide-thumb']",
            "[class*='slider-btn']",
            ".slider-button",
            ".drag-button",
            "[class*='thumb']",
            "div[class*='slide'] > div",
            "div[class*='slider'] > div",
        ]
        for selector in slider_selectors:
            btn = page.query_selector(selector)
            if not btn or not btn.is_visible():
                continue
            try:
                box = btn.bounding_box()
            except Exception:
                continue
            if box and box["width"] > 0 and box["height"] > 0:
                print(
                    f"[滑块] 找到滑块按钮: {selector}, "
                    f"位置: ({box['x']:.1f}, {box['y']:.1f}), 尺寸: {box['width']:.1f}x{box['height']:.1f}"
                )
                return btn
        return None

    def _log_slider_candidates(self, page) -> None:
        try:
            all_candidates = page.query_selector_all("[class*='slide'], [class*='slider'], [class*='drag'], [class*='thumb']")
        except Exception:
            return
        for index, element in enumerate(all_candidates[:5]):
            try:
                if not element.is_visible():
                    continue
                class_name = element.get_attribute("class") or ""
                tag = element.evaluate("el => el.tagName")
                box = element.bounding_box()
                if box:
                    print(f"  候选[{index}]: <{tag}> class='{class_name[:50]}' size={box['width']:.0f}x{box['height']:.0f}")
            except Exception:
                pass


class CaptchaSolverRegistry:
    """验证码 solver 注册表。"""

    def __init__(self) -> None:
        self._solvers: dict[CaptchaType, BaseCaptchaSolver] = {}

    def register(self, solver: BaseCaptchaSolver) -> None:
        """注册一个验证码 solver。"""
        self._solvers[solver.captcha_type] = solver

    def get(self, captcha_type: CaptchaType) -> BaseCaptchaSolver | None:
        """按验证码类型获取 solver。"""
        return self._solvers.get(captcha_type)


def build_default_solver_registry() -> CaptchaSolverRegistry:
    """构建默认 solver 注册表。"""
    registry = CaptchaSolverRegistry()
    registry.register(CheckboxCaptchaSolver())
    registry.register(ClickCaptchaSolver())
    registry.register(SliderCaptchaSolver())
    return registry
