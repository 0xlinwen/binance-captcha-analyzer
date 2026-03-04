import random
import time
import traceback
import logging

from .utils import dismiss_global_modal

logger = logging.getLogger(__name__)


def click_button(scope, texts):
    """Click button containing any text inside scope(page/locator/element)."""
    try:
        if hasattr(scope, "query_selector"):
            dismiss_global_modal(scope, logger=logger)
    except Exception:
        pass

    for text in texts:
        try:
            btn = scope.query_selector(f"button:has-text('{text}')")
            if btn and btn.is_visible():
                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
                logger.info(f"点击了按钮: {text}")
                return True
        except Exception:
            pass
    return False


def dismiss_cookie_popup(page):
    try:
        cookie_btns = [
            "button:has-text('接受所有')",
            "button:has-text('Accept All')",
            "button:has-text('全部拒绝')",
            "button:has-text('Reject All')",
            "#onetrust-accept-btn-handler",
            "#onetrust-reject-all-handler",
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
        email_input.click()
        time.sleep(random.uniform(0.2, 0.4))
        email_input.fill("")
        time.sleep(random.uniform(0.1, 0.2))
        email_input.type(email_addr, delay=random.randint(50, 100))
        logger.info(f"输入邮箱: {email_addr}")
        return True

    inputs = page.query_selector_all("input[type='text'], input:not([type])")
    if inputs:
        inputs[0].click()
        time.sleep(random.uniform(0.2, 0.4))
        inputs[0].type(email_addr, delay=random.randint(50, 100))
        logger.info(f"使用兜底输入框输入邮箱: {email_addr}")
        return True

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
        "button[type='submit']:has-text('继续')",
        "button[type='submit']:has-text('Continue')",
        "button:has-text('继续')",
        "button:has-text('Continue')",
        "button:has-text('下一步')",
        "button:has-text('Next')",
        # 更宽泛的选择器
        "button[type='submit']",
        "[data-e2e='btn-submit']",
        "[data-testid='btn-submit']",
        "form button",
    ]

    def _is_passkey_button(btn):
        try:
            txt = (btn.inner_text() or "").lower()
            if "通行密钥" in txt or "passkey" in txt:
                return True
        except Exception:
            pass
        return False

    for selector in selectors:
        try:
            candidates = page.query_selector_all(selector)
            for btn in candidates:
                if not btn or not btn.is_visible():
                    continue
                if _is_passkey_button(btn):
                    continue
                # Skip buttons inside explicit passkey blocks.
                try:
                    passkey_ancestor = btn.evaluate(
                        """(node) => {
                            let cur = node;
                            while (cur && cur !== document.body) {
                              const t = (cur.innerText || '').toLowerCase();
                              if (t.includes('通行密钥') || t.includes('passkey')) return true;
                              cur = cur.parentElement;
                            }
                            return false;
                        }"""
                    )
                    if passkey_ancestor:
                        continue
                except Exception:
                    pass

                try:
                    btn.click(timeout=5000)
                except Exception:
                    btn.click(timeout=5000, force=True)
                logger.info(f"点击了登录继续按钮: {selector}")
                return True
        except Exception:
            pass

    # 尝试通过 JavaScript 查找并点击按钮
    try:
        clicked = page.evaluate(
            """() => {
                // 查找所有按钮
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const text = (btn.innerText || '').toLowerCase();
                    // 排除 passkey 相关按钮
                    if (text.includes('通行密钥') || text.includes('passkey')) continue;
                    // 查找继续/下一步按钮
                    if (text.includes('继续') || text.includes('continue') ||
                        text.includes('下一步') || text.includes('next')) {
                        btn.click();
                        return true;
                    }
                }
                // 查找 submit 类型按钮
                const submitBtns = document.querySelectorAll('button[type="submit"]');
                for (const btn of submitBtns) {
                    const text = (btn.innerText || '').toLowerCase();
                    if (text.includes('通行密钥') || text.includes('passkey')) continue;
                    if (btn.offsetParent !== null) {  // 可见
                        btn.click();
                        return true;
                    }
                }
                return false;
            }"""
        )
        if clicked:
            logger.info("通过 JavaScript 点击了继续按钮")
            return True
    except Exception as e:
        logger.warning(f"JavaScript 点击按钮失败: {e}")

    # 最后尝试回车提交
    try:
        email_input.press("Enter")
        logger.info("未找到继续按钮，使用回车提交")
        return True
    except Exception:
        return False


def input_password(page, password):
    dismiss_global_modal(page, logger=logger)
    password_input = page.query_selector("input[name='password'], input[type='password']")
    if password_input:
        password_input.click()
        time.sleep(random.uniform(0.1, 0.2))
        # 清空已有内容再输入
        current_value = password_input.input_value()
        if current_value:
            password_input.fill("")
            time.sleep(random.uniform(0.1, 0.2))
        password_input.type(password, delay=random.randint(30, 80))
        logger.info("密码已输入")
        return True
    return False


def need_register(page):
    page_text = page.inner_text("body") if page.query_selector("body") else ""
    lower = page_text.lower()
    return (
        "未注册" in page_text
        or "没有账号" in page_text
        or "未找到币安账户" in page_text
        or "未找到币安账号" in page_text
        or "未找到" in page_text
        or "找不到" in page_text
        or "账号不存在" in page_text
        or "not registered" in lower
        or "don't have an account" in lower
        or "not found" in lower
        or "account does not exist" in lower
    )


def goto_with_retry(page, url, page_timeout, max_retries=3):
    for attempt in range(max_retries):
        try:
            logger.info(f"正在访问: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=page_timeout)
            page.wait_for_timeout(random.randint(1800, 2200))

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
