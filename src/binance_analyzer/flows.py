import random

from .captcha_solver import solve_captcha, solve_captcha_if_present, detect_captcha_type
from .email_imap import get_initial_mail_count, handle_email_verification
from .web_actions import (
    click_button,
    click_login_continue_strict,
    goto_with_retry,
    input_email,
    input_password,
    need_register,
)


RISK_SIGNATURES = [
    "网络连接失败",
    "操作失败",
    "PRECHECK",
    "cap_too_many_attempts",
    "208075",
    "认证失败，请刷新页面后重试",
    "$e.execute is not a function",
]


def _has_risk_error(page):
    try:
        body = page.query_selector("body")
        text = body.inner_text() if body else ""
        return any(sig.lower() in text.lower() for sig in RISK_SIGNATURES), text
    except Exception:
        return False, ""


def _dismiss_error_popup(page):
    dismiss_btns = [
        "button:has-text('已知晓')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('确定')",
        "button:has-text('关闭')",
        "button:has-text('Close')",
    ]
    for selector in dismiss_btns:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                print(f"点击了关闭按钮: {selector}")
                page.wait_for_timeout(random.randint(800, 1200))
                return True
        except Exception:
            pass
    return False


def _check_url_change(page, url_before, action_name, wait_ms=1000):
    """
    检查URL是否变化，用于检测页面跳转

    Args:
        page: Playwright page对象
        url_before: 操作前的URL
        action_name: 操作名称（用于日志）
        wait_ms: 等待时间（毫秒）

    Returns:
        新的URL
    """
    page.wait_for_timeout(wait_ms)
    url_after = page.url
    if url_after != url_before:
        print(f"[URL变化] {action_name} 后: {url_before} -> {url_after}")
    return url_after


def login_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    api_key = config["openrouter_api_key"]
    model = config.get("models", [])
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    login_start_url = config.get("login", {}).get("start_url", "https://accounts.binance.com/zh-CN/login")
    captcha_config = config.get("captcha", {})
    mfa_config = config.get("mfa", {})

    print("\n[URL状态机] 打开登录页面...")
    if not goto_with_retry(page, login_start_url, page_timeout=page_timeout):
        print("登录页面加载失败")
        return False

    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
    consumed_codes = set()
    mfa_retry_count = 0

    max_iterations = 20
    for iteration in range(max_iterations):
        page.wait_for_timeout(random.randint(1800, 2500))
        url = page.url
        print(f"\n[迭代 {iteration + 1}] 当前 URL: {url}")

        has_risk, body_text = _has_risk_error(page)
        if has_risk:
            print("检测到错误页面!")
            print(f"页面内容: {body_text[:500]}")
            _dismiss_error_popup(page)
            if ("网络连接失败" in body_text or "208075" in body_text) and iteration < 5:
                print("尝试刷新页面...")
                page.reload()
                page.wait_for_timeout(random.randint(2500, 3500))
                continue

        if "/login/stay-signed-in" in url:
            print("[状态] stay-signed-in - 点击'是'按钮")
            url_before = url
            click_button(page, ["是", "Yes", "确定", "OK"])
            url = _check_url_change(page, url_before, "点击'是'按钮", 1500)
            continue

        if "/login/mfa" in url:
            print("[状态] mfa - 处理邮件验证码")
            url_before = url
            ok = handle_email_verification(
                page,
                imap_host,
                imap_port,
                email_addr,
                email_password,
                initial_mail_count,
                mfa_submit_retry=mfa_config.get("submit_retry", 2),
                consumed_codes=consumed_codes,
            )
            if not ok:
                return False

            page.wait_for_timeout(random.randint(2200, 3200))
            url_after = page.url
            if "/my/" in url_after or "/login/stay-signed-in" in url_after:
                mfa_retry_count = 0
                continue

            if url_after == url_before or "/login/mfa" in url_after:
                # Only go to register flow if explicit not-registered signals are present.
                if need_register(page):
                    print("[状态] 检测到账号未注册")
                    return "need_register"

                mfa_retry_count += 1
                print(f"[状态] MFA 提交后仍停留在当前页，重试次数: {mfa_retry_count}")
                if mfa_retry_count >= 3:
                    has_risk_now, _ = _has_risk_error(page)
                    if has_risk_now:
                        return "rate_limited"
                    return False
                continue

            mfa_retry_count = 0
            continue

        if "/login/password" in url:
            print("[状态] password - 输入密码")
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page)

            initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
            input_password(page, email_password)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            url = _check_url_change(page, url_before, "输入密码并点击继续", 1800)

            captcha_result = solve_captcha_if_present(
                page,
                api_key,
                model,
                email_addr,
                captcha_config=captcha_config,
                reload_url=login_start_url,
                page_timeout=page_timeout,
            )
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"

            # 验证码处理后再次检查URL
            url = _check_url_change(page, url, "验证码处理", 500)
            continue

        if "/login" in url and "/login/" not in url:
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page)

            captcha_result = solve_captcha_if_present(
                page,
                api_key,
                model,
                email_addr,
                captcha_config=captcha_config,
                reload_url=login_start_url,
                page_timeout=page_timeout,
            )
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            if captcha_result is not True:
                url = _check_url_change(page, url_before, "验证码处理", 500)
                continue

            email_input = page.query_selector("input[data-e2e='input-username'], input[name='username'], input[name='email']")
            if email_input:
                try:
                    current_value = email_input.input_value()
                except Exception:
                    current_value = ""
                if current_value and email_addr in current_value:
                    print("[状态] 邮箱已输入，点击继续...")
                    click_login_continue_strict(page)
                    url = _check_url_change(page, url_before, "点击继续", 2500)

                    # 检查是否有验证码
                    captcha_type, _ = detect_captcha_type(page)
                    if captcha_type == "unknown":
                        # 没有验证码，检查URL是否变化
                        if url == url_before or ("/login" in url and "/login/" not in url):
                            # URL没变化或仍在登录页，可能是未注册
                            print("[状态] 点击继续后无验证码且URL未变化，检查是否未注册...")
                            if need_register(page):
                                print("[状态] 检测到账号未注册")
                                return "need_register"
                            # 等待一下再检查
                            page.wait_for_timeout(1000)
                            url_after_wait = page.url
                            if url_after_wait == url:
                                print("[状态] 等待后URL仍未变化，判定为未注册")
                                return "need_register"

                    if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                        # 可能是验证码错误，继续重试而不是直接判断未注册
                        print("[状态] 邮箱+验证码完成后未进入密码页，继续重试...")
                    continue

            print("[状态] login - 输入邮箱")
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))
            click_login_continue_strict(page)
            url = _check_url_change(page, url_before, "输入邮箱并点击继续", 2500)

            # 检查是否有验证码
            captcha_type, _ = detect_captcha_type(page)
            if captcha_type == "unknown":
                # 没有验证码，检查URL是否变化
                if url == url_before or ("/login" in url and "/login/" not in url):
                    # URL没变化或仍在登录页，可能是未注册
                    print("[状态] 输入邮箱后无验证码且URL未变化，检查是否未注册...")
                    if need_register(page):
                        print("[状态] 检测到账号未注册")
                        return "need_register"
                    # 等待一下再检查
                    page.wait_for_timeout(1000)
                    url_after_wait = page.url
                    if url_after_wait == url:
                        print("[状态] 等待后URL仍未变化，判定为未注册")
                        return "need_register"

            if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                # 先再做一次验证码处理
                post_captcha_result = solve_captcha_if_present(
                    page,
                    api_key,
                    model,
                    email_addr,
                    captcha_config=captcha_config,
                    reload_url=login_start_url,
                    page_timeout=page_timeout,
                )
                if post_captcha_result == "rate_limited":
                    return "rate_limited"
                url = _check_url_change(page, url, "验证码处理", 500)
                if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                    # 检查是否有明确的未注册提示
                    if need_register(page):
                        print("[状态] 检测到账号未注册")
                        return "need_register"
                    # 否则继续重试，可能是验证码错误
                    print("[状态] 验证码可能错误，继续重试...")
                    continue

            if need_register(page):
                print("[状态] 检测到账号未注册")
                return "need_register"
            continue

        if "/my/" in url or ("binance.com" in url and "login" not in url and "register" not in url):
            print("[状态] 登录成功!")
            return True

        if need_register(page):
            print("[状态] 检测到账号未注册")
            return "need_register"

        url_before = url
        captcha_result = solve_captcha_if_present(
            page,
            api_key,
            model,
            email_addr,
            captcha_config=captcha_config,
            reload_url=login_start_url,
            page_timeout=page_timeout,
        )
        if captcha_result == "rate_limited":
            print("[ERROR] IP 被风控，请更换代理或等待后重试")
            return "rate_limited"

        # 验证码处理后检查URL
        url = _check_url_change(page, url_before, "兜底验证码处理", 500)

    print("登录流程超过最大迭代次数")
    return False


def register_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    api_key = config["openrouter_api_key"]
    model = config.get("models", [])
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]

    print("\n[注册状态机] 打开注册页面...")
    if not goto_with_retry(page, "https://www.binance.com/zh-CN/register", page_timeout=page_timeout):
        print("注册页面加载失败")
        return False

    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
    consumed_codes = set()

    max_iterations = 20
    for iteration in range(max_iterations):
        page.wait_for_timeout(random.randint(1800, 2500))
        url = page.url
        print(f"\n[注册迭代 {iteration + 1}] 当前 URL: {url}")

        if "/invite" in url:
            print("[状态] invite - 点击下一步")
            url_before = url
            click_button(page, ["下一步", "Next", "跳过", "Skip"])
            url = _check_url_change(page, url_before, "点击下一步", 1500)
            continue

        if "/register/register-set-password" in url or "/register-set-password" in url:
            print("[状态] set-password - 输入密码")
            url_before = url
            input_password(page, email_password)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            url = _check_url_change(page, url_before, "输入密码并点击继续", 1800)

            # 某些场景会出现多轮验证码：点击继续后立即重复检测并处理
            for _ in range(3):
                captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
                if captcha_result == "rate_limited":
                    return "rate_limited"
                url = _check_url_change(page, url, "验证码处理", 500)
                if "/register/register-set-password" not in url and "/register-set-password" not in url:
                    break
                # 不再重复点击"继续"，只等待验证码刷新
                page.wait_for_timeout(random.randint(1200, 1800))
            continue

        if "/register/verification" in url or "/verification-new-register" in url:
            print("[状态] verification - 处理邮件验证码")
            url_before = url
            if not handle_email_verification(
                page,
                imap_host,
                imap_port,
                email_addr,
                email_password,
                initial_mail_count,
                consumed_codes=consumed_codes,
            ):
                return False
            url = _check_url_change(page, url_before, "邮件验证", 2500)
            continue

        if "/register" in url and "/register/" not in url:
            print("[状态] register - 输入邮箱")
            url_before = url
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))

            try:
                checkbox = page.query_selector("input[type='checkbox']")
                if checkbox and not checkbox.is_checked():
                    checkbox.click()
                    print("勾选了创建账户复选框")
            except Exception:
                pass

            page.wait_for_timeout(random.randint(400, 600))
            initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            url = _check_url_change(page, url_before, "输入邮箱并点击继续", 2500)

            # 注册页会连续出现"继续 -> 验证码 -> 继续 -> 验证码"
            # 同一轮内做闭环，避免等下一轮状态机再处理
            for _ in range(4):
                captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
                if captcha_result == "rate_limited":
                    return "rate_limited"
                url = _check_url_change(page, url, "验证码处理", 500)
                if "/register" not in url or "/register/" in url:
                    break
                # 不再重复点击"继续"，只等待验证码刷新
                page.wait_for_timeout(random.randint(1500, 2200))
            continue

        if "/my/" in url or ("binance.com" in url and "register" not in url and "login" not in url):
            print("[状态] 注册成功!")
            return True

        url_before = url
        captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
        if captcha_result == "rate_limited":
            return "rate_limited"

        # 验证码处理后检查URL
        url = _check_url_change(page, url_before, "兜底验证码处理", 500)

    print("注册流程超过最大迭代次数")
    return False
