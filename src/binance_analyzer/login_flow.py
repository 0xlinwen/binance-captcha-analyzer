"""登录流程状态机。"""

from __future__ import annotations

from . import flows as _shared

globals().update({name: getattr(_shared, name) for name in dir(_shared) if not name.startswith("__")})


def login_with_url_state(page, email_addr, email_password, config, page_timeout=60000):
    # 设置日志
    logger = setup_logger(email_addr)
    start_time = datetime.now()
    short_email = email_addr.split("@")[0]
    console_log(email_addr, "开始登录")
    logger.info(f"开始登录: {email_addr}")

    api_key = config["openrouter_api_key"]
    model = config["models"]
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    login_start_url = config["login"]["start_url"]
    captcha_config = config["captcha"]
    ai_proxy_config = _get_ai_proxy_config(config)
    mfa_config = config["mfa"]

    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
    if initial_mail_count == "imap_auth_failed":
        logger.info("IMAP 认证失败，邮箱未开启 IMAP 或密码错误，无法读取邮件")
        save_failure_log(logger, email_addr)
        return AccountStatus.IMAP_AUTH_FAILED
    logger.info(f"登录URL: {login_start_url}")

    if not goto_with_retry(page, login_start_url, page_timeout=page_timeout):
        logger.error("登录页面加载失败")
        duration = (datetime.now() - start_time).total_seconds()
        log_summary(email_addr, False, duration, stage="页面加载", extra_info="proxy_failed")
        save_failure_log(logger, email_addr)
        return AccountStatus.PROXY_FAILED
    consumed_codes = set()
    mfa_retry_count = 0
    last_stage = "login"  # 跟踪当前阶段
    captcha_fail_count = 0  # 验证码连续失败计数

    # URL 状态计数器
    url_retry_counts = {}  # {url_pattern: count}
    last_url_pattern = None

    for iteration in range(MAX_TOTAL_ITERATIONS):
        try:
            page.wait_for_timeout(1000)  # 固定等待1秒
            url = page.url
            url_pattern = detect_login_url_state(url).value

            # 检查 URL 状态是否变化
            if url_pattern == last_url_pattern:
                url_retry_counts[url_pattern] = url_retry_counts.get(url_pattern, 0) + 1
                if url_retry_counts[url_pattern] >= MAX_URL_RETRIES:
                    logger.warning(f"URL 状态 {url_pattern} 重试次数超过 {MAX_URL_RETRIES} 次，停止")
                    console_log(email_addr, f"{url_pattern} 重试超限", "error")
                    duration = (datetime.now() - start_time).total_seconds()
                    log_summary(email_addr, False, duration, stage=last_stage, iterations=iteration+1, extra_info=f"{url_pattern}超限")
                    save_failure_log(logger, email_addr)
                    return AccountStatus.FAILED
            else:
                # URL 状态变化，重置该状态的计数
                url_retry_counts[url_pattern] = 1
                last_url_pattern = url_pattern

            msg = f"迭代 {iteration + 1} URL状态: {url_pattern} (重试 {url_retry_counts.get(url_pattern, 1)}/{MAX_URL_RETRIES})"
            logger.info(msg)

            # 更新当前阶段
            if "/login/mfa" in url:
                last_stage = "mfa"
            elif "/login/password" in url:
                last_stage = "password"
            elif "/login/stay-signed-in" in url:
                last_stage = "stay-signed-in"
            elif "/login" in url:
                last_stage = "login"

            # 检测浏览器错误页面（通常是代理断开/网络问题），交给外层重建代理和浏览器
            if _is_browser_network_error_url(url):
                logger.warning(f"检测到浏览器错误页面: {url}，停止当前代理会话")
                duration = (datetime.now() - start_time).total_seconds()
                log_summary(email_addr, False, duration, stage=last_stage, iterations=iteration+1, extra_info="proxy_failed")
                save_failure_log(logger, email_addr)
                return AccountStatus.PROXY_FAILED

            if "about:blank" in url:
                logger.warning(f"检测到空白页: {url}，停止当前代理会话")
                return AccountStatus.PROXY_FAILED

            # authcenter/callback 需要刷新才能继续跳转
            if "authcenter/callback" in url:
                logger.info("检测到 authcenter/callback，刷新页面...")
                try:
                    page.reload(timeout=10000)
                except Exception as e:
                    logger.error(f"刷新 authcenter/callback 失败: {e}")
                    return AccountStatus.FAILED
                page.wait_for_timeout(2000)
                continue

            # 登录成功判断（只接受明确登录态页面，避免普通 Binance 页面被误判）
            if _is_logged_in_url(url):
                _ensure_dashboard_page(page, logger=logger, page_timeout=page_timeout)
                duration = (datetime.now() - start_time).total_seconds()
                logger.info("登录成功!")
                log_summary(email_addr, True, duration, stage="dashboard", iterations=iteration+1)
                return AccountStatus.SUCCESS

            # 检测白屏（仅在登录/注册页面检测）
            if _is_page_blank(page, logger):
                console_log(email_addr, "检测到白屏，刷新页面", "warning")
                logger.warning("检测到白屏，刷新页面")
                try:
                    page.reload(timeout=10000)
                except Exception as e:
                    logger.error(f"白屏刷新失败: {e}")
                    return AccountStatus.PROXY_FAILED
                page.wait_for_timeout(2000)
                continue

            has_risk, body_text = _has_risk_error(page, logger)
            if has_risk:
                risk = assess_risk_text(body_text)
                msg = "检测到错误页面!"
                logger.warning(msg)
                logger.info(f"页面内容: {body_text[:500]}")

                # CloudFront 403 等 CDN 层拦截，直接失败不重试
                if risk.is_fatal:
                    console_log(email_addr, "CDN 403 拦截，IP 已被封禁，直接失败", "error")
                    logger.error("CloudFront 403 拦截，停止登录")
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
                        stage=last_stage,
                        iterations=iteration+1,
                        extra_info="auth_failed",
                    )
                    save_failure_log(logger, email_addr)
                    return AccountStatus.AUTH_FAILED

                if risk.is_proxy_failure:
                    console_log(email_addr, "代理连接失败，换新代理重试", "error")
                    logger.error("代理连接失败，停止当前代理会话并换新IP")
                    duration = (datetime.now() - start_time).total_seconds()
                    log_summary(
                        email_addr,
                        False,
                        duration,
                        stage=last_stage,
                        iterations=iteration+1,
                        extra_info="proxy_failed",
                    )
                    save_failure_log(logger, email_addr)
                    return AccountStatus.PROXY_FAILED

                _dismiss_error_popup(page, logger)
                if ("网络连接失败" in body_text or "208075" in body_text) and iteration < 5:
                    msg = "尝试刷新页面..."
                    logger.info(msg)
                    try:
                        page.reload(timeout=10000)
                    except Exception as e:
                        logger.error(f"网络错误页面刷新失败: {e}")
                        return AccountStatus.PROXY_FAILED
                    page.wait_for_timeout(random.randint(2500, 3500))
                    continue
                # 300010 等临时性错误，点击"已知晓"后继续
                if risk.is_retriable:
                    console_log(email_addr, "临时错误，点击已知晓后继续", "warning")
                    logger.warning("临时错误 (RETRIABLE)，继续重试")
                    page.wait_for_timeout(random.randint(2000, 4000))
                    continue
        except Exception as e:
            msg = f"迭代 {iteration + 1} 异常: {e}"
            logger.error(msg)
            import traceback
            stack = traceback.format_exc()
            logger.error(f"堆栈: {stack}")
            continue

        if "/login/stay-signed-in" in url:
            last_stage = "stay-signed-in"
            console_log(email_addr, "stay-signed-in: 点击保持登录")
            logger.info("stay-signed-in - 点击'是'按钮")
            url_before = url

            # 尝试点击按钮
            clicked = click_button(page, ["是", "Yes", "确定", "OK", "保持登录", "Stay signed in"])
            if not clicked:
                logger.error("stay-signed-in 页面未找到确认按钮")
                return AccountStatus.FAILED

            url = _check_url_change(page, url_before, "点击'是'按钮", 1500)
            continue

        if "/login/mfa" in url:
            last_stage = "mfa"
            console_log(email_addr, "mfa: 处理邮件验证码")
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
                    expected_url_pattern="/login/mfa",
                )
                # 处理 IMAP 认证失败
                if ok == "imap_auth_failed":
                    logger.info("IMAP 认证失败，邮箱未开启 IMAP 或密码错误，无法读取邮件")
                    save_failure_log(logger, email_addr)
                    return AccountStatus.IMAP_AUTH_FAILED
                # 处理 URL 跳转的情况
                if ok == "url_changed":
                    logger.info("邮件验证期间检测到 URL 跳转，继续状态机")
                    continue
                if not ok:
                    url_now = page.url
                    if "/my/" in url_now or "authcenter" in url_now or "/login/stay-signed-in" in url_now:
                        logger.info("邮件验证返回失败但页面已跳转，判定成功")
                        continue
                    console_log(email_addr, "邮件验证失败，刷新重试", "warning")
                    logger.warning("邮件验证失败，刷新页面重试")
                    try:
                        page.reload(timeout=10000)
                    except Exception as reload_error:
                        logger.error(f"MFA 刷新失败: {reload_error}")
                        return AccountStatus.FAILED
                    page.wait_for_timeout(2000)
                    continue
            except Exception as e:
                logger.info(f"handle_email_verification 异常: {e}")
                import traceback
                logger.info(f"堆栈: {traceback.format_exc()}")
                url_now = page.url
                if "/my/" in url_now or "authcenter" in url_now or "/login/stay-signed-in" in url_now:
                    logger.info("异常但页面已跳转，判定成功")
                    continue
                return AccountStatus.FAILED

            page.wait_for_timeout(random.randint(2200, 3200))
            url_after = page.url
            if "/my/" in url_after or "/login/stay-signed-in" in url_after:
                mfa_retry_count = 0
                continue

            if url_after == url_before or "/login/mfa" in url_after:
                # Only go to register flow if explicit not-registered signals are present.
                if need_register(page):
                    console_log(email_addr, "检测到账号未注册，跳转注册")
                    logger.info("检测到账号未注册")
                    return AccountStatus.NEED_REGISTER

                mfa_retry_count += 1
                console_log(email_addr, f"MFA重试 #{mfa_retry_count}", "warning")
                logger.info(f"MFA 提交后仍停留在当前页，重试次数: {mfa_retry_count}")
                if mfa_retry_count >= MAX_MFA_RETRIES:
                    has_risk_now, _ = _has_risk_error(page)
                    if has_risk_now:
                        return AccountStatus.RATE_LIMITED
                    return AccountStatus.FAILED
                continue

            mfa_retry_count = 0
            continue

        if "/login/password" in url:
            last_stage = "password"
            console_log(email_addr, "password: 输入密码")
            logger.info("password - 输入密码")
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page)

            try:
                initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
                if initial_mail_count == "imap_auth_failed":
                    logger.info("IMAP 认证失败，邮箱未开启 IMAP 或密码错误，无法读取邮件")
                    save_failure_log(logger, email_addr)
                    return AccountStatus.IMAP_AUTH_FAILED
                logger.info(f"获取初始邮件数: {initial_mail_count}")
            except Exception as e:
                logger.error(f"获取初始邮件数失败: {e}")
                return AccountStatus.FAILED

            try:
                if not input_password(page, get_login_password(email_password)):
                    logger.error("密码输入失败")
                    return AccountStatus.FAILED
                page.wait_for_timeout(random.randint(400, 600))
                if not click_button(page, ["继续", "Continue", "下一步", "Next"]):
                    logger.error("密码页未找到继续按钮")
                    return AccountStatus.FAILED

                # 等待页面响应（URL变化或验证码弹窗）
                response_type, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
                logger.info(f"密码页点击继续后响应类型: {response_type}")

            except Exception as e:
                logger.info(f"输入密码或点击继续失败: {e}")
                import traceback
                logger.info(f"堆栈: {traceback.format_exc()}")
                continue

            captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                page,
                api_key,
                model,
                email_addr=email_addr,
                captcha_config=captcha_config,
                ai_proxy_config=ai_proxy_config,
                reload_url=login_start_url,
                page_timeout=page_timeout,
            )
            should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                captcha_result, captcha_fail_count, email_addr, logger, page=page
            )
            if should_stop:
                return stop_reason
            if captcha_result is CaptchaSolveStatus.FAILED:
                continue

            # 验证码处理后等待URL变化（最多等待3秒）
            changed, url = _wait_for_url_change(page, url, timeout_ms=3000, logger=logger)
            continue

        if "/login" in url and "/login/" not in url:
            url_before = url

            # 检查并点击"已知晓"按钮
            _dismiss_error_popup(page, logger)

            captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                page,
                api_key,
                model,
                email_addr=email_addr,
                captcha_config=captcha_config,
                ai_proxy_config=ai_proxy_config,
                reload_url=login_start_url,
                page_timeout=page_timeout,
            )
            should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                captcha_result, captcha_fail_count, email_addr, logger, page=page
            )
            if should_stop:
                return stop_reason
            if captcha_result is not CaptchaSolveStatus.PASSED:
                if captcha_result is CaptchaSolveStatus.FAILED:
                    continue
                # 验证码处理后等待URL变化
                changed, url = _wait_for_url_change(page, url_before, timeout_ms=3000, logger=logger)
                continue

            # 验证码处理成功后，等待URL变化（最多等待5秒）
            changed, url = _wait_for_url_change(page, url_before, timeout_ms=5000, logger=logger)
            if changed:
                # URL已经变化，跳到下一次迭代处理新的URL状态
                logger.info(f"验证码处理后URL已变化，进入新状态: {url}")
                continue

            email_input = page.query_selector("input[data-e2e='input-username'], input[name='username'], input[name='email']")
            if email_input:
                try:
                    current_value = email_input.input_value()
                    logger.info(f"邮箱输入框当前值: {current_value}")
                except Exception as e:
                    logger.error(f"获取邮箱输入框值失败: {e}")
                    return AccountStatus.FAILED
                if current_value and email_addr in current_value:
                    logger.info("邮箱已输入，点击继续...")
                    if not click_login_continue_strict(page):
                        return AccountStatus.FAILED

                    # 等待页面响应（URL变化或验证码弹窗）
                    response_type, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
                    logger.info(f"邮箱已输入点击继续后响应类型: {response_type}")

                    if _has_auth_failure_error(_get_body_text(page)):
                        if _continue_login_after_auth_failure(page, email_addr, logger):
                            continue
                        console_log(email_addr, "认证失败重试3次仍未通过，停止当前账号", "error")
                        logger.error("平台认证失败，连续点击已知晓并重新登录3次仍未进入下一步")
                        save_failure_log(logger, email_addr)
                        return AccountStatus.AUTH_FAILED

                    # 处理非认证失败类临时弹窗
                    _dismiss_error_popup(page, logger)

                    # 检查页面是否提示未注册
                    if need_register(page):
                        console_log(email_addr, "账号未注册，跳转注册流程")
                        logger.info("页面提示账号未注册，跳转到注册流程")
                        return AccountStatus.NEED_REGISTER

                    if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                        # 可能是验证码错误，继续重试而不是直接判断未注册
                        logger.info("邮箱+验证码完成后未进入密码页，继续重试...")
                    continue

            console_log(email_addr, "login: 输入邮箱")
            last_stage = "login"
            if not input_email(page, email_addr):
                return AccountStatus.FAILED
            page.wait_for_timeout(random.randint(400, 600))
            if not click_login_continue_strict(page):
                return AccountStatus.FAILED

            # 等待页面响应（URL变化或验证码弹窗）
            response_type, url = _wait_for_page_response(page, url_before, timeout_ms=5000, logger=logger)
            logger.info(f"登录页输入邮箱后响应类型: {response_type}")

            if _has_auth_failure_error(_get_body_text(page)):
                if _continue_login_after_auth_failure(page, email_addr, logger):
                    continue
                console_log(email_addr, "认证失败重试3次仍未通过，停止当前账号", "error")
                logger.error("平台认证失败，连续点击已知晓并重新登录3次仍未进入下一步")
                save_failure_log(logger, email_addr)
                return AccountStatus.AUTH_FAILED

            # 处理非认证失败类临时弹窗
            _dismiss_error_popup(page, logger)

            # 检查页面是否提示未注册
            if need_register(page):
                console_log(email_addr, "账号未注册，跳转注册流程")
                logger.info("页面提示账号未注册，跳转到注册流程")
                return AccountStatus.NEED_REGISTER

            if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                # 先再做一次验证码处理
                post_captcha_result = _CAPTCHA_SERVICE.solve_if_present(
                    page,
                    api_key,
                    model,
                    email_addr=email_addr,
                    captcha_config=captcha_config,
                    ai_proxy_config=ai_proxy_config,
                    reload_url=login_start_url,
                    page_timeout=page_timeout,
                )
                should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
                    post_captcha_result, captcha_fail_count, email_addr, logger, page=page
                )
                if should_stop:
                    return stop_reason
                if post_captcha_result is CaptchaSolveStatus.FAILED:
                    continue
                # 验证码处理后等待URL变化
                changed, url = _wait_for_url_change(page, url, timeout_ms=5000, logger=logger)
                if "/login/password" not in url and "/login/mfa" not in url and "/my/" not in url:
                    # 检查是否有明确的未注册提示
                    if need_register(page):
                        console_log(email_addr, "检测到账号未注册，跳转注册")
                        logger.info("检测到账号未注册")
                        return AccountStatus.NEED_REGISTER
                    # 否则继续重试，可能是验证码错误
                    logger.info("验证码可能错误，继续重试...")
                    continue

            if need_register(page):
                console_log(email_addr, "检测到账号未注册，跳转注册")
                logger.info("检测到账号未注册")
                return AccountStatus.NEED_REGISTER
            continue

        if need_register(page):
            console_log(email_addr, "检测到账号未注册，跳转注册")
            logger.info("检测到账号未注册")
            return AccountStatus.NEED_REGISTER

        url_before = url
        captcha_result = _CAPTCHA_SERVICE.solve_if_present(
            page,
            api_key,
            model,
            email_addr=email_addr,
            captcha_config=captcha_config,
            ai_proxy_config=ai_proxy_config,
            reload_url=login_start_url,
            page_timeout=page_timeout,
        )
        should_stop, captcha_fail_count, stop_reason = _handle_captcha_result(
            captcha_result, captcha_fail_count, email_addr, logger, page=page
        )
        if should_stop:
            return stop_reason
        if captcha_result is CaptchaSolveStatus.FAILED:
            continue

        # 验证码处理后等待URL变化（最多等待3秒）
        changed, url = _wait_for_url_change(page, url_before, timeout_ms=3000, logger=logger)

    duration = (datetime.now() - start_time).total_seconds()
    logger.warning("登录流程超过最大总迭代次数")
    log_summary(email_addr, False, duration, stage=last_stage, iterations=MAX_TOTAL_ITERATIONS, extra_info="总迭代超时")
    save_failure_log(logger, email_addr)
    return AccountStatus.FAILED
