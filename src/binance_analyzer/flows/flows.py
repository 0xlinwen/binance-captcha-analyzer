import math
import random
import time
from datetime import datetime

from ..captcha.service import CaptchaService
from ..captcha.types import CaptchaSolveStatus
from ..integrations.email_imap import get_initial_mail_count, get_login_password, handle_email_verification
from ..automation.web_actions import (
    DIALOG_ACTION_TEXTS,
    click_button,
    click_register_continue_strict,
    click_login_continue_strict,
    click_unobscured_button,
    click_without_scroll,
    goto_with_retry,
    input_email,
    input_password,
    is_unobscured_element,
    need_register,
)
from ..runtime.logger import get_logger_manager
from ..utils import wait_for_url_change
from ..constants import (
    MAX_CAPTCHA_FAILS,
)
from .page_signals import (
    DASHBOARD_URL,
    RETRIABLE_SIGNATURES,
    assess_risk_text,
    detect_login_url_state,
    detect_register_url_state,
    has_auth_failure_error,
    has_frequency_limit_error,
    has_already_registered_error,
    has_proxy_failure_error,
    is_browser_network_error_url,
    is_dashboard_url,
    is_logged_in_url,
)
from ..results import AccountStatus


# URL 状态机最大迭代次数
MAX_TOTAL_ITERATIONS = 50
# 单个 URL 状态最大重试次数
MAX_URL_RETRIES = 10
# MFA 最大重试次数
MAX_MFA_RETRIES = 3
_CAPTCHA_SERVICE = CaptchaService()


def _is_browser_network_error_url(url: str) -> bool:
    return is_browser_network_error_url(url)


def _is_logged_in_url(url: str) -> bool:
    return is_logged_in_url(url)


def _is_dashboard_url(url: str) -> bool:
    """判断当前页面是否已经位于 dashboard。"""
    return is_dashboard_url(url)


def _ensure_dashboard_page(page, logger=None, page_timeout=60000) -> bool:
    """确保登录态页面最终停留在 dashboard，返回是否到达目标页面。"""
    current_url = str(getattr(page, "url", "") or "")
    if _is_dashboard_url(current_url):
        return True

    if logger:
        logger.info(f"登录态页面不在 dashboard，跳转到 {DASHBOARD_URL}: {current_url}")

    try:
        page.goto(DASHBOARD_URL, wait_until="domcontentloaded", timeout=page_timeout)
        page.wait_for_timeout(random.randint(2000, 3000))
    except Exception as e:
        if logger:
            logger.warning(f"跳转 dashboard 失败: {e}")
        return False

    return _is_dashboard_url(getattr(page, "url", ""))


def _bezier_mouse_move(page, x_end, y_end, duration_ms=None):
    """
    贝塞尔曲线鼠标移动，模拟真实人类轨迹。
    从当前位置移动到 (x_end, y_end)，带随机弧度和变速。
    """
    # 获取当前鼠标位置；首次移动时初始化一个自然起点。
    try:
        pos = page.evaluate("() => ({x: window._mouseX || 0, y: window._mouseY || 0})")
        x_start, y_start = pos["x"], pos["y"]
        if x_start == 0 and y_start == 0:
            x_start = random.randint(100, 500)
            y_start = random.randint(100, 400)
    except Exception:
        x_start = random.randint(100, 500)
        y_start = random.randint(100, 400)

    dist = math.hypot(x_end - x_start, y_end - y_start)
    if dist < 5:
        return

    if duration_ms is None:
        duration_ms = random.randint(80, 250) + int(dist * random.uniform(0.4, 1.0))

    # 随机控制点，制造弧线
    cx = (x_start + x_end) / 2 + random.uniform(-120, 120)
    cy = (y_start + y_end) / 2 + random.uniform(-120, 120)

    steps = max(8, int(dist / random.uniform(6, 12)))
    step_delay = duration_ms / steps / 1000  # 秒

    for i in range(1, steps + 1):
        t = i / steps
        # 缓入缓出 ease
        t_ease = t * t * (3 - 2 * t)
        bx = (1 - t_ease) ** 2 * x_start + 2 * (1 - t_ease) * t_ease * cx + t_ease ** 2 * x_end
        by = (1 - t_ease) ** 2 * y_start + 2 * (1 - t_ease) * t_ease * cy + t_ease ** 2 * y_end
        # 加微小抖动
        bx += random.uniform(-1.5, 1.5)
        by += random.uniform(-1.5, 1.5)
        page.mouse.move(bx, by)
        time.sleep(step_delay * random.uniform(0.6, 1.4))

    # 记录最终位置
    try:
        page.evaluate(f"() => {{ window._mouseX = {x_end}; window._mouseY = {y_end}; }}")
    except Exception:
        pass


def setup_logger(email_addr):
    """为每个账号设置日志（使用统一的日志管理器）"""
    logger_manager = get_logger_manager()
    return logger_manager.get_account_logger(email_addr)


def save_failure_log(logger, email_addr):
    """失败时记录标记（实际日志保存由 LoggerManager.record_result 处理）"""
    # 新的日志系统在 record_result 时自动保存失败日志
    # 这里只记录一个失败标记到当前 logger
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    logger.info(f"{'='*60}")
    logger.info(f"失败时间: {timestamp}")
    logger.info(f"账号: {email_addr}")
    logger.info(f"状态: 失败")
    logger.info(f"{'='*60}")


def log_summary(email_addr, success, duration_sec, extra_info="", stage="", iterations=0):
    """记录摘要到控制台"""
    status = "成功" if success else "失败"

    parts = [f"[{email_addr}]", status]
    if stage:
        parts.append(f"阶段:{stage}")
    if iterations > 0:
        parts.append(f"迭代:{iterations}")
    parts.append(f"耗时:{duration_sec:.0f}s")
    if extra_info:
        parts.append(extra_info)

    msg = " | ".join(parts)

    # 输出到控制台
    symbol = "✓" if success else "✗"
    print(f"[{symbol}] {msg}")


def console_log(email_addr, message, level="info"):
    """统一的控制台输出格式"""
    short_email = email_addr.split("@")[0]
    prefix = {
        "info": "→",
        "success": "✓",
        "warning": "⚠",
        "error": "✗"
    }.get(level, "→")
    print(f"[{short_email}] {prefix} {message}")


AUTH_FAILURE_MAX_CONTINUE_ATTEMPTS = 3


def _has_proxy_failure_error(text: str) -> bool:
    return has_proxy_failure_error(text)


def _has_auth_failure_error(text: str) -> bool:
    return has_auth_failure_error(text)


def _has_frequency_limit_error(text: str) -> bool:
    return has_frequency_limit_error(text)


def _has_already_registered_error(text: str) -> bool:
    return has_already_registered_error(text)


def _get_body_text(page) -> str:
    try:
        body = page.query_selector("body")
        return body.inner_text() if body else ""
    except Exception:
        try:
            return page.inner_text("body")
        except Exception:
            return ""


def _retry_form_continue(page) -> bool:
    """弹窗关闭后，点击当前页的登录/注册继续按钮以重新提交。"""
    url = str(getattr(page, "url", "") or "")
    if "/register" in url:
        return click_register_continue_strict(page)
    return click_login_continue_strict(page)


def _retry_auth_failure_continue(page, email_addr, logger=None, max_attempts=AUTH_FAILURE_MAX_CONTINUE_ATTEMPTS) -> bool:
    """先关闭认证失败弹窗，再点登录/注册继续重试。"""
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        body_text = _get_body_text(page)
        if not _has_auth_failure_error(body_text):
            return True

        console_log(email_addr, f"认证失败提示，点击继续重试 {attempt}/{max_attempts}", "warning")
        if logger:
            logger.warning(f"认证失败提示，点击继续重试 {attempt}/{max_attempts}")

        url_before = page.url
        clicked = click_unobscured_button(page, DIALOG_ACTION_TEXTS)
        if not clicked:
            clicked = _dismiss_error_popup(page, logger)
        if not clicked and logger:
            logger.warning("认证失败提示未找到可点击按钮")

        if not _retry_form_continue(page):
            if logger:
                logger.error("认证失败提示关闭后，继续按钮点击失败")
            return False

        response_type, _ = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
        body_after = _get_body_text(page)
        if response_type in ("url_changed", "captcha") or not _has_auth_failure_error(body_after):
            if logger:
                logger.info(f"认证失败提示点击后进入下一状态: {response_type}")
            return True

        page.wait_for_timeout(random.randint(800, 1200))

    return False


def _is_login_next_step_url(url: str) -> bool:
    """判断登录邮箱提交后是否进入下一业务状态。"""
    value = str(url or "")
    return "/login/password" in value or "/login/mfa" in value or "/my/" in value


def _continue_login_after_auth_failure(
    page,
    email_addr: str,
    logger=None,
    max_attempts: int = AUTH_FAILURE_MAX_CONTINUE_ATTEMPTS,
) -> bool:
    """处理 208075 认证失败弹窗：点击已知晓后重新提交登录，最多尝试三次。"""
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        current_url = str(getattr(page, "url", "") or "")
        if _is_login_next_step_url(current_url):
            return True

        body_text = _get_body_text(page)
        if not _has_auth_failure_error(body_text):
            if logger:
                logger.warning("认证失败提示已消失，但仍未进入登录下一步")
            return False

        console_log(email_addr, f"认证失败提示，点击已知晓后继续登录 {attempt}/{max_attempts}", "warning")
        if logger:
            logger.warning(f"认证失败提示，点击已知晓后继续登录 {attempt}/{max_attempts}")

        if not _dismiss_error_popup(page, logger):
            if logger:
                logger.warning("认证失败提示未找到已知晓按钮")
            return False

        if not click_login_continue_strict(page):
            if logger:
                logger.error("认证失败提示关闭后，登录按钮点击失败")
            return False

        response_type, url_after = _wait_for_page_response(
            page,
            current_url,
            timeout_ms=5000,
            logger=logger,
        )
        if logger:
            logger.info(f"认证失败后重新提交登录响应类型: {response_type}")

        if _is_login_next_step_url(url_after) or response_type == "captcha":
            return True

        page.wait_for_timeout(random.randint(800, 1200))

    return False


def _get_ai_proxy_config(config):
    ai_proxy_config = config.get("ai_proxy")
    if isinstance(ai_proxy_config, dict) and any(key in ai_proxy_config for key in ("enabled", "bootstrap")):
        return ai_proxy_config
    return config.get("proxy")


def _handle_captcha_result(captcha_result, captcha_fail_count, email_addr, logger, page=None):
    """
    处理验证码结果的统一逻辑

    Returns:
        tuple: (should_stop, new_fail_count, stop_reason)
        - should_stop: 是否应该停止流程
        - new_fail_count: 更新后的失败计数
        - stop_reason: 停止状态
    """
    if captcha_result is CaptchaSolveStatus.RATE_LIMITED:
        console_log(email_addr, "IP被风控，需更换代理", "error")
        logger.error("IP 被风控，请更换代理或等待后重试")
        return True, captcha_fail_count, AccountStatus.RATE_LIMITED

    if captcha_result is CaptchaSolveStatus.AUTH_FAILED:
        if page is not None and _retry_auth_failure_continue(page, email_addr, logger):
            return False, 0, None
        console_log(email_addr, "认证失败，停止当前账号", "error")
        logger.error("平台认证失败，停止当前账号，不按代理连接失败重试")
        return True, captcha_fail_count, AccountStatus.AUTH_FAILED

    if captcha_result is CaptchaSolveStatus.FAILED:
        captcha_fail_count += 1
        console_log(email_addr, f"验证码失败 {captcha_fail_count}/{MAX_CAPTCHA_FAILS}", "warning")
        logger.warning(f"验证码处理失败 ({captcha_fail_count}/{MAX_CAPTCHA_FAILS})")
        if captcha_fail_count >= MAX_CAPTCHA_FAILS:
            logger.error("验证码连续失败次数过多")
            return True, captcha_fail_count, AccountStatus.FAILED
        return False, captcha_fail_count, None

    # 成功，重置计数
    return False, 0, None


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
                    logger.info(f"检测到白屏: text_len={len(text)}, visible_elements={visible_count}")
                return True
        return False
    except Exception as e:
        if logger:
            logger.info(f"_is_page_blank 异常: {e}")
        return False


def _has_risk_error(page, logger=None):
    """检查页面是否有风控错误"""
    try:
        body = page.query_selector("body")
        text = body.inner_text() if body else ""
        assessment = assess_risk_text(text)
        if assessment.has_risk:
            if logger:
                logger.info("检测到风控错误")
        return assessment.has_risk, text
    except Exception as e:
        if logger:
            logger.info(f"_has_risk_error 异常: {e}")
        return False, ""


def _tick_agreement_checkbox(page, email_addr=None, logger=None):
    """
    勾选注册页面的"创建账户即表示您同意币安"复选框。
    币安用自定义组件，复选框是文字左侧的方框图标。
    """
    try:
        # 方法1: 直接用 JS 找到复选框方框并点击
        # 币安的复选框通常是文字旁边的一个小方框（div/span/svg），在包含"创建账户"文字的行内
        clicked = page.evaluate("""() => {
            function isChecked(element) {
                if (!element) return false;
                if (element.checked === true) return true;
                const ariaChecked = element.getAttribute('aria-checked');
                if (ariaChecked === 'true') return true;
                const dataChecked = element.getAttribute('data-checked');
                if (dataChecked === 'true') return true;
                const className = String(element.className || '').toLowerCase();
                return className.includes('checked') && !className.includes('unchecked');
            }

            // 找包含关键文字的元素
            const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
            let textNode = null;
            while (walker.nextNode()) {
                if (walker.currentNode.textContent.includes('创建账户即表示') ||
                    walker.currentNode.textContent.includes('创建个人账户即表示')) {
                    textNode = walker.currentNode;
                    break;
                }
            }
            if (!textNode) return 'not_found';

            // 从文字节点向上找到行容器
            let row = textNode.parentElement;
            for (let i = 0; i < 8 && row; i++) {
                // 在这个容器里找 input[type=checkbox]
                const cb = row.querySelector('input[type="checkbox"]');
                if (cb) {
                    if (isChecked(cb)) return 'already_checked';
                    // 点击 checkbox 本身
                    cb.click();
                    if (cb.checked) return 'input_checked';
                    // 如果 click 没生效，尝试 dispatchEvent
                    cb.dispatchEvent(new MouseEvent('click', {bubbles: true}));
                    return 'input_dispatched';
                }
                // 找 role=checkbox 的元素
                const roleBox = row.querySelector('[role="checkbox"]');
                if (roleBox) {
                    if (isChecked(roleBox)) return 'already_checked';
                    roleBox.click();
                    return 'role_checkbox';
                }
                row = row.parentElement;
            }
            return 'no_checkbox_in_row';
        }""")

        if logger:
            logger.info(f"JS 勾选结果: {clicked}")

        if clicked and clicked not in ('not_found', 'no_checkbox_in_row'):
            if email_addr:
                console_log(email_addr, "勾选创建账户协议")
            page.wait_for_timeout(random.randint(300, 500))
            return True

        # 方法2: Playwright locator 点击复选框左侧的方框
        # 币安页面上复选框方框通常在文字的前一个兄弟元素
        checkbox_selectors = [
            "input[type='checkbox']",
            "[role='checkbox']",
            # 匹配 checkbox 容器 class
            "[class*='bn-checkbox']",
            "[class*='BnCheckbox']",
            "[class*='check-box']",
            "[class*='CheckBox']",
        ]
        for selector in checkbox_selectors:
            try:
                els = page.query_selector_all(selector)
                for el in els:
                    if el.is_visible():
                        try:
                            if el.get_attribute("aria-checked") == "true":
                                if logger:
                                    logger.info(f"协议复选框已勾选: {selector}")
                                return True
                            if el.get_attribute("data-checked") == "true":
                                if logger:
                                    logger.info(f"协议复选框已勾选: {selector}")
                                return True
                            if selector == "input[type='checkbox']" and el.is_checked():
                                if logger:
                                    logger.info(f"协议复选框已勾选: {selector}")
                                return True
                        except Exception:
                            pass
                        el.click()
                        if logger:
                            logger.info(f"Playwright 点击复选框: {selector}")
                        if email_addr:
                            console_log(email_addr, "勾选创建账户协议")
                        page.wait_for_timeout(random.randint(300, 500))
                        return True
            except Exception:
                continue

        # 方法3: 用坐标点击——找到"创建账户"文字，点击其左侧的方框位置
        try:
            text_el = page.query_selector("text=创建账户即表示")
            if not text_el:
                text_el = page.query_selector("text=创建个人账户即表示")
            if text_el:
                box = text_el.bounding_box()
                if box:
                    # 方框在文字左侧，大约 x - 30 的位置，y 居中
                    click_x = box['x'] - 20
                    click_y = box['y'] + box['height'] / 2
                    page.mouse.click(click_x, click_y)
                    if logger:
                        logger.info(f"坐标点击复选框: ({click_x:.0f}, {click_y:.0f})")
                    if email_addr:
                        console_log(email_addr, "勾选创建账户协议")
                    page.wait_for_timeout(random.randint(300, 500))
                    return True
        except Exception as e:
            if logger:
                logger.info(f"坐标点击异常: {e}")

        if logger:
            logger.warning("未找到协议复选框")
        return False
    except Exception as e:
        if logger:
            logger.info(f"勾选复选框异常: {e}")
        return False


def _dismiss_error_popup(page, logger=None):
    """检查并点击普通错误弹窗；频率限制弹窗绝不点击。"""
    try:
        body_text = page.inner_text("body")
        if has_frequency_limit_error(body_text):
            if logger:
                logger.info("检测到频率限制弹窗 (208061)，跳过普通弹窗处理")
            return False
    except Exception:
        pass

    if click_unobscured_button(page, DIALOG_ACTION_TEXTS):
        page.wait_for_timeout(random.randint(800, 1200))
        return True

    dismiss_btns = [
        "button:has-text('OK')",
        "button:has-text('确定')",
        "button:has-text('关闭')",
        "button:has-text('Close')",
        "button:has-text('取消')",
        "button:has-text('Cancel')",
        "button:has-text('Try again')",
        "button:has-text('Retry')",
        "button:has-text('重试')",
        "[aria-label='Close']",
    ]
    for selector in dismiss_btns:
        try:
            buttons = page.query_selector_all(selector)
        except Exception:
            try:
                btn = page.query_selector(selector)
                buttons = [btn] if btn else []
            except Exception as e:
                msg = f"点击按钮 {selector} 失败: {e}"
                if logger:
                    logger.info(msg)
                continue
        if not isinstance(buttons, (list, tuple)):
            continue
        for btn in buttons:
            try:
                if not btn or not btn.is_visible() or not is_unobscured_element(page, btn):
                    continue
                click_without_scroll(page, btn)
                msg = f"点击了关闭按钮: {selector}"
                if logger:
                    logger.info(msg)
                page.wait_for_timeout(random.randint(800, 1200))
                return True
            except Exception as e:
                msg = f"点击按钮 {selector} 失败: {e}"
                if logger:
                    logger.info(msg)
    return False


def _check_url_change(page, url_before, action_name, wait_ms=1000, logger=None):
    """
    检查URL是否变化，用于检测页面跳转（使用统一的 wait_for_url_change）

    Args:
        page: Playwright page对象
        url_before: 操作前的URL
        action_name: 操作名称（用于日志）
        wait_ms: 最大等待时间（毫秒）
        logger: 日志对象

    Returns:
        新的URL
    """
    changed, url_after = wait_for_url_change(page, url_before, timeout_ms=wait_ms, logger=logger)
    if changed and logger:
        logger.info(f"URL变化 {action_name} 后: {url_before} -> {url_after}")
    elif not changed and logger:
        logger.info(f"{action_name} 后URL未变化: {url_before}")
    return url_after


def _wait_for_url_change(page, url_before, timeout_ms=5000, logger=None):
    """
    等待URL发生变化，用于关键操作后的页面跳转检测（使用统一的 wait_for_url_change）

    Args:
        page: Playwright page对象
        url_before: 操作前的URL
        timeout_ms: 最大等待时间（毫秒）
        logger: 日志对象

    Returns:
        tuple: (changed: bool, new_url: str)
    """
    return wait_for_url_change(page, url_before, timeout_ms=timeout_ms, logger=logger)


def _wait_for_page_response(page, url_before, timeout_ms=5000, logger=None):
    """
    等待页面响应：URL变化 或 验证码弹窗出现

    点击按钮后，页面可能：
    1. URL跳转到新页面
    2. 弹出验证码弹窗（URL不变）
    3. 显示错误提示

    Args:
        page: Playwright page对象
        url_before: 操作前的URL
        timeout_ms: 最大等待时间（毫秒）
        logger: 日志对象

    Returns:
        tuple: (response_type, new_url)
        - response_type: "url_changed" / "captcha" / "timeout"
        - new_url: 当前URL
    """
    # 验证码弹窗选择器（含 Binance 专用）
    captcha_selectors = [
        ".bcapc-popup",
        ".bcap-popup",
        ".bcap-modal",
        ".bs-modal",
        ".bs-slide-container",
        ".verify-slider",
        ".bcap-slider",
        ".bcap-drag",
        "#captcha-popup",
        "[data-testid='captcha']",
        ".bds-modal",
    ]

    try:
        poll_interval = 200
        max_polls = timeout_ms // poll_interval

        for i in range(max_polls):
            page.wait_for_timeout(poll_interval)

            # 检查URL变化
            url_after = page.url
            if url_after != url_before:
                if logger:
                    logger.info(f"页面响应: URL变化 ({(i+1)*poll_interval}ms): {url_before} -> {url_after}")
                return "url_changed", url_after

            # 检查验证码弹窗
            for selector in captcha_selectors:
                try:
                    el = page.query_selector(selector)
                    if el and el.is_visible():
                        if logger:
                            logger.info(f"页面响应: 验证码弹窗出现 ({(i+1)*poll_interval}ms): {selector}")
                        return "captcha", url_after
                except Exception:
                    pass

        # 超时后再检查一次
        url_after = page.url
        if url_after != url_before:
            if logger:
                logger.info(f"页面响应: URL在超时边界变化: {url_before} -> {url_after}")
            return "url_changed", url_after

        if logger:
            logger.info(f"页面响应: 等待超时 ({timeout_ms}ms)，无变化")
        return "timeout", url_before

    except Exception as e:
        if logger:
            logger.error(f"_wait_for_page_response 异常: {e}")
        return "timeout", url_before
