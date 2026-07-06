"""验证码求解服务。"""

from __future__ import annotations

import logging
import random
import time

from ..constants import (
    AI_RETRY_COUNT,
    CAPTCHA_STABILITY_CHECK_COUNT,
    CAPTCHA_STABILITY_CHECK_INTERVAL_MS,
    RETRY_BASE_DELAY,
)
from ..utils import dismiss_global_modal, retry_with_backoff
from .ai_client import OpenRouterCaptchaClient
from .detector import detect_captcha_type
from .solvers import CaptchaSolverRegistry, build_default_solver_registry
from .types import CaptchaSolveContext, CaptchaSolveStatus, CaptchaType

logger = logging.getLogger(__name__)

MAX_COOLDOWN_SECONDS = 30


class CaptchaService:
    """验证码检测与求解服务。"""

    def __init__(self, registry: CaptchaSolverRegistry | None = None) -> None:
        self.registry = registry or build_default_solver_registry()

    def solve_if_present(
        self,
        page,
        api_key: str,
        model,
        *,
        email_addr: str = "",
        captcha_config: dict | None = None,
        ai_proxy_config=None,
        reload_url: str | None = None,
        page_timeout: int = 60000,
    ) -> CaptchaSolveStatus:
        """如果页面存在验证码，则执行求解；不存在返回通过状态。"""
        captcha_type, _ = detect_captcha_type(page)
        if captcha_type is CaptchaType.UNKNOWN:
            return CaptchaSolveStatus.PASSED

        print(f"检测到{captcha_type.value}验证码，开始识别...")
        if captcha_config is None:
            raise ValueError("缺少 captcha 配置")
        return self.solve(
            page,
            api_key,
            model,
            ai_proxy_config=ai_proxy_config,
            max_attempts=captcha_config["max_attempts_per_round"],
            email_addr=email_addr,
            max_rounds=captcha_config["max_rounds"],
            reload_url=reload_url,
            page_timeout=page_timeout,
            cooldown_min_sec=captcha_config["cooldown_on_risk_min_sec"],
            cooldown_max_sec=captcha_config["cooldown_on_risk_max_sec"],
            click_retry_per_cell=captcha_config["click_retry_per_cell"],
        )

    def solve(
        self,
        page,
        api_key: str,
        model,
        *,
        ai_proxy_config=None,
        max_attempts: int = 3,
        email_addr: str = "",
        max_rounds: int = 1,
        reload_url: str | None = None,
        page_timeout: int = 60000,
        cooldown_min_sec: int = 5,
        cooldown_max_sec: int = MAX_COOLDOWN_SECONDS,
        click_retry_per_cell: int = 3,
    ) -> CaptchaSolveStatus:
        """执行验证码求解主循环。"""
        observation_delay = random.uniform(0.4, 1.5)
        print(f"[验证码] 观察验证码 {observation_delay:.1f}秒...")
        time.sleep(observation_delay)

        model_candidates = model if isinstance(model, (list, tuple)) else [model]
        model_candidates = [candidate for candidate in model_candidates if candidate]
        if not model_candidates:
            raise ValueError("models 配置为空，无法识别验证码")
        primary_model = model_candidates[0]
        context = CaptchaSolveContext(
            api_key=api_key,
            model=primary_model,
            ai_proxy_config=ai_proxy_config,
            email_addr=email_addr,
            page_timeout=page_timeout,
            click_retry_per_cell=click_retry_per_cell,
        )
        ai_client = OpenRouterCaptchaClient(api_key, primary_model, proxy_config=ai_proxy_config)

        for round_idx in range(max_rounds):
            if round_idx > 0 and reload_url:
                try:
                    print(f"进入第 {round_idx + 1}/{max_rounds} 轮，重开登录页: {reload_url}")
                    page.goto(reload_url, wait_until="domcontentloaded", timeout=page_timeout)
                    page.wait_for_timeout(random.randint(2200, 3000))
                except Exception as exc:
                    print(f"重开登录页失败: {exc}")
                    return CaptchaSolveStatus.FAILED

            for attempt in range(max_attempts):
                print(f"\n--- 验证码轮次 {round_idx + 1}/{max_rounds}，尝试 {attempt + 1}/{max_attempts} ---")
                if attempt > 0:
                    time.sleep(random.uniform(0.5, 1.0))
                dismiss_global_modal(page)

                special_result = self._detect_special_page_result(
                    page,
                    round_idx=round_idx,
                    max_rounds=max_rounds,
                    cooldown_min_sec=cooldown_min_sec,
                    cooldown_max_sec=cooldown_max_sec,
                )
                if special_result:
                    if special_result is CaptchaSolveStatus.NEXT_ROUND:
                        break
                    return special_result

                captcha_type, captcha_element = detect_captcha_type(page)
                if captcha_type is CaptchaType.UNKNOWN:
                    print("未检测到验证码，进行稳定性确认...")
                    if self.captcha_gone_stably(page):
                        print("验证码已稳定消失，判定通过")
                        return CaptchaSolveStatus.PASSED
                    print("验证码可能仍在或短暂重绘，继续尝试")
                    continue

                solver = self.registry.get(captcha_type)
                if not solver:
                    raise ValueError(f"未注册验证码 solver: {captcha_type.value}")

                solved = self._call_solver_with_retry(solver, page, captcha_element, context, ai_client)

                if solved and self.captcha_gone_stably(page):
                    print(f"[验证码] {captcha_type.value} 验证码通过!")
                    return CaptchaSolveStatus.PASSED

                if captcha_type is CaptchaType.CLICK:
                    self._wait_for_click_captcha_reload(page)

                page.wait_for_timeout(random.randint(1000, 1500))

        print("验证码尝试次数已用完")
        return CaptchaSolveStatus.FAILED

    def captcha_gone_stably(
        self,
        page,
        checks: int = CAPTCHA_STABILITY_CHECK_COUNT,
        interval_ms: int = CAPTCHA_STABILITY_CHECK_INTERVAL_MS,
    ) -> bool:
        """多次确认验证码已消失，避免短暂重绘误判。"""
        for index in range(max(1, checks)):
            captcha_type, _ = detect_captcha_type(page)
            if captcha_type is not CaptchaType.UNKNOWN:
                return False
            if index < checks - 1:
                page.wait_for_timeout(interval_ms)
        return True

    def _call_solver_with_retry(self, solver, page, captcha_element, context, ai_client):
        def call_solver():
            return solver.solve(page, captcha_element, context, ai_client)

        return retry_with_backoff(
            call_solver,
            max_retries=AI_RETRY_COUNT,
            base_delay=RETRY_BASE_DELAY,
            logger=logger,
            operation_name="AI 验证码识别",
        )

    def _detect_special_page_result(
        self,
        page,
        *,
        round_idx: int,
        max_rounds: int,
        cooldown_min_sec: int,
        cooldown_max_sec: int,
    ):
        page_text = page.inner_text("body") if page.query_selector("body") else ""
        auth_failure_signatures = ["认证失败，请刷新页面后重试"]
        rate_limit_signatures = [
            "too_many_attempts",
            "尝试次数过多",
            "cap_too_many",
            "cap_too_many_attempts",
            "208075",
            "208061",
            "$e.execute is not a function",
        ]
        if any(signature.lower() in page_text.lower() for signature in auth_failure_signatures):
            print("[WARNING] 检测到平台认证失败签名")
            return CaptchaSolveStatus.AUTH_FAILED
        if any(signature.lower() in page_text.lower() for signature in rate_limit_signatures):
            print("[WARNING] 检测到验证码限流/异常签名")
            cooldown = min(MAX_COOLDOWN_SECONDS, max(0, random.uniform(cooldown_min_sec, cooldown_max_sec)))
            if cooldown > 0:
                print(f"[WARNING] 冷却 {cooldown:.1f} 秒后重试")
                time.sleep(cooldown)
            if round_idx == max_rounds - 1:
                return CaptchaSolveStatus.RATE_LIMITED
            return CaptchaSolveStatus.NEXT_ROUND
        return None

    def _wait_for_click_captcha_reload(self, page) -> None:
        print("[状态] 点击验证码仍存在，等待第二轮验证码加载...")
        page.wait_for_timeout(1000)
        for _index in range(15):
            container = page.query_selector(".bcap-modal, .bcapc-popup")
            if container:
                image = container.query_selector(".bcap-image11")
                if image and image.is_visible():
                    print("[状态] 第二轮验证码已加载")
                    break
            page.wait_for_timeout(200)
        page.wait_for_timeout(500)
