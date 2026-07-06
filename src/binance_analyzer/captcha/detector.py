"""验证码类型检测。"""

from __future__ import annotations

from .types import CaptchaType


# 人机验证复选框的唯一文字信号（不同语言在此扩充）
CHECKBOX_TEXT_SIGNALS = ("进行人机身份验证", "确认您是真人", "I'm not a robot", "verify you are human")

# 复选框容器的候选祖先选择器，从内到外优先匹配"最紧凑的弹窗"
_CHECKBOX_CONTAINER_SELECTORS = "[role='dialog'],[class*='modal'],[class*='popup'],[class*='captcha'],[class*='verify']"


def _is_visible(element) -> bool:
    try:
        return bool(element and element.is_visible())
    except Exception:
        return False


def _find_click_modal(page):
    """定位图片点击验证码弹窗，要求存在真实网格或提示元素。"""
    modal = page.query_selector(".bcap-modal, .bcapc-popup")
    if not _is_visible(modal):
        return None
    try:
        has_prompt = bool(modal.query_selector("#tagLabel, .bcap-text-message-title2"))
        has_grid = bool(modal.query_selector("[class*='bcap-image']"))
    except Exception:
        return None
    return modal if has_prompt or has_grid else None


def _find_slider_container(page):
    """定位滑块验证码容器。"""
    slider_selectors = [
        ".bs-modal",
        ".bs-slide-container",
        ".verify-slider",
        ".bcap-slider",
        ".bcap-drag",
        "[class*='slider-container']",
        "[class*='slide-verify']",
        "[class*='captcha-slider']",
        ".geetest_slider",
    ]
    for selector in slider_selectors:
        slider = page.query_selector(selector)
        if _is_visible(slider):
            return slider

    popup = page.query_selector(".bcapc-popup, .bs-popup")
    if _is_visible(popup):
        try:
            has_slider = bool(popup.query_selector("[class*='slider'], [class*='slide'], [class*='drag'], [class*='thumb']"))
        except Exception:
            has_slider = False
        if has_slider:
            return popup
    return None


def _find_visible_captcha_popup(page):
    """定位仍可见的 Binance 验证码 popup。"""
    popup = page.query_selector(".bcapc-popup, .bs-popup")
    return popup if _is_visible(popup) else None


def _find_checkbox_container(page):
    """按文字信号定位人机验证复选框弹窗容器，返回 ElementHandle 或 None。

    找到包含信号文字的可见元素后，向上取最近的弹窗祖先作为截图容器；
    找不到弹窗祖先时回退到文字元素本身。
    """
    handle = page.evaluate_handle(
        """(args) => {
            const [signals, containerSel] = args;
            const normalizedSignals = signals.map((sig) => String(sig).toLowerCase().replace(/[’‘`]/g, "'"));
            const normalize = (value) => String(value || '').toLowerCase().replace(/[’‘`]/g, "'");
            const nodes = Array.from(document.querySelectorAll('div,span,label,p'));
            for (const node of nodes) {
                const text = normalize((node.textContent || '').trim());
                if (!text || text.length > 40) continue;  // 只认短文本，避开整页文本节点
                if (!normalizedSignals.some((sig) => text.includes(sig))) continue;
                const rect = node.getBoundingClientRect();
                if (rect.width <= 0 || rect.height <= 0) continue;  // 不可见跳过
                const container = node.closest(containerSel);
                if (container) return container;
                // 无已知弹窗祖先时向上取明显更宽的祖先，确保把文字左侧的复选框方框也截进来
                let el = node;
                for (let i = 0; i < 4 && el.parentElement; i++) {
                    el = el.parentElement;
                    const r = el.getBoundingClientRect();
                    if (r.width >= rect.width + 30 && r.width <= window.innerWidth * 0.9) return el;
                }
                return node;
            }
            return null;
        }""",
        [list(CHECKBOX_TEXT_SIGNALS), _CHECKBOX_CONTAINER_SELECTORS],
    )
    element = handle.as_element()
    try:
        if element and element.is_visible():
            return element
    except Exception:
        pass
    handle.dispose()
    return None


def is_checkbox_captcha_checked(page) -> bool:
    """判断人机验证复选框是否已经出现勾选完成态。"""
    try:
        return bool(
            page.evaluate(
                """(signals) => {
                    const normalizedSignals = signals.map((sig) => String(sig).toLowerCase().replace(/[’‘`]/g, "'"));
                    const normalize = (value) => String(value || '').toLowerCase().replace(/[’‘`]/g, "'");
                    const nodes = Array.from(document.querySelectorAll('div,span,label,p'));
                    for (const node of nodes) {
                        const text = normalize((node.textContent || '').trim());
                        if (!text || text.length > 40) continue;
                        if (!normalizedSignals.some((sig) => text.includes(sig))) continue;
                        const textRect = node.getBoundingClientRect();
                        if (textRect.width <= 0 || textRect.height <= 0) continue;
                        const textCenterY = textRect.top + textRect.height / 2;
                        const candidates = Array.from(document.querySelectorAll('button,div,span,label,input,[role="checkbox"]'));
                        for (const candidate of candidates) {
                            const rect = candidate.getBoundingClientRect();
                            if (rect.width < 20 || rect.height < 20 || rect.width > 120 || rect.height > 120) continue;
                            if (rect.right > textRect.left + 12) continue;
                            if (Math.abs((rect.top + rect.height / 2) - textCenterY) > Math.max(40, textRect.height)) continue;
                            const ariaChecked = String(candidate.getAttribute('aria-checked') || '').toLowerCase();
                            const className = String(candidate.className || '').toLowerCase();
                            const textValue = String(candidate.textContent || '');
                            if (candidate.checked === true || ariaChecked === 'true') return true;
                            if (/(checked|selected|active|success|complete|passed)/.test(className)) return true;
                            if (/[✓✔]/.test(textValue)) return true;
                            if (candidate.querySelector('svg,path,[class*="check"],[class*="tick"]')) return true;
                        }
                    }
                    return false;
                }""",
                list(CHECKBOX_TEXT_SIGNALS),
            )
        )
    except Exception:
        return False


def detect_captcha_type(page) -> tuple[CaptchaType, object | None]:
    """检测当前页面上的验证码类型。"""
    # 已进入图片/滑块挑战时优先按真实挑战处理，避免复选框说明文字残留导致误判。
    click_modal = _find_click_modal(page)
    if click_modal:
        return CaptchaType.CLICK, click_modal

    slider = _find_slider_container(page)
    if slider:
        return CaptchaType.SLIDER, slider

    checkbox_container = _find_checkbox_container(page)
    if checkbox_container:
        return CaptchaType.CHECKBOX, checkbox_container

    visible_popup = _find_visible_captcha_popup(page)
    if visible_popup:
        return CaptchaType.CHECKBOX, visible_popup

    return CaptchaType.UNKNOWN, None
