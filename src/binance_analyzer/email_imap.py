import email
import imaplib
import re
import time


def get_initial_mail_count(imap_host, imap_port, email_addr, email_password):
    max_retries = 5
    for attempt in range(max_retries):
        try:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(email_addr, email_password)
            mail.select("INBOX")
            _, messages = mail.search(None, "ALL")
            count = len(messages[0].split()) if messages[0] else 0
            mail.logout()
            print(f"当前邮件数量: {count}")
            return count
        except Exception as e:
            error_str = str(e)
            print(f"获取邮件数量失败: {e}")
            # 检测认证失败
            if b"AUTHENTICATIONFAILED" in getattr(e, 'args', (b'',))[0] if isinstance(getattr(e, 'args', (None,))[0], bytes) else "AUTHENTICATIONFAILED" in error_str:
                print(f"IMAP 认证失败 ({attempt + 1}/{max_retries})")
                if attempt >= max_retries - 1:
                    print(f"IMAP 认证连续失败 {max_retries} 次，邮箱未开启 IMAP 或密码错误，无法读取邮件")
                    return "imap_auth_failed"
                time.sleep(1)
                continue
            return 0
    return 0


def _extract_code_from_message(msg):
    body = ""
    html_body = ""

    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if not payload:
                    continue
                text = payload.decode(charset, errors="ignore")
            except Exception:
                continue

            if content_type == "text/plain":
                body += "\n" + text
            elif content_type == "text/html":
                html_body += "\n" + text
    else:
        try:
            payload = msg.get_payload(decode=True)
            charset = msg.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="ignore") if payload else ""
            if "<html" in text.lower():
                html_body = text
            else:
                body = text
        except Exception:
            pass

    content = body
    if not content and html_body:
        verification_section = re.search(r'(验证码|激活码|code)[^>]*>[\s\S]{0,200}?>(\d{6})<', html_body, re.IGNORECASE)
        if verification_section:
            return verification_section.group(2)
        content = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_body, flags=re.IGNORECASE)
        content = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', content, flags=re.IGNORECASE)
        content = re.sub(r'<[^>]+>', ' ', content)
        content = re.sub(r'&nbsp;', ' ', content)
        content = re.sub(r'\s+', ' ', content)

    patterns = [
        r'账户验证码[：:\s]*(\d{6})',
        r'验证码[：:\s]*(\d{6})',
        r'激活码[：:\s]*(\d{6})',
        r'\b(\d{6})\b',
    ]
    for pattern in patterns:
        match = re.search(pattern, content, re.IGNORECASE)
        if match:
            code = match.group(1)
            if code != "000000":
                return code
    return None


def get_email_verification_code(
    imap_host,
    imap_port,
    username,
    password,
    timeout=90,
    initial_count=0,
    consumed_codes=None,
):
    print(f"连接 IMAP: {imap_host}:{imap_port}, 用户: {username}")
    print(f"等待新邮件 (初始邮件数: {initial_count})...")
    consumed_codes = consumed_codes if consumed_codes is not None else set()

    auth_fail_count = 0
    max_auth_fails = 5

    start_time = time.time()
    while time.time() - start_time < timeout:
        mail = None
        try:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(username, password)
            auth_fail_count = 0  # 登录成功，重置计数
            mail.select("INBOX")

            _, messages = mail.search(None, "ALL")
            if not messages[0]:
                time.sleep(3)
                continue

            mail_ids = messages[0].split()
            current_count = len(mail_ids)
            if current_count <= initial_count:
                print(f"等待新邮件... (当前: {current_count})")
                time.sleep(3)
                continue

            # 只检查最近 20 封，降低并发下误取老邮件概率
            recent_mail_ids = mail_ids[-20:]
            for mail_id in reversed(recent_mail_ids):
                _, msg_data = mail.fetch(mail_id, "(RFC822)")
                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    msg = email.message_from_bytes(response_part[1])
                    from_addr = msg.get("From", "").lower()
                    if "binance" not in from_addr:
                        continue

                    code = _extract_code_from_message(msg)
                    if code and code not in consumed_codes:
                        consumed_codes.add(code)
                        print(f"找到验证码: {code}")
                        return code
        except Exception as e:
            error_str = str(e)
            print(f"IMAP 错误: {e}")
            # 检测认证失败
            if b"AUTHENTICATIONFAILED" in getattr(e, 'args', (b'',))[0] if isinstance(getattr(e, 'args', (None,))[0], bytes) else "AUTHENTICATIONFAILED" in error_str:
                auth_fail_count += 1
                print(f"IMAP 认证失败 ({auth_fail_count}/{max_auth_fails})")
                if auth_fail_count >= max_auth_fails:
                    print(f"IMAP 认证连续失败 {max_auth_fails} 次，邮箱未开启 IMAP 或密码错误，无法读取邮件")
                    return "imap_auth_failed"
        finally:
            if mail:
                try:
                    mail.logout()
                except Exception:
                    pass

        time.sleep(3)

    print("获取邮件验证码超时")
    return None


def handle_email_verification(
    page,
    imap_host,
    imap_port,
    email_addr,
    email_password,
    initial_count,
    mfa_submit_retry=2,
    consumed_codes=None,
    expected_url_pattern=None,
):
    # 等待页面加载完成
    page.wait_for_timeout(2000)

    # 记录初始 URL，用于检测页面跳转
    initial_url = page.url

    def _check_url_redirect():
        """检查页面是否跳转到其他页面（如登录页）"""
        try:
            current_url = page.url
            # 如果指定了期望的 URL 模式，检查是否匹配
            if expected_url_pattern and expected_url_pattern not in current_url:
                print(f"[URL变化] 页面已跳转: {current_url}")
                return True
            # 如果跳转到登录首页（非 mfa/password 子页面），说明验证失败
            if "/login" in current_url and "/login/mfa" not in current_url and "/login/password" not in current_url:
                if "/login/mfa" in initial_url or "/register/verification" in initial_url:
                    print(f"[URL变化] 从验证页跳转回登录页: {current_url}")
                    return True
        except Exception:
            pass
        return False

    def _dismiss_auth_error_popup():
        """检测并关闭认证失败弹窗"""
        try:
            # 检测页面是否有"认证失败"文字
            body_text = page.inner_text("body")
            if "认证失败" in body_text:
                # 尝试点击弹窗按钮（知道了 优先）
                popup_buttons = [
                    "button:has-text('知道了')",
                    "button:has-text('确定')",
                    "button:has-text('OK')",
                    "button:has-text('关闭')",
                ]
                for selector in popup_buttons:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            print("关闭了认证失败弹窗")
                            page.wait_for_timeout(1000)
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _click_get_code_button():
        """检测并点击获取验证码按钮"""
        try:
            # 检查是否已有"验证码已发送"提示
            body_text = page.inner_text("body")
            if "验证码已发送" in body_text or "已发送" in body_text:
                return False  # 已发送，不需要点击

            # 尝试点击获取验证码按钮
            get_code_buttons = [
                "button:has-text('获取验证码')",
                "button:has-text('发送验证码')",
                "button:has-text('获取')",
                "button:has-text('发送')",
                "a:has-text('获取验证码')",
                "span:has-text('获取验证码')",
                "[class*='send'] button",
                "[class*='get-code']",
            ]
            for selector in get_code_buttons:
                try:
                    btn = page.query_selector(selector)
                    if btn and btn.is_visible():
                        btn.click()
                        print("点击了获取验证码按钮")
                        page.wait_for_timeout(2000)
                        return True
                except Exception:
                    pass
        except Exception:
            pass
        return False

    code_input_selectors = [
        "input[maxlength='6']",
        "input[autocomplete='one-time-code']",
        "input[placeholder*='验证']",
        "input[placeholder*='code']",
        "input[placeholder*='Code']",
        "input[type='tel']",
        "input[type='number']",
        "input[data-e2e*='code']",
        "input[data-e2e*='otp']",
        "input[name*='code']",
        "input[name*='otp']",
        "input[id*='code']",
        "input[id*='otp']",
        "[class*='code'] input",
        "[class*='otp'] input",
        "[class*='verification'] input",
    ]

    def _find_code_input():
        """在主页面和 iframe 中查找验证码输入框"""
        # 先在主页面查找
        for selector in code_input_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    return el, page
            except Exception:
                pass

        # 在 iframe 中查找
        try:
            frames = page.frames
            for frame in frames:
                if frame == page.main_frame:
                    continue
                for selector in code_input_selectors:
                    try:
                        el = frame.query_selector(selector)
                        if el and el.is_visible():
                            print(f"在 iframe 中找到验证码输入框: {frame.url}")
                            return el, frame
                    except Exception:
                        pass
        except Exception:
            pass

        return None, None

    code_input = None
    # 尝试多次查找，等待元素出现
    for retry in range(10):
        # 检测 URL 是否跳转
        if _check_url_redirect():
            print("检测到页面跳转，退出邮件验证流程")
            return "url_changed"

        # 检测并关闭认证失败弹窗
        _dismiss_auth_error_popup()

        # 检测是否需要点击获取验证码按钮
        if retry == 0:
            _click_get_code_button()

        code_input, _ = _find_code_input()
        if code_input:
            print(f"找到验证码输入框")
            break
        print(f"等待验证码输入框出现... ({retry + 1}/10)")
        page.wait_for_timeout(1500)

    if not code_input:
        # 最后再检查一次 URL
        if _check_url_redirect():
            return "url_changed"
        return False

    email_code = get_email_verification_code(
        imap_host,
        imap_port,
        email_addr,
        email_password,
        timeout=90,
        initial_count=initial_count,
        consumed_codes=consumed_codes,
    )
    if email_code == "imap_auth_failed":
        return "imap_auth_failed"
    if not email_code:
        print("未能获取邮件验证码")
        # 检查是否因为 URL 跳转导致
        if _check_url_redirect():
            return "url_changed"
        return False

    # 重新查找输入框（等待邮件期间页面可能刷新）
    code_input = None
    for retry in range(3):
        # 检测 URL 是否跳转
        if _check_url_redirect():
            print("等待邮件期间页面跳转，退出邮件验证流程")
            return "url_changed"
        code_input, _ = _find_code_input()
        if code_input:
            break
        page.wait_for_timeout(500)

    if not code_input:
        print("填充验证码时未找到输入框")
        if _check_url_redirect():
            return "url_changed"
        return False

    print(f"输入验证码: {email_code}")
    code_input.fill(email_code)
    page.wait_for_timeout(900)

    if not _submit_mfa(page):
        for _ in range(max(1, mfa_submit_retry)):
            page.wait_for_timeout(500)
            # 检测认证失败弹窗
            _dismiss_auth_error_popup()
            if _submit_mfa(page):
                return True
        return False

    # 提交后检测认证失败弹窗
    page.wait_for_timeout(1000)
    if _dismiss_auth_error_popup():
        return False  # 认证失败，返回 False 让上层重试

    return True


def _submit_mfa(page):
    selectors = [
        "button:has-text('提交')",
        "button:has-text('Submit')",
        "button:has-text('确认')",
        "button:has-text('Confirm')",
        "button:has-text('继续')",
        "button:has-text('Continue')",
        "button:has-text('验证')",
        "button:has-text('Verify')",
    ]
    for selector in selectors:
        try:
            btn = page.query_selector(selector)
            if btn and btn.is_visible():
                btn.click()
                print(f"点击了按钮: {selector}")
                return True
        except Exception:
            pass

    try:
        page.keyboard.press("Enter")
        print("未找到提交按钮，尝试按回车...")
        return True
    except Exception as e:
        print(f"提交 MFA 失败: {e}")
        return False
