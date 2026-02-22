import email
import imaplib
import re
import time


def get_initial_mail_count(imap_host, imap_port, email_addr, email_password):
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
        print(f"获取邮件数量失败: {e}")
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

    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(username, password)
            mail.select("INBOX")

            _, messages = mail.search(None, "ALL")
            if not messages[0]:
                mail.logout()
                time.sleep(3)
                continue

            mail_ids = messages[0].split()
            current_count = len(mail_ids)
            if current_count <= initial_count:
                mail.logout()
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
                        mail.logout()
                        print(f"找到验证码: {code}")
                        return code
            mail.logout()
        except Exception as e:
            print(f"IMAP 错误: {e}")

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
):
    code_input_selectors = [
        "input[maxlength='6']",
        "input[autocomplete='one-time-code']",
        "input[placeholder*='验证']",
        "input[placeholder*='code']",
        "input[type='tel']",
    ]

    code_input = None
    for selector in code_input_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                code_input = el
                print(f"找到验证码输入框: {selector}")
                break
        except Exception:
            pass

    if not code_input:
        print("未找到验证码输入框")
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
    if not email_code:
        print("未能获取邮件验证码")
        return False

    print(f"输入验证码: {email_code}")
    code_input.fill(email_code)
    page.wait_for_timeout(900)

    if not _submit_mfa(page):
        for _ in range(max(1, mfa_submit_retry)):
            page.wait_for_timeout(500)
            if _submit_mfa(page):
                return True
        return False
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
