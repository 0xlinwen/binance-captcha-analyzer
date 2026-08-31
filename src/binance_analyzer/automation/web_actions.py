import random
import time
import traceback
import logging
import sys

from ..utils import dismiss_global_modal

logger = logging.getLogger(__name__)


def _human_clear_input(element, page):
    """用键盘操作清空输入框（全选+删除），避免 fill('') 的 JS 直接操作被检测"""
    element.click()
    time.sleep(random.uniform(0.05, 0.15))
    mod = "Meta" if sys.platform == "darwin" else "Control"
    page.keyboard.press(f"{mod}+a")
    time.sleep(random.uniform(0.05, 0.1))
    page.keyboard.press("Backspace")
    time.sleep(random.uniform(0.05, 0.1))


def _paste_text(page, text):
    """模拟粘贴：通过 CDP Input.insertText 一次性插入文本

    与 type() 逐字输入不同，insertText 一次性插入整段文本，
    跟真人 Cmd+V 粘贴的效果一致，产生的事件 isTrusted=true。
    """
    page.keyboard.insert_text(text)
    time.sleep(random.uniform(0.1, 0.2))


DIALOG_ACTION_TEXTS = (
    "已知晓",
    "Got it",
    "继续",
    "Continue",
)

_SSO_OR_PASSKEY_MARKERS = (
    "通行密钥",
    "passkey",
    "google",
    "apple",
    "facebook",
    "telegram",
    "sign in with",
    "continue with",
)


def visible_button_label(element) -> str:
    """读取按钮可见文案，压缩空白后用于精确匹配。"""
    try:
        return " ".join(str(element.inner_text() or "").split())
    except Exception:
        return ""


def is_sso_or_passkey_button(element) -> bool:
    """通行密钥 / Google / Apple 等第三方入口，不能当成登录继续。"""
    text = visible_button_label(element).lower()
    if not text:
        return False
    return any(marker in text for marker in _SSO_OR_PASSKEY_MARKERS)


def button_label_equals(element, text: str) -> bool:
    """整段文案相等才算命中，避免「使用通行密钥继续」匹配到「继续」。"""
    label = visible_button_label(element)
    wanted = str(text or "").strip()
    if not label or not wanted:
        return False
    return label == wanted or label.lower() == wanted.lower()


def is_unobscured_element(page, element) -> bool:
    """判断按钮中心点是否就是该元素，避免点到弹窗后面的表单按钮。"""
    try:
        if not element or not element.is_visible():
            return False
        box = element.bounding_box()
        if not box or box.get("width", 0) <= 0 or box.get("height", 0) <= 0:
            return False
        x = box["x"] + box["width"] / 2
        y = box["y"] + box["height"] / 2
        return bool(
            element.evaluate(
                """(el, point) => {
                    const top = document.elementFromPoint(point.x, point.y);
                    return !!top && (el === top || el.contains(top));
                }""",
                {"x": x, "y": y},
            )
        )
    except Exception:
        return False


def click_without_scroll(page, element, timeout_ms: int = 2500) -> None:
    """点击可见按钮，不触发 scrollIntoView。"""
    box = None
    try:
        box = element.bounding_box()
    except Exception:
        box = None
    if box and box.get("width", 0) > 0 and box.get("height", 0) > 0:
        page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
        return
    element.click(timeout=timeout_ms, force=True)


def click_unobscured_button(page, texts) -> bool:
    """点击当前最上层的指定按钮。被遮挡的同名按钮不会点，也不滚动页面。"""
    for text in texts:
        try:
            buttons = page.query_selector_all(f"button:has-text('{text}')")
        except Exception:
            continue
        if not isinstance(buttons, (list, tuple)):
            continue
        for btn in buttons:
            if not is_unobscured_element(page, btn):
                continue
            if is_sso_or_passkey_button(btn) or not button_label_equals(btn, text):
                continue
            try:
                click_without_scroll(page, btn)
                logger.info(f"点击了未遮挡按钮: {text}")
                return True
            except Exception:
                continue
    return False


def click_button(scope, texts):
    """Click button containing any text inside scope(page/locator/element)."""
    try:
        if hasattr(scope, "query_selector"):
            dismiss_global_modal(scope, logger=logger)
    except Exception:
        pass

    for text in texts:
        try:
            buttons = scope.query_selector_all(f"button:has-text('{text}')")
        except Exception:
            try:
                btn = scope.query_selector(f"button:has-text('{text}')")
                buttons = [btn] if btn else []
            except Exception:
                continue
        if not isinstance(buttons, (list, tuple)):
            continue
        for btn in buttons:
            try:
                if not btn or not btn.is_visible():
                    continue
                if is_sso_or_passkey_button(btn) or not button_label_equals(btn, text):
                    continue
                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
                logger.info(f"点击了按钮: {text}")
                return True
            except Exception:
                continue
    return False


def dismiss_cookie_popup(page):
    try:
        cookie_btns = [
            "#onetrust-reject-all-handler",
            "#onetrust-accept-btn-handler",
            "button:has-text('接受所有 Cookie')",
            "button:has-text('全部拒绝')",
            "button:has-text('接受所有')",
            "button:has-text('Accept All')",
            "button:has-text('Reject All')",
        ]
        for selector in cookie_btns:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    logger.info(f"关闭了 Cookie 弹窗: {selector}")
                    page.wait_for_timeout(random.randint(400, 600))
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def input_email(page, email_addr):
    dismiss_global_modal(page, logger=logger)
    dismiss_cookie_popup(page)
    page.wait_for_timeout(random.randint(400, 600))

    email_selectors = [
        "input[data-e2e='input-username']",
        "input[name='username']",
        "input[placeholder*='邮箱']",
        "input[placeholder*='手机']",
        "input[name='email']",
        "input[type='email']",
        "input[id*='email']",
    ]
    primary_selector = ", ".join(email_selectors[:4])
    try:
        page.wait_for_selector(primary_selector, state="visible", timeout=8000)
    except Exception:
        pass

    email_input = None
    for selector in email_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                email_input = el
                logger.info(f"找到邮箱输入框: {selector}")
                break
        except Exception:
            pass

    if email_input:
        expected_value = str(email_addr).strip().lower()
        for attempt in range(1, 3):
            email_input.click()
            time.sleep(random.uniform(0.2, 0.4))
            _human_clear_input(email_input, page)
            _paste_text(page, email_addr)
            try:
                actual_value = str(email_input.get_attribute("value") or "").strip()
            except Exception:
                actual_value = ""
            if actual_value.lower() == expected_value:
                logger.info(f"输入邮箱: {email_addr}")
                return True
            logger.warning(
                f"邮箱输入校验失败 ({attempt}/2): expected={email_addr!r}, actual={actual_value!r}"
            )
            page.wait_for_timeout(300)
        logger.error(f"邮箱输入校验连续失败: expected={email_addr!r}")
        return False

    logger.error("未找到邮箱输入框!")
    return False


def click_login_continue_strict(page):
    """Click the continue button bound to login email flow, avoid passkey entry."""
    dismiss_global_modal(page, logger=logger)

    email_input = page.query_selector(
        "input[data-e2e='input-username'], input[name='username'], input[name='email'], input[type='email']"
    )
    if not email_input or not email_input.is_visible():
        logger.error("未找到登录邮箱输入框，无法严格点击继续")
        return False

    # Prefer submit buttons near email form and exclude passkey-related buttons.
    selectors = [
        "[data-e2e='btn-accounts-form-submit']",
        "button[type='submit']:has-text('继续')",
        "button[type='submit']:has-text('Continue')",
        "button[type='button']:has-text('登录')",
        "button[type='button']:has-text('Log in')",
        "button:has-text('继续')",
        "button:has-text('Continue')",
        "button:has-text('登录')",
        "button:has-text('Log in')",
        "button:has-text('下一步')",
        "button:has-text('Next')",
        "button[type='submit']",
        "[data-e2e='btn-submit']",
        "[data-testid='btn-submit']",
    ]

    for selector in selectors:
        try:
            candidates = page.query_selector_all(selector)
            for btn in candidates:
                if not btn or not btn.is_visible():
                    continue
                if is_sso_or_passkey_button(btn):
                    continue
                if "has-text('继续')" in selector and not button_label_equals(btn, "继续"):
                    continue
                if "has-text('Continue')" in selector and not button_label_equals(btn, "Continue"):
                    continue

                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
                logger.info(f"点击了登录继续按钮: {selector}")
                return True
        except Exception:
            pass

    logger.error("未找到登录继续按钮")
    return False


def click_register_continue_strict(page):
    """点击注册邮箱表单的提交按钮，避免误点第三方登录入口。"""
    dismiss_global_modal(page, logger=logger)

    email_input = page.query_selector(
        "input[data-e2e='input-username'], input[name='username'], input[name='email'], input[type='email']"
    )
    if not email_input or not email_input.is_visible():
        logger.error("未找到注册邮箱输入框，无法严格点击继续")
        return False

    selectors = [
        "[data-e2e='btn-accounts-form-submit']",
        "form button[type='submit']:has-text('继续')",
        "form button[type='submit']:has-text('Continue')",
        "form button[type='button']:has-text('继续')",
        "form button[type='button']:has-text('Continue')",
        "form button:has-text('下一步')",
        "form button:has-text('Next')",
        "form button:has-text('注册')",
        "form button:has-text('Sign up')",
        "button:has-text('注册')",
        "button:has-text('Sign up')",
        "[data-e2e='btn-submit']",
        "[data-testid='btn-submit']",
        "button[type='submit']",
    ]

    def _is_third_party_button(btn):
        try:
            text = (btn.inner_text() or "").lower()
            third_party_words = (
                "google",
                "apple",
                "facebook",
                "telegram",
                "通行密钥",
                "passkey",
                "使用",
                "通过",
                "企业",
                "entity",
                "sign in with",
                "continue with",
            )
            return any(word in text for word in third_party_words)
        except Exception:
            return False

    for selector in selectors:
        try:
            candidates = page.query_selector_all(selector)
            for btn in candidates:
                if not btn or not btn.is_visible():
                    continue
                if _is_third_party_button(btn):
                    continue

                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
                logger.info(f"点击了注册继续按钮: {selector}")
                return True
        except Exception:
            pass

    logger.error("未找到注册继续按钮")
    return False


def input_password(page, password):
    dismiss_global_modal(page, logger=logger)
    selector = "input[name='password'], input[type='password']"
    for attempt in range(3):
        password_input = page.query_selector(selector)
        if not password_input:
            page.wait_for_timeout(300)
            continue
        try:
            password_input.click()
            time.sleep(random.uniform(0.1, 0.2))
            current_value = password_input.input_value()
            if current_value:
                _human_clear_input(password_input, page)
            _paste_text(page, password)
            logger.info("密码已输入")
            return True
        except Exception as e:
            logger.info(f"密码输入框失效，重新定位 ({attempt + 1}/3): {e}")
            page.wait_for_timeout(300)
    return False


def need_register(page):
    page_text = page.inner_text("body") if page.query_selector("body") else ""
    lower = page_text.lower()
    return (
        "未注册" in page_text
        or "没有账号" in page_text
        or "未找到币安账户" in page_text
        or "未找到币安账号" in page_text
        or "账号不存在" in page_text
        or "not registered" in lower
        or "don't have an account" in lower
        or "account does not exist" in lower
    )


def goto_with_retry(page, url, page_timeout, max_retries=3):
    for attempt in range(max_retries):
        try:
            logger.info(f"正在访问: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
            page.wait_for_timeout(random.randint(1800, 2200))
            dismiss_global_modal(page, logger=logger)
            dismiss_cookie_popup(page)

            body_text = ""
            try:
                body = page.query_selector("body")
                if body:
                    body_text = body.inner_text()
            except Exception:
                pass

            error_keywords = [
                "网络连接失败", "network error", "连接失败", "connection failed",
                "请稍后重试", "please try again", "操作失败", "operation failed",
                "403", "forbidden", "blocked", "拒绝访问",
                "cap_too_many_attempts", "208075", "208061", "$e.execute is not a function",
            ]
            for keyword in error_keywords:
                if keyword.lower() in body_text.lower():
                    logger.warning(f"检测到错误关键词: {keyword}")
                    return False
            return True
        except Exception as e:
            logger.warning(f"页面加载失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            logger.debug(f"异常详情: {traceback.format_exc()}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return False
