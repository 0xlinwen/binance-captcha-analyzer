"""
工具函数模块

提供通用的工具函数，包括重试机制、URL 变化检测、弹窗关闭等。
"""

import time
import random
from typing import Callable, Any, Optional, Tuple
from contextlib import contextmanager
from playwright.sync_api import Page, TimeoutError as PlaywrightTimeout

from .constants import (
    RETRY_BASE_DELAY,
    RETRY_MAX_DELAY,
    RETRY_USE_JITTER,
    URL_CHANGE_TIMEOUT_MS,
)
from .exceptions import is_retryable


# ============================================================================
# 重试机制
# ============================================================================

def exponential_backoff(
    attempt: int,
    base_delay: float = RETRY_BASE_DELAY,
    max_delay: float = RETRY_MAX_DELAY,
    jitter: bool = RETRY_USE_JITTER
) -> float:
    """
    计算指数退避延迟时间

    Args:
        attempt: 当前重试次数（从 0 开始）
        base_delay: 基础延迟时间（秒）
        max_delay: 最大延迟时间（秒）
        jitter: 是否添加随机抖动

    Returns:
        延迟时间（秒）
    """
    delay = min(base_delay * (2 ** attempt), max_delay)

    if jitter:
        # 添加 ±50% 的随机抖动
        delay *= random.uniform(0.5, 1.5)

    return delay


def retry_with_backoff(
    func: Callable[[], Any],
    max_retries: int = 3,
    base_delay: float = RETRY_BASE_DELAY,
    logger: Optional[Any] = None,
    operation_name: str = "操作"
) -> Any:
    """
    使用指数退避策略重试函数

    Args:
        func: 要执行的函数（无参数）
        max_retries: 最大重试次数
        base_delay: 基础延迟时间（秒）
        logger: 日志记录器
        operation_name: 操作名称（用于日志）

    Returns:
        函数执行结果

    Raises:
        最后一次执行的异常
    """
    last_exception = None

    for attempt in range(max_retries):
        try:
            return func()
        except Exception as e:
            last_exception = e

            # 判断是否可重试
            if not is_retryable(e):
                if logger:
                    logger.error(f"{operation_name} 失败（不可重试）: {e}")
                raise

            # 最后一次尝试失败，不再重试
            if attempt >= max_retries - 1:
                if logger:
                    logger.error(f"{operation_name} 失败（已达最大重试次数）: {e}")
                raise

            # 计算延迟时间
            delay = exponential_backoff(attempt, base_delay)

            if logger:
                logger.warning(
                    f"{operation_name} 失败，{delay:.1f}s 后重试 "
                    f"({attempt + 1}/{max_retries}): {e}"
                )

            time.sleep(delay)

    # 理论上不会到达这里
    if last_exception:
        raise last_exception


# ============================================================================
# 浏览器操作工具
# ============================================================================

def dismiss_modal(
    page: Page,
    selector: str,
    timeout_ms: int = 2000,
    logger: Optional[Any] = None
) -> bool:
    """
    关闭页面弹窗

    Args:
        page: Playwright 页面对象
        selector: 关闭按钮的选择器
        timeout_ms: 等待超时时间（毫秒）
        logger: 日志记录器

    Returns:
        True 表示成功关闭，False 表示弹窗不存在或关闭失败
    """
    try:
        close_btn = page.locator(selector)
        if close_btn.is_visible(timeout=timeout_ms):
            close_btn.click()
            if logger:
                logger.info(f"已关闭弹窗: {selector}")
            return True
    except Exception as e:
        if logger:
            logger.debug(f"关闭弹窗失败或弹窗不存在: {e}")

    return False


def dismiss_global_modal(page: Page, logger: Optional[Any] = None) -> bool:
    """
    关闭 Binance 全局弹窗（#globalmodal-common）

    Args:
        page: Playwright 页面对象
        logger: 日志记录器

    Returns:
        True 表示成功关闭，False 表示弹窗不存在或关闭失败
    """
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
                if logger:
                    logger.info(f"关闭全局弹窗: {selector}")
                return True
        except Exception:
            pass

    # Fallback: hide overlay using JavaScript
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
        if logger:
            logger.info("通过注入样式隐藏全局弹窗")
        return True
    except Exception:
        return False


def wait_for_url_change(
    page: Page,
    url_before: str,
    timeout_ms: int = URL_CHANGE_TIMEOUT_MS,
    logger: Optional[Any] = None
) -> Tuple[bool, str]:
    """
    等待页面 URL 变化（使用 Playwright 内置机制，避免轮询）

    Args:
        page: Playwright 页面对象
        url_before: 变化前的 URL
        timeout_ms: 超时时间（毫秒）
        logger: 日志记录器

    Returns:
        (是否变化, 当前 URL)
    """
    try:
        # 使用 Playwright 的 wait_for_url 等待 URL 变化
        page.wait_for_url(lambda url: url != url_before, timeout=timeout_ms)
        url_after = page.url

        if logger:
            logger.info(f"URL 已变化: {url_before} -> {url_after}")

        return True, url_after

    except PlaywrightTimeout:
        url_after = page.url
        if logger:
            logger.debug(f"URL 未变化（超时 {timeout_ms}ms）: {url_after}")
        return False, url_after

    except Exception as e:
        if logger:
            logger.error(f"等待 URL 变化时出错: {e}")
        return False, page.url


# ============================================================================
# 性能监控
# ============================================================================

@contextmanager
def log_step(step_name: str, logger: Optional[Any] = None):
    """
    记录步骤执行时间的上下文管理器

    Args:
        step_name: 步骤名称
        logger: 日志记录器

    Example:
        with log_step("验证码识别", logger):
            solve_captcha()
    """
    start_time = time.time()

    if logger:
        logger.info(f"[开始] {step_name}")

    try:
        yield
    finally:
        duration = time.time() - start_time

        if logger:
            logger.info(f"[完成] {step_name} 耗时: {duration:.2f}s")


# ============================================================================
# 字符串处理
# ============================================================================

def sanitize_filename(filename: str) -> str:
    """
    清理文件名，移除非法字符

    Args:
        filename: 原始文件名

    Returns:
        清理后的文件名
    """
    # 移除或替换非法字符
    illegal_chars = ['<', '>', ':', '"', '/', '\\', '|', '?', '*']
    for char in illegal_chars:
        filename = filename.replace(char, '_')

    return filename


def truncate_string(s: str, max_length: int = 100, suffix: str = "...") -> str:
    """
    截断字符串

    Args:
        s: 原始字符串
        max_length: 最大长度
        suffix: 截断后的后缀

    Returns:
        截断后的字符串
    """
    if len(s) <= max_length:
        return s

    return s[:max_length - len(suffix)] + suffix
