import email
import imaplib
import random
import re
import time
import logging
from html.parser import HTMLParser
from contextlib import contextmanager

import requests

from .constants import (
    IMAP_RETRY_COUNT,
    IMAP_CONNECTION_TIMEOUT_SEC,
    IMAP_FETCH_TIMEOUT_SEC,
)
from .utils import retry_with_backoff
from .web_actions import _human_clear_input
from .exceptions import (
    IMAPAuthFailed,
    IMAPConnectionError,
    IMAPTimeout,
    EmailCodeNotFound,
)

logger = logging.getLogger(__name__)
OUTLOOK_MAIL_API_URL = "https://api.bujidian.com/getMailInfo"


def _is_outlook_address(email_addr: str) -> bool:
    return str(email_addr).strip().lower().endswith("@outlook.com")


def _strip_html(text: str) -> str:
    """HTML 转纯文本"""
    class _Parser(HTMLParser):
        def __init__(self):
            super().__init__()
            self.parts = []

        def handle_data(self, data):
            self.parts.append(data)

    parser = _Parser()
    parser.feed(text)
    return "".join(parser.parts)


def _extract_6digit_code(text: str) -> str | None:
    """
    从文本中提取 6 位验证码。
    修复：
    1. \b 在中文字符后不工作，改用 (?<!\d)...(?!\d) lookaround
    2. 过滤时间戳格式（HH:MM:SS / YYYY-MM-DD）中误匹配的数字
    3. 优先匹配"验证码/驗證碼"附近的数字，再兜底匹配独立 6 位数
    """
    if not text:
        return None

    # 优先：验证码关键词后的数字（简体+繁体+英文）
    keyword_patterns = [
        r'[验驗][证證][码碼][：:\s]*(\d{4,8})',
        r'[激][活][码碼][：:\s]*(\d{4,8})',
        r'[Cc]ode[：:\s]*(\d{4,8})',
        r'[Vv]erification[：:\s]*(\d{4,8})',
        r'[Cc]onfirmation[：:\s]*(\d{4,8})',
        r'OTP[：:\s]*(\d{4,8})',
        # 繁体"您的驗證碼"后紧跟数字（无分隔符）
        r'[验驗][证證][码碼](\d{4,8})',
    ]
    for pattern in keyword_patterns:
        m = re.search(pattern, text)
        if m:
            return m.group(1)

    # 兜底：独立的 6 位数字
    # 排除时间戳（如 06:39:39 / 2026-03-04）
    # 先把时间戳替换掉再匹配
    clean = re.sub(r'\d{4}-\d{2}-\d{2}', '', text)          # 日期
    clean = re.sub(r'\d{1,2}:\d{2}:\d{2}', '', clean)        # 时间
    clean = re.sub(r'\d{4}/\d{2}/\d{2}', '', clean)          # 斜线日期
    # (?<!\d)(\d{6})(?!\d) 代替 \b，兼容中文前缀
    m = re.search(r'(?<!\d)(\d{6})(?!\d)', clean)
    if m:
        return m.group(1)

    return None


def _extract_code_from_api_text(text: str) -> str | None:
    """从 API 返回的 subject 或 content（可能含 HTML）中提取验证码"""
    if not text:
        return None

    # 如果包含 HTML，先剥离
    if "<" in text and ">" in text:
        text = _strip_html(text)

    return _extract_6digit_code(text)


def _fetch_outlook_code_via_api(email_addr: str, email_password: str, timeout=60, poll_interval=5, should_abort=None):
    """使用外部 API 拉取 outlook 邮箱验证码。"""
    PERMANENT_FAIL_KEYWORDS = [
        "邮箱信息不存在",
        "邮箱密码错误",
        "账号不存在",
        "account not found",
        "invalid credentials",
    ]

    start_time = time.time()
    permanent_fail_count = 0
    max_permanent_fails = 3

    while time.time() - start_time < timeout:
        # 检查外部是否要求中止（如页面 URL 已变化）
        if should_abort and should_abort():
            logger.info(f"[{email_addr}] 外部中止信号，停止等待 Outlook 验证码")
            return "aborted"

        print(f"[{email_addr}] 请求邮件 ...")
        try:
            resp = requests.get(
                OUTLOOK_MAIL_API_URL,
                params={"name": email_addr, "pwd": email_password},
                timeout=15,
            )
            data = resp.json()
        except Exception as e:
            print(f"[{email_addr}] 请求失败: {e}")
            logger.warning(f"[{email_addr}] Outlook API 请求失败: {e}")
            time.sleep(poll_interval)
            continue

        if data.get("status") != 1:
            msg = data.get("message", "") or ""
            print(f"[{email_addr}] API 返回失败: {msg}")
            logger.info(f"[{email_addr}] Outlook API 返回失败: {msg}")

            if any(kw in msg for kw in PERMANENT_FAIL_KEYWORDS):
                permanent_fail_count += 1
                if permanent_fail_count >= max_permanent_fails:
                    print(f"[{email_addr}] 邮箱认证失败（连续 {permanent_fail_count} 次），停止重试")
                    logger.error(f"[{email_addr}] Outlook API 永久性错误，停止: {msg}")
                    return "imap_auth_failed"
            else:
                permanent_fail_count = 0

            time.sleep(poll_interval)
            continue

        msg_obj = data.get("message", {}) or {}
        subject = msg_obj.get("subject", "") or ""
        content = msg_obj.get("content", "") or ""
        send_time = msg_obj.get("send_time_utc", "") or ""
        print(f"[{email_addr}] 收到邮件: {subject} ({send_time})")
        logger.info(f"[{email_addr}] Outlook API 收到邮件: subject={subject!r}")

        # 优先从 subject 提取，再从 content 提取
        code = _extract_code_from_api_text(subject)
        if not code:
            code = _extract_code_from_api_text(content)

        if code:
            print(f"[{email_addr}] 找到验证码: {code}")
            logger.info(f"[{email_addr}] Outlook API 找到验证码: {code}")
            return code

        print(f"[{email_addr}] 未匹配到验证码，{poll_interval}秒后重试...")
        logger.debug(f"[{email_addr}] subject={subject!r}, content前200字符={_strip_html(content)[:200]!r}")
        time.sleep(poll_interval)

    print(f"[{email_addr}] 超时未获取到验证码")
    logger.warning(f"[{email_addr}] Outlook API 获取验证码超时")
    return None


@contextmanager
def imap_connection(imap_host, imap_port, email_addr, email_password):
    """
    IMAP 连接上下文管理器，确保连接正确关闭

    Raises:
        IMAPAuthFailed: 认证失败
        IMAPConnectionError: 连接失败
    """
    mail = None
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port, timeout=IMAP_CONNECTION_TIMEOUT_SEC)
        mail.login(email_addr, email_password)
        yield mail
    except imaplib.IMAP4.error as e:
        error_str = str(e)
        if "AUTHENTICATIONFAILED" in error_str.upper():
            raise IMAPAuthFailed(f"IMAP 认证失败: {e}") from e
        else:
            raise IMAPConnectionError(f"IMAP 连接错误: {e}") from e
    except Exception as e:
        raise IMAPConnectionError(f"IMAP 连接失败: {e}") from e
    finally:
        if mail:
            try:
                mail.logout()
            except Exception:
                pass


def get_initial_mail_count(imap_host, imap_port, email_addr, email_password):
    """
    获取邮箱初始邮件数量

    Returns:
        邮件数量，失败返回 0 或 "imap_auth_failed"
    """
    if _is_outlook_address(email_addr):
        logger.info(f"[{email_addr}] Outlook 邮箱使用 API 拉码，跳过 IMAP 初始计数")
        return 0

    def _get_count():
        with imap_connection(imap_host, imap_port, email_addr, email_password) as mail:
            mail.select("INBOX")
            _, messages = mail.search(None, "ALL")
            count = len(messages[0].split()) if messages[0] else 0
            logger.info(f"当前邮件数量: {count}")
            return count

    try:
        return retry_with_backoff(
            _get_count,
            max_retries=IMAP_RETRY_COUNT,
            logger=logger,
            operation_name="获取邮件数量"
        )
    except IMAPAuthFailed as e:
        logger.error(f"IMAP 认证失败: {e}")
        return "imap_auth_failed"
    except Exception as e:
        logger.error(f"获取邮件数量失败: {e}")
        return 0


def get_latest_binance_mail_timestamp(imap_host, imap_port, email_addr, email_password):
    """
    获取最新一封币安邮件的时间戳

    Returns:
        时间戳(float)，没有币安邮件返回 0，失败返回 0 或 "imap_auth_failed"
    """
    if _is_outlook_address(email_addr):
        # Outlook API 模式下返回当前时间，确保只获取之后的邮件
        return time.time()

    def _get_timestamp():
        with imap_connection(imap_host, imap_port, email_addr, email_password) as mail:
            mail.select("INBOX")
            _, messages = mail.search(None, "ALL")
            if not messages[0]:
                return 0

            mail_ids = messages[0].split()
            # 从最新的邮件开始查找币安邮件
            for mail_id in reversed(mail_ids[-20:]):
                _, msg_data = mail.fetch(mail_id, "(RFC822)")
                for response_part in msg_data:
                    if not isinstance(response_part, tuple):
                        continue
                    msg = email.message_from_bytes(response_part[1])
                    from_addr = msg.get("From", "").lower()
                    if "binance" in from_addr:
                        # 获取邮件日期
                        date_str = msg.get("Date", "")
                        if date_str:
                            try:
                                from email.utils import parsedate_to_datetime
                                dt = parsedate_to_datetime(date_str)
                                ts = dt.timestamp()
                                logger.info(f"最新币安邮件时间戳: {ts} ({date_str})")
                                return ts
                            except Exception:
                                pass
                        # 如果无法解析日期，返回当前时间
                        return time.time()
            return 0

    try:
        return retry_with_backoff(
            _get_timestamp,
            max_retries=IMAP_RETRY_COUNT,
            logger=logger,
            operation_name="获取最新邮件时间戳"
        )
    except IMAPAuthFailed as e:
        logger.error(f"IMAP 认证失败: {e}")
        return "imap_auth_failed"
    except Exception as e:
        logger.error(f"获取最新邮件时间戳失败: {e}")
        return 0


def _extract_code_from_message(msg):
    """从 IMAP 邮件对象中提取验证码"""
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

    # 优先用纯文本
    if body.strip():
        code = _extract_6digit_code(body)
        if code:
            return code

    # 再处理 HTML
    if html_body:
        # 先尝试直接在 HTML 中用正则找验证码（避免 strip 后上下文丢失）
        # 繁体/简体"您的验证码"后面紧跟 <strong>XXXXXX
        html_patterns = [
            r'[验驗][证證][码碼][^>]*>[\s\S]{0,300}?<[^>]+>\s*(\d{4,8})\s*<',
            r'<strong[^>]*>\s*(\d{6})\s*</strong>',
            r'color:#f0b90b[^>]*>\s*<strong>\s*(\d{6})\s*</strong>',
        ]
        for pattern in html_patterns:
            m = re.search(pattern, html_body, re.IGNORECASE)
            if m:
                code = m.group(1)
                if code != "000000":
                    return code

        # 剥离 HTML 后再提取
        clean = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_body, flags=re.IGNORECASE)
        clean = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean)
        code = _extract_6digit_code(clean)
        if code:
            return code

    return None


def get_email_verification_code(
    imap_host,
    imap_port,
    username,
    password,
    timeout=IMAP_FETCH_TIMEOUT_SEC,
    initial_count=0,
    consumed_codes=None,
    should_abort=None,
    min_timestamp=0,
):
    """
    获取邮件验证码

    Args:
        min_timestamp: 最小时间戳，只接受比这个时间戳更新的邮件

    Returns:
        验证码字符串，失败返回 None 或 "imap_auth_failed"
    """
    if _is_outlook_address(username):
        logger.info(f"[{username}] 使用 Outlook API 获取验证码")
        return _fetch_outlook_code_via_api(username, password, timeout=timeout, poll_interval=5, should_abort=should_abort)

    logger.info(f"连接 IMAP: {imap_host}:{imap_port}, 用户: {username}")
    logger.info(f"等待新邮件 (初始邮件数: {initial_count}, 最小时间戳: {min_timestamp})...")
    consumed_codes = consumed_codes if consumed_codes is not None else set()

    auth_fail_count = 0
    max_auth_fails = IMAP_RETRY_COUNT

    start_time = time.time()
    while time.time() - start_time < timeout:
        # 检查外部是否要求中止（如页面 URL 已变化）
        if should_abort and should_abort():
            logger.info("外部中止信号，停止等待邮件验证码")
            return "aborted"

        try:
            with imap_connection(imap_host, imap_port, username, password) as mail:
                auth_fail_count = 0
                mail.select("INBOX")

                _, messages = mail.search(None, "ALL")
                if not messages[0]:
                    time.sleep(3)
                    continue

                mail_ids = messages[0].split()
                current_count = len(mail_ids)
                if current_count <= initial_count:
                    logger.info(f"等待新邮件... (当前: {current_count})")
                    time.sleep(3)
                    continue

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

                        # 检查邮件时间戳是否比 min_timestamp 更新
                        if min_timestamp > 0:
                            date_str = msg.get("Date", "")
                            if date_str:
                                try:
                                    from email.utils import parsedate_to_datetime
                                    dt = parsedate_to_datetime(date_str)
                                    mail_ts = dt.timestamp()
                                    if mail_ts <= min_timestamp:
                                        logger.debug(f"跳过旧邮件 (时间戳: {mail_ts} <= {min_timestamp})")
                                        continue
                                except Exception:
                                    pass

                        code = _extract_code_from_message(msg)
                        if code and code not in consumed_codes:
                            consumed_codes.add(code)
                            logger.info(f"找到验证码: {code}")
                            return code

        except IMAPAuthFailed as e:
            auth_fail_count += 1
            logger.warning(f"IMAP 认证失败 ({auth_fail_count}/{max_auth_fails}): {e}")
            if auth_fail_count >= max_auth_fails:
                logger.error(f"IMAP 认证连续失败 {max_auth_fails} 次，邮箱未开启 IMAP 或密码错误")
                return "imap_auth_failed"
            time.sleep(1)
            continue

        except Exception as e:
            logger.error(f"IMAP 错误: {e}")
            time.sleep(3)

        time.sleep(3)

    logger.warning("获取邮件验证码超时")
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
    page.wait_for_timeout(2000)
    initial_url = page.url

    def _check_url_redirect():
        """检测页面是否已跳转离开验证码页面（成功或失败）"""
        try:
            current_url = page.url
            # 成功跳转：进入 stay-signed-in 或用户中心
            if "/login/stay-signed-in" in current_url or "/my/" in current_url or "authcenter" in current_url:
                logger.info(f"[URL变化] 验证成功，页面已跳转: {current_url}")
                return True
            # 检测是否离开了预期的验证码页面
            if expected_url_pattern and expected_url_pattern not in current_url:
                logger.info(f"[URL变化] 页面已跳转: {current_url}")
                return True
            if "/login" in current_url and "/login/mfa" not in current_url and "/login/password" not in current_url:
                if "/login/mfa" in initial_url or "/register/verification" in initial_url:
                    logger.info(f"[URL变化] 从验证页跳转回登录页: {current_url}")
                    return True
        except Exception:
            pass
        return False

    def _dismiss_auth_error_popup():
        try:
            body_text = page.inner_text("body")
            if "认证失败" in body_text:
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
                            logger.info("关闭了认证失败弹窗")
                            page.wait_for_timeout(1000)
                            return True
                    except Exception:
                        pass
        except Exception:
            pass
        return False

    def _click_get_code_button():
        try:
            body_text = page.inner_text("body")
            if "验证码已发送" in body_text or "已发送" in body_text:
                return False

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
                        logger.info("点击了获取验证码按钮")
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
        for selector in code_input_selectors:
            try:
                el = page.query_selector(selector)
                if el and el.is_visible():
                    return el, page
            except Exception:
                pass

        try:
            frames = page.frames
            for frame in frames:
                if frame == page.main_frame:
                    continue
                for selector in code_input_selectors:
                    try:
                        el = frame.query_selector(selector)
                        if el and el.is_visible():
                            logger.info(f"在 iframe 中找到验证码输入框: {frame.url}")
                            return el, frame
                    except Exception:
                        pass
        except Exception:
            pass

        return None, None

    code_input = None
    min_timestamp = 0
    for retry in range(10):
        if _check_url_redirect():
            logger.info("检测到页面跳转，退出邮件验证流程")
            return "url_changed"

        _dismiss_auth_error_popup()

        if retry == 0:
            # 在点击获取验证码按钮前，记录当前最新邮件的时间戳
            min_timestamp = get_latest_binance_mail_timestamp(
                imap_host, imap_port, email_addr, email_password
            )
            if min_timestamp == "imap_auth_failed":
                return "imap_auth_failed"
            logger.info(f"记录当前最新邮件时间戳: {min_timestamp}")
            _click_get_code_button()

        code_input, _ = _find_code_input()
        if code_input:
            logger.info("找到验证码输入框")
            break
        logger.info(f"等待验证码输入框出现... ({retry + 1}/10)")
        page.wait_for_timeout(1500)

    if not code_input:
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
        should_abort=_check_url_redirect,
        min_timestamp=min_timestamp,
    )
    if email_code == "aborted":
        logger.info("等待邮件期间页面 URL 变化，退出邮件验证流程")
        return "url_changed"
    if email_code == "imap_auth_failed":
        return "imap_auth_failed"
    if not email_code:
        logger.warning("未能获取邮件验证码")
        if _check_url_redirect():
            return "url_changed"
        return False

    code_input = None
    for retry in range(3):
        if _check_url_redirect():
            logger.info("等待邮件期间页面跳转，退出邮件验证流程")
            return "url_changed"
        code_input, _ = _find_code_input()
        if code_input:
            break
        page.wait_for_timeout(500)

    if not code_input:
        logger.warning("填充验证码时未找到输入框")
        if _check_url_redirect():
            return "url_changed"
        return False

    logger.info(f"输入验证码: {email_code}")
    code_input.click()
    time.sleep(random.uniform(0.1, 0.2))
    # 先清空输入框，避免重新获取验证码时旧内容残留
    _human_clear_input(code_input, page)
    code_input.type(email_code, delay=random.randint(40, 80))
    page.wait_for_timeout(900)

    if not _submit_mfa(page):
        for _ in range(max(1, mfa_submit_retry)):
            page.wait_for_timeout(500)
            _dismiss_auth_error_popup()
            if _submit_mfa(page):
                break
        else:
            return False

    # 等待页面响应
    page.wait_for_timeout(1500)

    # 检查是否已跳转到成功页面
    if _check_url_redirect():
        logger.info("验证码提交后页面已跳转，验证成功")
        return "url_changed"

    if _dismiss_auth_error_popup():
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
                logger.info(f"点击了按钮: {selector}")
                return True
        except Exception:
            pass

    try:
        page.keyboard.press("Enter")
        logger.info("未找到提交按钮，尝试按回车...")
        return True
    except Exception as e:
        logger.error(f"提交 MFA 失败: {e}")
        return False