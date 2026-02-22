import random
import logging
import os
from datetime import datetime

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


# 配置日志
def setup_logger(email_addr):
    """为每个账号设置独立的日志文件"""
    log_dir = "logs"
    os.makedirs(log_dir, exist_ok=True)

    # 使用邮箱和时间戳作为日志文件名
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_email = email_addr.replace("@", "_at_").replace(".", "_")
    log_file = os.path.join(log_dir, f"{safe_email}_{timestamp}.log")

    # 创建logger
    logger = logging.getLogger(f"flows_{email_addr}")
    logger.setLevel(logging.DEBUG)

    # 清除已有的handlers
    logger.handlers.clear()

    # 文件handler
    file_handler = logging.FileHandler(log_file, encoding='utf-8')
    file_handler.setLevel(logging.DEBUG)

    # 控制台handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)

    # 格式
    formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
    file_handler.setFormatter(formatter)
    console_handler.setFormatter(formatter)

    logger.addHandler(file_handler)
    logger.addHandler(console_handler)

    logger.info(f"日志文件: {log_file}")
    return logger, log_file


RISK_SIGNATURES = [
    "网络连接失败",
    "操作失败",
    "PRECHECK",
    "cap_too_many_attempts",
    "208075",
    "认证失败，请刷新页面后重试",
    "$e.execute is not a function",
]


def _is_page_blank(page, logger=None):
    """检测页面是否白屏"""
    try:
        body = page.query_selector("body")
        if not body:
            return True
        text = body.inner_text().strip()
        # 白屏：body 为空或只有很少内容
        if len(text) < 50:
            # 再检查是否有可见元素
            visible_elements = page.query_selector_all("button, input, a, img")
            visible_count = sum(1 for el in visible_elements if el.is_visible())
            if visible_count < 3:
                if logger:
                    logger.debug(f"检测到白屏: text_len={len(text)}, visible_elements={visible_count}")
                return True
        return False
    except Exception as e:
        if logger:
            logger.debug(f"_is_page_blank 异常: {e}")
        return False


def _has_risk_error(page, logger=None):
    """检查页面是否有风控错误"""
    try:
        body = page.query_selector("body")
        text = body.inner_text() if body else ""
        has_risk = any(sig.lower() in text.lower() for sig in RISK_SIGNATURES)
        if has_risk:
            print(f"[DEBUG] 检测到风控错误")
            if logger:
                logger.debug("检测到风控错误")
        return has_risk, text
    except Exception as e:
        print(f"[DEBUG] _has_risk_error 异常: {e}")
        if logger:
            logger.debug(f"_has_risk_error 异常: {e}")
        return False, ""


def _dismiss_error_popup(page, logger=None):
    """检查并点击"已知晓"等弹窗按钮"""
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
                msg = f"点击了关闭按钮: {selector}"
                print(f"[DEBUG] {msg}")
                if logger:
                    logger.debug(msg)
                page.wait_for_timeout(random.randint(800, 1200))
                return True
        except Exception as e:
            msg = f"点击按钮 {selector} 失败: {e}"
            print(f"[DEBUG] {msg}")
            if logger:
                logger.debug(msg)
    return False


def _check_url_change(page, url_before, action_name, wait_ms=1000, logger=None):
    """
    检查URL是否变化，用于检测页面跳转

    Args:
        page: Playwright page对象
        url_before: 操作前的URL
        action_name: 操作名称（用于日志）
        wait_ms: 等待时间（毫秒）
        logger: 日志对象

    Returns:
        新的URL
    """
    try:
        page.wait_for_timeout(wait_ms)
        url_after = page.url
        if url_after != url_before:
            msg = f"URL变化 {action_name} 后: {url_before} -> {url_after}"
            print(f"[URL变化] {msg}")
            if logger:
                logger.info(msg)
        else:
            msg = f"{action_name} 后URL未变化: {url_before}"
            print(f"[DEBUG] {msg}")
            if logger:
                logger.debug(msg)
        return url_after
    except Exception as e:
        msg = f"_check_url_change 异常: {e}"
        print(f"[DEBUG] {msg}")
        if logger:
            logger.error(msg)
        return url_before


def login_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    # 设置日志
    logger, log_file = setup_logger(email_addr)
    logger.info("="*60)
    logger.info(f"开始登录流程: {email_addr}")
    logger.info("="*60)

    api_key = config["openrouter_api_key"]
    model = config.get("models", [])
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    login_start_url = config.get("login", {}).get("start_url", "https://accounts.binance.com/zh-CN/login")
    captcha_config = config.get("captcha", {})
    mfa_config = config.get("mfa", {})

    logger.info(f"登录URL: {login_start_url}")
    logger.info("打开登录页面...")
    print(f"\n[日志文件] {log_file}")

    if not goto_with_retry(page, login_start_url, page_timeout=page_timeout):
        logger.error("登录页面加载失败")
        print("登录页面加载失败")
        return False

    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
    consumed_codes = set()
    mfa_retry_count = 0

    max_iterations = 20
    captcha_fail_count = 0  # 验证码连续失败计数
    max_captcha_fails = 3   # 最大连续失败次数

    for iteration in range(max_iterations):
        try:
            page.wait_for_timeout(1000)  # 固定等待1秒
            url = page.url
            msg = f"迭代 {iteration + 1} 当前 URL: {url}"
            print(f"\n[迭代 {iteration + 1}] 当前 URL: {url}")
            logger.info(msg)

            # 检测白屏
            if _is_page_blank(page, logger):
                print("[WARNING] 检测到白屏，刷新页面...")
                logger.warning("检测到白屏，刷新页面")
                page.reload()
                page.wait_for_timeout(2000)
                continue

            has_risk, body_text = _has_risk_error(page, logger)
            if has_risk:
                msg = "检测到错误页面!"
                print(f"[DEBUG] {msg}")
                logger.warning(msg)
                logger.debug(f"页面内容: {body_text[:500]}")
                _dismiss_error_popup(page, logger)
                if ("网络连接失败" in body_text or "208075" in body_text) and iteration < 5:
                    msg = "尝试刷新页面..."
                    print(f"[DEBUG] {msg}")
                    logger.info(msg)
                    page.reload()
                    page.wait_for_timeout(random.randint(2500, 3500))
                    continue
        except Exception as e:
            msg = f"迭代 {iteration + 1} 异常: {e}"
            print(f"[DEBUG] {msg}")
            logger.error(msg)
            import traceback
            stack = traceback.format_exc()
            print(f"[DEBUG] 堆栈: {stack}")
            logger.error(f"堆栈: {stack}")
            continue

        if "/login/stay-signed-in" in url:
            print("[状态] stay-signed-in - 点击'是'按钮")
            logger.info("stay-signed-in - 点击'是'按钮")
            url_before = url
            click_button(page, ["是", "Yes", "确定", "OK"])
            url = _check_url_change(page, url_before, "点击'是'按钮", 1500)
            continue

        if "/login/mfa" in url:
            print("[状态] mfa - 处理邮件验证码")
            logger.info("mfa - 处理邮件验证码")
            url_before = url
            try:
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
                    print("[DEBUG] 邮件验证失败")
                    logger.debug("邮件验证失败")
                    return False
            except Exception as e:
                print(f"[DEBUG] handle_email_verification 异常: {e}")
                logger.debug(f"handle_email_verification 异常: {e}")
                import traceback
                print(f"[DEBUG] 堆栈: {traceback.format_exc()}")
                logger.debug(f"堆栈: {traceback.format_exc()}")
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
                    logger.info("检测到账号未注册")
                    return "need_register"

                mfa_retry_count += 1
                print(f"[状态] MFA 提交后仍停留在当前页，重试次数: {mfa_retry_count}")
                logger.info(f"MFA 提交后仍停留在当前页，重试次数: {mfa_retry_count}")
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
            logger.info("password - 输入密码")
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page)

            try:
                initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
                print(f"[DEBUG] 获取初始邮件数: {initial_mail_count}")
                logger.debug(f"获取初始邮件数: {initial_mail_count}")
            except Exception as e:
                print(f"[DEBUG] 获取初始邮件数失败: {e}")
                logger.debug(f"获取初始邮件数失败: {e}")
                initial_mail_count = 0

            try:
                input_password(page, email_password)
                page.wait_for_timeout(random.randint(400, 600))
                click_button(page, ["继续", "Continue", "下一步", "Next"])
                url = _check_url_change(page, url_before, "输入密码并点击继续", 1800, logger)
            except Exception as e:
                print(f"[DEBUG] 输入密码或点击继续失败: {e}")
                logger.debug(f"输入密码或点击继续失败: {e}")
                import traceback
                print(f"[DEBUG] 堆栈: {traceback.format_exc()}")
                logger.debug(f"堆栈: {traceback.format_exc()}")
                continue

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
                logger.error("IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            if captcha_result is False:
                captcha_fail_count += 1
                print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                if captcha_fail_count >= max_captcha_fails:
                    logger.error("验证码连续失败次数过多")
                    return False
                continue
            captcha_fail_count = 0  # 成功则重置计数

            # 验证码处理后再次检查URL
            url = _check_url_change(page, url, "验证码处理", 500, logger)
            continue

        if "/login" in url and "/login/" not in url:
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page, logger)

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
                logger.error("IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            if captcha_result is not True:
                if captcha_result is False:
                    captcha_fail_count += 1
                    print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    if captcha_fail_count >= max_captcha_fails:
                        logger.error("验证码连续失败次数过多")
                        return False
                    continue
                url = _check_url_change(page, url_before, "验证码处理", 500, logger)
                continue
            captcha_fail_count = 0  # 成功则重置计数

            # 验证码处理成功后，检查URL是否已经跳转
            url = _check_url_change(page, url_before, "验证码处理", 500, logger)
            if url != url_before:
                # URL已经变化，跳到下一次迭代处理新的URL状态
                print(f"[状态] 验证码处理后URL已变化，进入新状态: {url}")
                logger.info(f"验证码处理后URL已变化，进入新状态: {url}")
                continue

            email_input = page.query_selector("input[data-e2e='input-username'], input[name='username'], input[name='email']")
            if email_input:
                try:
                    current_value = email_input.input_value()
                    print(f"[DEBUG] 邮箱输入框当前值: {current_value}")
                    logger.debug(f"邮箱输入框当前值: {current_value}")
                except Exception as e:
                    print(f"[DEBUG] 获取邮箱输入框值失败: {e}")
                    logger.debug(f"获取邮箱输入框值失败: {e}")
                    current_value = ""
                if current_value and email_addr in current_value:
                    print("[状态] 邮箱已输入，点击继续...")
                    logger.info("邮箱已输入，点击继续...")
                    click_login_continue_strict(page)

                    # 点击后等待1-3秒
                    wait_time = random.uniform(1000, 3000)
                    print(f"[等待] 点击继续后等待 {wait_time/1000:.1f}秒...")
                    page.wait_for_timeout(int(wait_time))

                    # 检查并点击"已知晓"按钮
                    _dismiss_error_popup(page, logger)

                    url = _check_url_change(page, url_before, "点击继续", 500, logger)

                    # 检查页面是否提示未注册
                    if need_register(page):
                        print("[状态] 页面提示账号未注册，跳转到注册流程")
                        logger.info("页面提示账号未注册，跳转到注册流程")
                        return "need_register"

                    if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                        # 可能是验证码错误，继续重试而不是直接判断未注册
                        print("[状态] 邮箱+验证码完成后未进入密码页，继续重试...")
                        logger.info("邮箱+验证码完成后未进入密码页，继续重试...")
                    continue

            print("[状态] login - 输入邮箱")
            logger.info("login - 输入邮箱")
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))
            click_login_continue_strict(page)

            # 点击后等待1-3秒
            wait_time = random.uniform(1000, 3000)
            print(f"[等待] 点击继续后等待 {wait_time/1000:.1f}秒...")
            page.wait_for_timeout(int(wait_time))

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page, logger)

            url = _check_url_change(page, url_before, "输入邮箱并点击继续", 500, logger)

            # 检查页面是否提示未注册
            if need_register(page):
                print("[状态] 页面提示账号未注册，跳转到注册流程")
                logger.info("页面提示账号未注册，跳转到注册流程")
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
                if post_captcha_result is False:
                    captcha_fail_count += 1
                    print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    if captcha_fail_count >= max_captcha_fails:
                        logger.error("验证码连续失败次数过多")
                        return False
                    continue
                captcha_fail_count = 0  # 成功则重置计数
                url = _check_url_change(page, url, "验证码处理", 500, logger)
                if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                    # 检查是否有明确的未注册提示
                    if need_register(page):
                        print("[状态] 检测到账号未注册")
                        logger.info("检测到账号未注册")
                        return "need_register"
                    # 否则继续重试，可能是验证码错误
                    print("[状态] 验证码可能错误，继续重试...")
                    logger.info("验证码可能错误，继续重试...")
                    continue

            if need_register(page):
                print("[状态] 检测到账号未注册")
                logger.info("检测到账号未注册")
                return "need_register"
            continue

        if "/my/" in url or ("binance.com" in url and "login" not in url and "register" not in url):
            print("[状态] 登录成功!")
            logger.info("登录成功!")
            return True

        if need_register(page):
            print("[状态] 检测到账号未注册")
            logger.info("检测到账号未注册")
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
            logger.error("IP 被风控，请更换代理或等待后重试")
            return "rate_limited"
        if captcha_result is False:
            captcha_fail_count += 1
            print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
            logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
            if captcha_fail_count >= max_captcha_fails:
                logger.error("验证码连续失败次数过多")
                return False
            continue
        captcha_fail_count = 0  # 成功则重置计数

        # 验证码处理后检查URL
        url = _check_url_change(page, url_before, "兜底验证码处理", 500, logger)

    print("登录流程超过最大迭代次数")
    logger.warning("登录流程超过最大迭代次数")
    return False


def register_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    # 设置日志
    logger, log_file = setup_logger(email_addr)
    logger.info("="*60)
    logger.info(f"开始注册流程: {email_addr}")
    logger.info("="*60)

    api_key = config["openrouter_api_key"]
    model = config.get("models", [])
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]

    print(f"\n[日志文件] {log_file}")
    print("\n[注册状态机] 打开注册页面...")
    logger.info("打开注册页面...")
    if not goto_with_retry(page, "https://www.binance.com/zh-CN/register", page_timeout=page_timeout):
        print("注册页面加载失败")
        logger.error("注册页面加载失败")
        return False

    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
    consumed_codes = set()
    captcha_fail_count = 0  # 验证码连续失败计数
    max_captcha_fails = 3   # 最大连续失败次数

    max_iterations = 20
    for iteration in range(max_iterations):
        page.wait_for_timeout(1000)  # 固定等待1秒
        url = page.url
        print(f"\n[注册迭代 {iteration + 1}] 当前 URL: {url}")
        logger.info(f"注册迭代 {iteration + 1} 当前 URL: {url}")

        # 检测白屏
        if _is_page_blank(page, logger):
            print("[WARNING] 检测到白屏，刷新页面...")
            logger.warning("检测到白屏，刷新页面")
            page.reload()
            page.wait_for_timeout(2000)
            continue

        if "/invite" in url:
            print("[状态] invite - 点击下一步")
            logger.info("invite - 点击下一步")
            url_before = url
            click_button(page, ["下一步", "Next", "跳过", "Skip"])
            url = _check_url_change(page, url_before, "点击下一步", 1500, logger)
            continue

        if "/register/register-set-password" in url or "/register-set-password" in url:
            print("[状态] set-password - 输入密码")
            logger.info("set-password - 输入密码")
            url_before = url
            input_password(page, email_password)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            url = _check_url_change(page, url_before, "输入密码并点击继续", 1800, logger)

            # 某些场景会出现多轮验证码：点击继续后立即重复检测并处理
            for _ in range(3):
                captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
                if captcha_result == "rate_limited":
                    return "rate_limited"
                if captcha_result is False:
                    captcha_fail_count += 1
                    print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    if captcha_fail_count >= max_captcha_fails:
                        logger.error("验证码连续失败次数过多")
                        return False
                    break
                captcha_fail_count = 0
                url = _check_url_change(page, url, "验证码处理", 500, logger)
                if "/register/register-set-password" not in url and "/register-set-password" not in url:
                    break
                # 不再重复点击"继续"，只等待验证码刷新
                page.wait_for_timeout(random.randint(1200, 1800))
            continue

        if "/register/verification" in url or "/verification-new-register" in url:
            print("[状态] verification - 处理邮件验证码")
            logger.info("verification - 处理邮件验证码")
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
                logger.error("邮件验证失败")
                return False
            url = _check_url_change(page, url_before, "邮件验证", 2500, logger)
            continue

        if "/register" in url and "/register/" not in url:
            print("[状态] register - 输入邮箱")
            logger.info("register - 输入邮箱")
            url_before = url
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))

            try:
                checkbox = page.query_selector("input[type='checkbox']")
                if checkbox and not checkbox.is_checked():
                    checkbox.click()
                    print("勾选了创建账户复选框")
                    logger.debug("勾选了创建账户复选框")
            except Exception:
                pass

            page.wait_for_timeout(random.randint(400, 600))
            initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            url = _check_url_change(page, url_before, "输入邮箱并点击继续", 2500, logger)

            # 注册页会连续出现"继续 -> 验证码 -> 继续 -> 验证码"
            # 同一轮内做闭环，避免等下一轮状态机再处理
            for _ in range(4):
                captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
                if captcha_result == "rate_limited":
                    return "rate_limited"
                if captcha_result is False:
                    captcha_fail_count += 1
                    print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
                    if captcha_fail_count >= max_captcha_fails:
                        logger.error("验证码连续失败次数过多")
                        return False
                    break
                captcha_fail_count = 0
                url = _check_url_change(page, url, "验证码处理", 500, logger)
                if "/register" not in url or "/register/" in url:
                    break
                # 不再重复点击"继续"，只等待验证码刷新
                page.wait_for_timeout(random.randint(1500, 2200))
            continue

        if "/my/" in url or ("binance.com" in url and "register" not in url and "login" not in url):
            print("[状态] 注册成功!")
            logger.info("注册成功!")
            return True

        url_before = url
        captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
        if captcha_result == "rate_limited":
            return "rate_limited"
        if captcha_result is False:
            captcha_fail_count += 1
            print(f"[WARNING] 验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
            logger.warning(f"验证码处理失败 ({captcha_fail_count}/{max_captcha_fails})")
            if captcha_fail_count >= max_captcha_fails:
                logger.error("验证码连续失败次数过多")
                return False
            continue
        captcha_fail_count = 0

        # 验证码处理后检查URL
        url = _check_url_change(page, url_before, "兜底验证码处理", 500, logger)

    print("注册流程超过最大迭代次数")
    logger.warning("注册流程超过最大迭代次数")
    return False
