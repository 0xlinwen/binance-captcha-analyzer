import random
import time
import traceback


def dismiss_global_modal(page):
    """Dismiss blocking global modal in current page only."""
    try:
        modal = page.query_selector("#globalmodal-common")
        if not modal or not modal.is_visible():
            return False
    except Exception:
        return False

    selectors = [
        "#globalmodal-common button:has-text('已知晓')",
        "#globalmodal-common button:has-text('确定')",
        "#globalmodal-common button:has-text('关闭')",
        "#globalmodal-common button:has-text('OK')",
        "#globalmodal-common button:has-text('Got it')",
        "#globalmodal-common button:has-text('Close')",
        "#globalmodal-common [aria-label='Close']",
        "#globalmodal-common .close",
    ]
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click(timeout=2500, force=True)
                page.wait_for_timeout(300)
                print(f"关闭全局弹窗: {selector}")
                return True
        except Exception:
            pass

    # Fallback: hide overlay in current page only.
    try:
        page.evaluate(
            """
            () => {
              const n = document.querySelector('#globalmodal-common');
              if (!n) return false;
              n.style.display = 'none';
              n.style.pointerEvents = 'none';
              return true;
            }
            """
        )
        page.wait_for_timeout(150)
        print("通过注入样式隐藏全局弹窗")
        return True
    except Exception:
        return False


def click_button(scope, texts):
    """Click button containing any text inside scope(page/locator/element)."""
    try:
        if hasattr(scope, "query_selector"):
            dismiss_global_modal(scope)
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
                print(f"点击了按钮: {text}")
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
                    print(f"关闭了 Cookie 弹窗: {selector}")
                    page.wait_for_timeout(random.randint(400, 600))
                    return True
            except Exception:
                pass
    except Exception:
        pass
    return False


def input_email(page, email_addr):
    dismiss_global_modal(page)
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
                print(f"找到邮箱输入框: {selector}")
                break
        except Exception:
            pass

    if email_input:
        email_input.click()
        time.sleep(random.uniform(0.2, 0.4))
        email_input.fill("")
        time.sleep(random.uniform(0.1, 0.2))
        email_input.type(email_addr, delay=random.randint(50, 100))
        print(f"输入邮箱: {email_addr}")
        return True

    inputs = page.query_selector_all("input[type='text'], input:not([type])")
    if inputs:
        inputs[0].click()
        time.sleep(random.uniform(0.2, 0.4))
        inputs[0].type(email_addr, delay=random.randint(50, 100))
        print(f"使用兜底输入框输入邮箱: {email_addr}")
        return True

    print("[ERROR] 未找到邮箱输入框!")
    return False


def click_login_continue_strict(page):
    """Click the continue button bound to login email flow, avoid passkey entry."""
    dismiss_global_modal(page)

    email_input = page.query_selector(
        "input[data-e2e='input-username'], input[name='username'], input[name='email'], input[type='email']"
    )
    if not email_input or not email_input.is_visible():
        print("[ERROR] 未找到登录邮箱输入框，无法严格点击继续")
        return False

    # Prefer submit buttons near email form and exclude passkey-related buttons.
    selectors = [
        "button[type='submit']:has-text('继续')",
        "button[type='submit']:has-text('Continue')",
        "button:has-text('继续')",
        "button:has-text('Continue')",
        "button:has-text('下一步')",
        "button:has-text('Next')",
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
                print(f"点击了登录继续按钮: {selector}")
                return True
        except Exception:
            pass

    # Fallback: submit via Enter on email input to avoid random wrong button click.
    try:
        email_input.press("Enter")
        print("未找到安全的继续按钮，改为邮箱输入框回车提交")
        return True
    except Exception:
        return False


def input_password(page, password):
    dismiss_global_modal(page)
    password_input = page.query_selector("input[name='password'], input[type='password']")
    if password_input:
        password_input.click()
        time.sleep(random.uniform(0.1, 0.2))
        password_input.type(password, delay=random.randint(30, 80))
        print("密码已输入")
        return True
    return False


def need_register(page):
    page_text = page.inner_text("body") if page.query_selector("body") else ""
    lower = page_text.lower()
    return (
        "未注册" in page_text
        or "没有账号" in page_text
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
            print(f"正在访问: {url}")
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
                "cap_too_many_attempts", "208075", "$e.execute is not a function",
            ]
            for keyword in error_keywords:
                if keyword.lower() in body_text.lower():
                    print(f"检测到错误关键词: {keyword}")
            return True
        except Exception as e:
            print(f"页面加载失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            print(f"异常详情: {traceback.format_exc()}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return False
