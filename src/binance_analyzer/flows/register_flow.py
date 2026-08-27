"""注册流程状态机。"""

from __future__ import annotations

from . import flows as _shared

globals().update({name: getattr(_shared, name) for name in dir(_shared) if not name.startswith("__")})


REGISTER_SUBMIT_ACK_SIGNATURES = (
    "600010",
    "我们无法处理您的请求，请稍后重试",
    "无法处理您的请求",
    "unable to process your request",
    "208075",
    "认证失败，请刷新页面后重试",
)


def _has_register_submit_ack_error(text: str) -> bool:
    value = str(text or "").lower()
    return any(signature.lower() in value for signature in REGISTER_SUBMIT_ACK_SIGNATURES)


def _has_visible_register_ack_button(page) -> bool:
    selectors = (
        "button:has-text('已知晓')",
        "button:has-text('Got it')",
        "button:has-text('OK')",
        "button:has-text('确定')",
    )
    for selector in selectors:
        try:
            button = page.query_selector(selector)
            if button and button.is_visible():
                return True
        except Exception:
            pass
    return False


def _has_visible_captcha_popup(page) -> bool:
    """检查注册页是否已有验证码弹窗遮挡输入区域。"""
    try:
        popup = page.query_selector(".bcapc-popup")
        return bool(popup and popup.is_visible())
    except Exception:
        return False


def _business_registration_visible(page) -> bool:
    """识别误进入企业注册页，避免把企业表单当作个人注册继续填写。"""
    try:
        url = page.url.lower()
        if "/register/business" in url or "/register/enterprise" in url:
            return True
        element = page.query_selector('input[name="entityname"]:visible')
        return bool(element and element.is_visible())
    except Exception:
        return False


def _return_to_personal_registration(page, logger) -> bool:
    for label in ("个人注册", "注册个人账户", "Sign up as an individual", "Sign up as an individual account"):
        try:
            button = page.get_by_role("button", name=label, exact=True).first
            if button.count() and button.is_visible():
                button.click(timeout=3000)
                page.wait_for_timeout(1200)
                logger.info("已从企业注册页返回个人注册: %s", label)
                return True
        except Exception:
            continue
    return False


def _retry_register_submit_ack_error(page, email_addr: str, logger=None, max_attempts: int = 3) -> bool:
    """处理注册提交后的 600010 等已知晓弹窗，重新提交最多指定次数。"""
    for attempt in range(1, max(1, int(max_attempts)) + 1):
        body_text = _get_body_text(page)
        if not _has_register_submit_ack_error(body_text) and not _has_visible_register_ack_button(page):
            if logger:
                logger.info("注册提交错误弹窗已消失，继续注册状态机")
            return True

        console_log(email_addr, f"注册提交错误，点击已知晓后继续 {attempt}/{max_attempts}", "warning")
        if logger:
            logger.warning(f"注册提交错误，点击已知晓后继续 {attempt}/{max_attempts}")

        if not _dismiss_error_popup(page, logger):
            if logger:
                logger.warning("注册提交错误弹窗未找到已知晓按钮")
            return False

        url_before = str(getattr(page, "url", "") or "")
        if not click_register_continue_strict(page):
            if logger:
                logger.error("注册提交错误弹窗关闭后，注册按钮点击失败")
            return False

        response_type, url_after = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
        if logger:
            logger.info(f"注册提交错误后重新提交响应类型: {response_type}")

        if response_type in ("url_changed", "captcha"):
            return True

        page.wait_for_timeout(random.randint(800, 1200))

    return False


def register_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    # 设置日志
    logger = setup_logger(email_addr)
    start_time = datetime.now()
    logger.info(f"开始注册: {email_addr}")

    api_key = config["openrouter_api_key"]
    model = config["models"]
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    captcha_config = config["captcha"]
    ai_proxy_config = _get_ai_proxy_config(config)
    submit_error_ack_max_attempts = config.get("register", {}).get("submit_error_ack_max_attempts", 3)
    is_email_verification_enabled = config["mfa"].get("email_verification_enabled", True)

    # 添加请求监控（调试风控）—— 用事件监听，不用 route 拦截，避免与缓存路由冲突
    request_log = []
    def log_request(request):
        if "binance.com" in request.url:
            req_info = {
                "time": datetime.now().strftime("%H:%M:%S.%f")[:-3],
                "method": request.method,
                "url": request.url[:100],
            }
            if any(keyword in request.url for keyword in ["precheck", "getCaptcha", "validateCaptcha", "bizCheck", "check/result"]):
                req_info["full_url"] = request.url
                req_info["headers"] = {
                    "user-agent": request.headers.get("user-agent", "")[:50],
                    "referer": request.headers.get("referer", ""),
                    "x-trace-id": request.headers.get("x-trace-id", ""),
                    "clienttype": request.headers.get("clienttype", ""),
                }
            request_log.append(req_info)

    def log_response(response):
        if "binance.com" in response.url and response.status >= 400:
            logger.warning(f"错误响应: {response.status} {response.url[:100]}")
            try:
                body = response.text()
                if "208061" in body or "208075" in body or "frequency" in body.lower():
                    logger.error(f"风控响应内容: {body[:500]}")
            except Exception:
                pass

    def cleanup_listeners():
        """清理页面事件监听器（只移除自己注册的，不动 route）"""
        try:
            page.remove_listener("request", log_request)
            page.remove_listener("response", log_response)
        except Exception:
            pass

    page.on("request", log_request)
    page.on("response", log_response)

    console_log(email_addr, "开始注册")
    logger.info("打开注册页面...")
    if not goto_with_retry(page, "https://accounts.binance.com/zh-CN/register", page_timeout=page_timeout):
        console_log(email_addr, "注册页面加载失败", "error")
        logger.error("注册页面加载失败")
        cleanup_listeners()
        return AccountStatus.PROXY_FAILED

    # 模拟真实用户：页面加载后的自然行为
    try:
        # 等待页面完全加载
        page.wait_for_timeout(random.randint(2000, 4000))

        # 随机移动鼠标（模拟浏览页面，贝塞尔曲线）
        for _ in range(random.randint(3, 6)):
            x = random.randint(200, 1000)
            y = random.randint(200, 700)
            _bezier_mouse_move(page, x, y)
            page.wait_for_timeout(random.randint(300, 800))

        # 随机滚动页面
        scroll_amount = random.randint(-100, 100)
        page.evaluate(f"window.scrollBy(0, {scroll_amount})")
        page.wait_for_timeout(random.randint(500, 1500))

        logger.info("完成页面浏览行为模拟")
    except Exception as e:
        logger.info(f"页面行为模拟异常: {e}")

    initial_mail_count = 0
    consumed_codes = set()
    captcha_fail_count = 0  # 验证码连续失败计数

    # URL 状态计数器
    url_retry_counts = {}  # {url_pattern: count}
    last_url_pattern = None

    for iteration in range(MAX_TOTAL_ITERATIONS):
        # 变速等待：基础随机 + 偶尔长停顿，避免固定节奏
        base_wait = random.randint(1200, 3200)
        if random.random() < 0.15:  # 15% 概率长停顿
            base_wait += random.randint(1500, 4000)
        page.wait_for_timeout(base_wait)
        url = page.url
        url_pattern = detect_register_url_state(url).value

        # 检查 URL 状态是否变化
        if url_pattern == last_url_pattern:
            url_retry_counts[url_pattern] = url_retry_counts.get(url_pattern, 0) + 1
            if url_retry_counts[url_pattern] >= MAX_URL_RETRIES:
                logger.warning(f"URL 状态 {url_pattern} 重试次数超过 {MAX_URL_RETRIES} 次，停止")
                console_log(email_addr, f"{url_pattern} 重试超限", "error")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(email_addr, False, duration, stage="register", iterations=iteration+1, extra_info=f"{url_pattern}超限")
                save_failure_log(logger, email_addr)
                cleanup_listeners()
                return AccountStatus.FAILED
        else:
            # URL 状态变化，重置该状态的计数
            url_retry_counts[url_pattern] = 1
            last_url_pattern = url_pattern

        logger.info(f"注册迭代 {iteration + 1} URL状态: {url_pattern} (重试 {url_retry_counts.get(url_pattern, 1)}/{MAX_URL_RETRIES})")

        if _business_registration_visible(page):
            logger.warning("检测到企业注册页面，尝试返回个人注册")
            if not _return_to_personal_registration(page, logger):
                try:
                    page.goto("https://accounts.binance.com/zh-CN/register", wait_until="domcontentloaded", timeout=page_timeout)
                except Exception:
                    cleanup_listeners()
                    return AccountStatus.FAILED
            continue

        # 检测风控错误（208061/208075 等频率限制）
        has_risk, body_text = _has_risk_error(page, logger)
        if has_risk:
            risk = assess_risk_text(body_text)
            console_log(email_addr, "检测到风控错误", "warning")
            logger.warning(f"风控错误，页面内容: {body_text[:300]}")

            # CloudFront 403 等 CDN 层拦截，直接失败不重试
            if risk.is_fatal:
                console_log(email_addr, "CDN 403 拦截，IP 已被封禁，直接失败", "error")
                logger.error("CloudFront 403 拦截，停止注册")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(email_addr, False, duration, stage="register", extra_info="CDN 403拦截")
                save_failure_log(logger, email_addr)
                cleanup_listeners()
                return AccountStatus.RATE_LIMITED

            if risk.is_auth_failure:
                if _retry_auth_failure_continue(page, email_addr, logger):
                    continue
                console_log(email_addr, "认证失败重试3次仍未通过，停止当前账号", "error")
                logger.error("平台认证失败，连续点击继续3次仍未进入下一步")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(
                    email_addr,
                    False,
                    duration,
                    stage="register",
                    iterations=iteration+1,
                    extra_info="auth_failed",
                )
                save_failure_log(logger, email_addr)
                cleanup_listeners()
                return AccountStatus.AUTH_FAILED

            if risk.is_proxy_failure:
                console_log(email_addr, "代理连接失败，换新代理重试", "error")
                logger.error("代理连接失败，停止当前代理会话并换新IP")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(
                    email_addr,
                    False,
                    duration,
                    stage="register",
                    iterations=iteration+1,
                    extra_info="proxy_failed",
                )
                save_failure_log(logger, email_addr)
                cleanup_listeners()
                return AccountStatus.PROXY_FAILED

            _dismiss_error_popup(page, logger)

            # 对特定错误码进行重试（类似登录流程）
            if ("208075" in body_text or "208061" in body_text or "网络连接失败" in body_text) and iteration < 5:
                console_log(email_addr, "尝试刷新页面重试", "warning")
                logger.warning("临时风控错误，刷新页面重试")
                try:
                    page.reload(timeout=10000)
                except Exception as e:
                    logger.error(f"临时风控刷新失败: {e}")
                    cleanup_listeners()
                    return AccountStatus.PROXY_FAILED
                # 更长的冷却时间
                page.wait_for_timeout(random.randint(5000, 8000))
                continue

            # 300010 等临时性错误，点击"已知晓"后继续（不刷新页面）
            if risk.is_retriable:
                console_log(email_addr, "临时错误，点击已知晓后继续", "warning")
                logger.warning(f"临时错误 (RETRIABLE)，继续重试")
                page.wait_for_timeout(random.randint(2000, 4000))
                continue

            # 其他风控错误才返回失败
            console_log(email_addr, "检测到严重风控错误，停止注册", "error")
            logger.error("严重风控错误，停止注册")

            # 输出最近的请求日志
            logger.error("=== 最近10个请求 ===")
            for req in request_log[-10:]:
                logger.error(f"{req['time']} {req['method']} {req['url']}")
                if 'headers' in req:
                    logger.error(f"  Headers: {req['headers']}")
                if 'full_url' in req:
                    logger.error(f"  Full URL: {req['full_url']}")

            duration = (datetime.now() - start_time).total_seconds()
            log_summary(email_addr, False, duration, stage="register", extra_info="风控限制")
            save_failure_log(logger, email_addr)
            cleanup_listeners()
            return AccountStatus.RATE_LIMITED

        # 检测白屏（类似登录流程）
        if _is_page_blank(page, logger):
            console_log(email_addr, "检测到白屏，刷新页面", "warning")
            logger.warning("检测到白屏，刷新页面")
            try:
                page.reload(timeout=10000)
            except Exception as e:
                logger.error(f"白屏刷新失败: {e}")
                cleanup_listeners()
                return AccountStatus.PROXY_FAILED
            page.wait_for_timeout(2000)
            continue

        # 检测"已存在账户"弹窗（账号已注册）
        try:
            body_text = page.inner_text("body")
            if "已经存在账户" in body_text or "已存在账户" in body_text or "account already exists" in body_text.lower():
                # 尝试关闭弹窗
                close_buttons = [
                    "button:has-text('关闭')",
                    "button:has-text('Close')",
                    "[aria-label='Close']",
                    ".close",
                    "button:has-text('取消')",
                    "button:has-text('Cancel')",
                ]
                for selector in close_buttons:
                    try:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            btn.click()
                            page.wait_for_timeout(500)
                            break
                    except Exception:
                        pass
                console_log(email_addr, "账号已注册", "warning")
                logger.info("检测到账号已注册弹窗")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(email_addr, False, duration, stage="register", extra_info="已注册")
                cleanup_listeners()
                return AccountStatus.ALREADY_REGISTERED
        except Exception:
            pass

        if "/invite" in url:
            console_log(email_addr, "invite: 点击下一步")
            logger.info("invite - 点击下一步")
            url_before = url
            if not click_button(page, ["下一步", "Next", "跳过", "Skip"]):
                logger.error("invite 页面未找到下一步/跳过按钮")
                cleanup_listeners()
                return AccountStatus.FAILED
            # 等待URL变化（最多等待3秒）
            changed, url = _wait_for_url_change(page, url_before, timeout_ms=3000, logger=logger)
            continue

        if "/register/register-set-password" in url or "/register-set-password" in url:
            console_log(email_addr, "set-password: 输入密码")
            logger.info("set-password - 输入密码")
            url_before = url

            # 等待页面稳定，避免导航中 context 被销毁
            try:
                page.wait_for_load_state("domcontentloaded", timeout=5000)
            except Exception as e:
                logger.error(f"密码页加载状态等待失败: {e}")
                cleanup_listeners()
                return AccountStatus.FAILED
            page.wait_for_timeout(random.randint(800, 1500))

            try:
                if not input_password(page, get_login_password(email_password)):
                    logger.error("密码输入失败")
                    cleanup_listeners()
                    return AccountStatus.FAILED
            except Exception as e:
                logger.error(f"输入密码异常: {e}")
                cleanup_listeners()
                return AccountStatus.FAILED

            page.wait_for_timeout(random.randint(400, 600))
            if not click_button(page, ["继续", "Continue", "下一步", "Next"]):
                logger.error("密码页未找到继续按钮")
                cleanup_listeners()
                return AccountStatus.FAILED

            # 等待页面响应（URL变化 或 验证码弹窗）
            response_type, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
            logger.info(f"密码页点击继续后响应类型: {response_type}")

            # 验证码弹窗 → 直接处理
            if response_type == "captcha":
                captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                )
            else:
                # URL变化或超时，也检测一次验证码（可能延迟弹出）
                captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                )

            should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                captcha_result, captcha_fail_count, email_addr, logger, page=page
            )
            if should_stop:
                cleanup_listeners()
                return stop_reason
            if captcha_result is CaptchaSolveStatus.FAILED:
                cooldown = random.randint(8000, 15000)
                console_log(email_addr, f"验证码失败，冷却 {cooldown/1000:.1f}秒", "warning")
                page.wait_for_timeout(cooldown)

            # 验证码处理后等待URL变化（增加到10秒，验证码服务器验证+跳转需要时间）
            changed, url_after = _wait_for_url_change(page, url, timeout_ms=10000, logger=logger)

            # 如果验证码通过但URL仍然是密码页，需要细分原因
            if captcha_result is CaptchaSolveStatus.PASSED and ("/register/register-set-password" in url_after or "/register-set-password" in url_after):
                console_log(email_addr, "验证码通过但页面未跳转，检查原因...", "warning")
                logger.warning("验证码通过但页面停留在密码页，进一步检查")

                # ── 优先检查是否是"已注册"弹窗 ────────────────────────
                page.wait_for_timeout(1500)
                already_reg_selectors = [
                    "text=账号已存在",
                    "text=该邮箱已注册",
                    "text=Email already",
                    "text=already registered",
                    "text=already exists",
                    "text=208001",  # Binance 已注册错误码
                ]
                for sel in already_reg_selectors:
                    try:
                        el = page.query_selector(sel)
                        if el and el.is_visible():
                            console_log(email_addr, "检测到已注册提示，标记为已注册", "warning")
                            logger.info(f"密码页检测到已注册: {sel}")
                            _dismiss_error_popup(page, logger)
                            duration = (datetime.now() - start_time).total_seconds()
                            log_summary(email_addr, False, duration, stage="register", extra_info="已注册")
                            cleanup_listeners()
                            return AccountStatus.ALREADY_REGISTERED
                    except Exception:
                        pass

                # ── 再等 3 秒，给页面跳转最后机会 ────────────────────
                changed2, url_after2 = _wait_for_url_change(page, url_after, timeout_ms=3000, logger=logger)
                if changed2:
                    logger.info(f"延迟跳转成功: {url_after2}")
                    url_after = url_after2
                else:
                    # ── 确认是否真的有风控错误 ───────────────────────
                    has_risk_now, body_text = _has_risk_error(page, logger)
                    if has_risk_now:
                        console_log(email_addr, "确认触发风控，停止注册", "error")
                        logger.error(f"风控错误内容: {body_text[:300]}")
                        duration = (datetime.now() - start_time).total_seconds()
                        log_summary(email_addr, False, duration, stage="register", extra_info="验证码后风控")
                        save_failure_log(logger, email_addr)
                        cleanup_listeners()
                        return AccountStatus.RATE_LIMITED
                    else:
                        # 无风控错误，可能只是页面慢，继续状态机下一轮
                        console_log(email_addr, "无风控错误，继续等待跳转", "warning")
                        logger.warning("密码页未跳转但无风控错误，继续状态机")
                        url_after = page.url

            url = url_after
            continue

        if "/register/verification" in url or "/verification-new-register" in url:
            if not is_email_verification_enabled:
                console_log(email_addr, "已到邮箱验证码页，按配置停止读取邮件", "warning")
                logger.info("邮箱验证码读取已关闭，停止在注册 verification 阶段")
                cleanup_listeners()
                return AccountStatus.EMAIL_VERIFICATION_REQUIRED

            console_log(email_addr, "verification: 处理邮件验证码")
            logger.info("verification - 处理邮件验证码")
            url_before = url
            result = handle_email_verification(
                page,
                imap_host,
                imap_port,
                email_addr,
                email_password,
                initial_mail_count,
                consumed_codes=consumed_codes,
                expected_url_pattern="/register/verification",
            )
            # 处理 IMAP 认证失败
            if result == "imap_auth_failed":
                logger.info("IMAP 认证失败，邮箱未开启 IMAP 或密码错误，无法读取邮件")
                cleanup_listeners()
                save_failure_log(logger, email_addr)
                return AccountStatus.IMAP_AUTH_FAILED
            # 处理 URL 跳转的情况
            if result == "url_changed":
                logger.info("邮件验证期间检测到 URL 跳转，继续状态机")
                url = page.url
                continue
            if not result:
                logger.error("邮件验证失败")
                cleanup_listeners()
                return AccountStatus.FAILED
            # 等待URL变化（最多等待5秒）
            changed, url = _wait_for_url_change(page, url_before, timeout_ms=5000, logger=logger)
            continue

        if "/register" in url and "/register/" not in url:
            console_log(email_addr, "register: 输入邮箱")
            logger.info("register - 输入邮箱")
            url_before = url

            # 模拟真实用户行为：贝塞尔曲线鼠标移动
            try:
                for _ in range(random.randint(2, 4)):
                    x = random.randint(100, 800)
                    y = random.randint(100, 600)
                    _bezier_mouse_move(page, x, y)
                    page.wait_for_timeout(random.randint(200, 500))

                # 随机滚动页面
                page.evaluate(f"window.scrollBy(0, {random.randint(-50, 50)})")
                page.wait_for_timeout(random.randint(300, 800))
            except Exception:
                pass

            if _has_visible_register_ack_button(page):
                if _retry_register_submit_ack_error(page, email_addr, logger, submit_error_ack_max_attempts):
                    continue
                console_log(email_addr, "注册提交确认弹窗处理失败", "error")
                logger.error("上一轮注册提交确认弹窗处理失败")
                cleanup_listeners()
                return AccountStatus.FAILED

            if _has_visible_captcha_popup(page):
                console_log(email_addr, "检测到残留验证码弹窗，先处理验证码")
                logger.info("注册页输入邮箱前检测到验证码弹窗，先处理验证码")
                captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                )
                should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                    captcha_result, captcha_fail_count, email_addr, logger, page=page
                )
                if should_stop:
                    cleanup_listeners()
                    return stop_reason
                if captcha_result is CaptchaSolveStatus.FAILED:
                    cooldown = random.randint(8000, 15000)
                    console_log(email_addr, f"验证码失败，冷却 {cooldown/1000:.1f}秒", "warning")
                    page.wait_for_timeout(cooldown)
                changed, url_after = _wait_for_url_change(page, url, timeout_ms=10000, logger=logger)
                url = url_after
                continue

            if not input_email(page, email_addr):
                cleanup_listeners()
                return AccountStatus.FAILED
            page.wait_for_timeout(random.randint(400, 600))

            # 勾选"创建账户即表示您同意币安"复选框
            if not _tick_agreement_checkbox(page, email_addr, logger):
                cleanup_listeners()
                return AccountStatus.FAILED

            page.wait_for_timeout(random.randint(400, 600))
            if not click_register_continue_strict(page):
                logger.error("注册页未找到继续按钮")
                cleanup_listeners()
                return AccountStatus.FAILED

            # 等待页面响应（URL变化 或 验证码弹窗）
            response_type, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
            logger.info(f"注册页点击继续后响应类型: {response_type}")
            if response_type == "url_changed":
                logger.info(f"注册页提交后已进入新状态: {url}")
                continue

            if _has_register_submit_ack_error(_get_body_text(page)):
                if _retry_register_submit_ack_error(page, email_addr, logger, submit_error_ack_max_attempts):
                    continue
                console_log(email_addr, "注册提交错误连续确认后仍未进入下一步", "error")
                logger.error("注册提交错误连续点击已知晓并重新提交后仍未进入下一步")
                cleanup_listeners()
                return AccountStatus.FAILED

            # 验证码弹窗出现 → 直接处理验证码，不做其他操作
            if response_type == "captcha":
                captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                )
                should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                    captcha_result, captcha_fail_count, email_addr, logger, page=page
                )
                if should_stop:
                    cleanup_listeners()
                    return stop_reason
                if captcha_result is CaptchaSolveStatus.FAILED:
                    cooldown = random.randint(8000, 15000)
                    console_log(email_addr, f"验证码失败，冷却 {cooldown/1000:.1f}秒", "warning")
                    page.wait_for_timeout(cooldown)
                # 验证码处理后等待URL变化
                changed, url_after = _wait_for_url_change(page, url, timeout_ms=10000, logger=logger)
                url = url_after
                continue

            # URL 未变化 → 检查是否因未勾选协议而被拦截
            if response_type == "timeout":
                try:
                    body_text = page.inner_text("body")
                    if "您需同意" in body_text or "需同意" in body_text or "agree to" in body_text.lower():
                        console_log(email_addr, "未勾选协议，重新勾选", "warning")
                        logger.warning("检测到未勾选协议提示，重新勾选")
                        if not _tick_agreement_checkbox(page, email_addr, logger):
                            cleanup_listeners()
                            return AccountStatus.FAILED
                        page.wait_for_timeout(random.randint(500, 800))
                        if not click_register_continue_strict(page):
                            logger.error("重新勾选协议后未找到继续按钮")
                            cleanup_listeners()
                            return AccountStatus.FAILED
                        response_type2, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
                        if response_type2 == "url_changed":
                            logger.info(f"重新勾选协议后已进入新状态: {url}")
                            continue
                        if _has_register_submit_ack_error(_get_body_text(page)):
                            if _retry_register_submit_ack_error(
                                page,
                                email_addr,
                                logger,
                                submit_error_ack_max_attempts,
                            ):
                                continue
                            console_log(email_addr, "注册提交错误连续确认后仍未进入下一步", "error")
                            logger.error("重新勾选协议后注册提交错误连续确认仍未进入下一步")
                            cleanup_listeners()
                            return AccountStatus.FAILED
                        if response_type2 == "captcha":
                            captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                                page,
                                api_key,
                                model,
                                email_addr=email_addr,
                                captcha_config=captcha_config,
                                ai_proxy_config=ai_proxy_config,
                            )
                            should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                                captcha_result, captcha_fail_count, email_addr, logger, page=page
                            )
                            if should_stop:
                                cleanup_listeners()
                                return stop_reason
                            if captcha_result is CaptchaSolveStatus.FAILED:
                                cooldown = random.randint(8000, 15000)
                                console_log(email_addr, f"验证码失败，冷却 {cooldown/1000:.1f}秒", "warning")
                                page.wait_for_timeout(cooldown)
                            changed, url_after = _wait_for_url_change(page, url, timeout_ms=10000, logger=logger)
                            url = url_after
                            continue
                except Exception:
                    pass

                # 检查 300010 等临时性错误弹窗
                try:
                    body_text = page.inner_text("body")
                    risk = assess_risk_text(body_text)
                    if risk.is_retriable:
                        console_log(email_addr, "检测到临时错误弹窗，点击已知晓", "warning")
                        logger.warning(f"检测到临时错误: {[s for s in RETRIABLE_SIGNATURES if s in body_text]}")
                        _dismiss_error_popup(page, logger)
                        page.wait_for_timeout(random.randint(2000, 4000))
                        continue
                except Exception:
                    pass

                # 超时但无特殊情况，尝试一次验证码检测（可能弹窗延迟出现）
                captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                )
                should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                    captcha_result, captcha_fail_count, email_addr, logger, page=page
                )
                if should_stop:
                    cleanup_listeners()
                    return stop_reason
                if captcha_result is CaptchaSolveStatus.FAILED:
                    cooldown = random.randint(8000, 15000)
                    console_log(email_addr, f"验证码失败，冷却 {cooldown/1000:.1f}秒", "warning")
                    page.wait_for_timeout(cooldown)

            # URL 已变化或验证码处理完毕，等待最终跳转
            changed, url_after = _wait_for_url_change(page, url, timeout_ms=10000, logger=logger)
            if captcha_result is CaptchaSolveStatus.PASSED and "/register" in url_after and "/register/" not in url_after:
                console_log(email_addr, "验证码通过但页面未跳转，检查是否风控", "warning")
                logger.warning("验证码通过但页面回到注册页，检查页面内容")

                # 检查页面是否有风控错误
                page.wait_for_timeout(2000)  # 等待错误信息加载
                has_risk_now, body_text = _has_risk_error(page, logger)
                if has_risk_now:
                    if _has_register_submit_ack_error(body_text):
                        if _retry_register_submit_ack_error(
                            page,
                            email_addr,
                            logger,
                            submit_error_ack_max_attempts,
                        ):
                            continue
                        console_log(email_addr, "注册提交错误连续确认后仍未进入下一步", "error")
                        logger.error("验证码后注册提交错误连续确认仍未进入下一步")
                        cleanup_listeners()
                        return AccountStatus.AUTH_FAILED

                    console_log(email_addr, "确认触发风控，停止注册", "error")
                    logger.error(f"风控错误内容: {body_text[:500]}")

                    # 输出最近的请求日志
                    logger.error("=== 最近10个请求 ===")
                    for req in request_log[-10:]:
                        logger.error(f"{req['time']} {req['method']} {req['url']}")
                        if 'headers' in req:
                            logger.error(f"  Headers: {req['headers']}")
                        if 'full_url' in req:
                            logger.error(f"  Full URL: {req['full_url']}")

                    duration = (datetime.now() - start_time).total_seconds()
                    log_summary(email_addr, False, duration, stage="register", extra_info="验证码后风控")
                    save_failure_log(logger, email_addr)
                    cleanup_listeners()
                    return AccountStatus.RATE_LIMITED
                else:
                    if _has_visible_register_ack_button(page):
                        if _retry_register_submit_ack_error(
                            page,
                            email_addr,
                            logger,
                            submit_error_ack_max_attempts,
                        ):
                            continue
                        console_log(email_addr, "注册提交确认弹窗处理失败", "error")
                        logger.error("验证码后注册提交确认弹窗处理失败")
                        cleanup_listeners()
                        return AccountStatus.FAILED

                    # 可能只是页面跳转慢，继续下一轮
                    console_log(email_addr, "未检测到风控错误，继续尝试", "warning")
                    logger.warning("页面未跳转但无风控错误，继续")

            url = url_after
            continue

        if _is_browser_network_error_url(url):
            duration = (datetime.now() - start_time).total_seconds()
            logger.warning(f"检测到浏览器错误页面: {url}，停止当前代理会话")
            log_summary(email_addr, False, duration, stage="register", iterations=iteration+1, extra_info="proxy_failed")
            cleanup_listeners()
            return AccountStatus.PROXY_FAILED

        if _is_logged_in_url(url):
            _ensure_dashboard_page(page, logger=logger, page_timeout=page_timeout)
            duration = (datetime.now() - start_time).total_seconds()
            logger.info("注册成功!")
            log_summary(email_addr, True, duration, stage="dashboard", iterations=iteration+1)
            cleanup_listeners()
            return AccountStatus.SUCCESS

        url_before = url
        captcha_result = _CAPTCHA_SERVICE.solve_if_present(
            page,
            api_key,
            model,
            email_addr=email_addr,
            captcha_config=captcha_config,
            ai_proxy_config=ai_proxy_config,
        )
        should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
            captcha_result, captcha_fail_count, email_addr, logger, page=page
        )
        if should_stop:
            cleanup_listeners()
            return stop_reason
        if captcha_result is CaptchaSolveStatus.FAILED:
            continue

        # 验证码处理后等待URL变化（最多等待3秒）
        changed, url = _wait_for_url_change(page, url_before, timeout_ms=3000, logger=logger)

    duration = (datetime.now() - start_time).total_seconds()
    logger.warning("注册流程超过最大总迭代次数")
    log_summary(email_addr, False, duration, stage="register", iterations=MAX_TOTAL_ITERATIONS, extra_info="总迭代超时")
    save_failure_log(logger, email_addr)
    cleanup_listeners()
    return AccountStatus.FAILED
