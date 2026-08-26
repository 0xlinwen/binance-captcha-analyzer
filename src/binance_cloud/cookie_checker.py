"""仅验证已有 Binance Cookie 是否能访问创作者中心。

该模块不读取 API Key、昵称或用户名，也不会修改页面资料。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from playwright.sync_api import sync_playwright

from binance_analyzer.browser_context import build_stealth_context, cleanup_subprocess_browser
from binance_analyzer.fingerprint import generate_fingerprint


CREATOR_CENTER_URL = "https://www.binance.com/zh-CN/square/creator-center/home"
CREATOR_MARKERS = ("创作者", "Creator", "查看 API", "创建 API 密钥", "创作内容")
LOGIN_MARKERS = ("登录", "Log In", "Sign in", "验证码")


@dataclass(frozen=True)
class CookieCheckResult:
    status: str
    final_url: str = ""
    reason: str = ""


def _cookie_list(cookie: str) -> list[dict[str, str]]:
    if not isinstance(cookie, str) or not cookie.strip():
        raise ValueError("Cookie 不能为空")
    values = []
    for part in cookie.split(";"):
        name, separator, value = part.strip().partition("=")
        if separator and name:
            values.append({"name": name, "value": value, "domain": ".binance.com", "path": "/"})
    if not values:
        raise ValueError("Cookie 格式无有效 name=value")
    return values


def classify_creator_page(url: str, body_text: str) -> CookieCheckResult:
    normalized_url = str(url or "").lower()
    text = str(body_text or "")
    if "/login" in normalized_url:
        return CookieCheckResult("expired", url, "页面进入登录态")
    if "creator-center" in normalized_url and any(marker.lower() in text.lower() for marker in CREATOR_MARKERS):
        return CookieCheckResult("valid", url, "创作者中心可访问")
    if any(marker.lower() in text.lower() for marker in LOGIN_MARKERS):
        return CookieCheckResult("expired", url, "页面出现登录提示")
    return CookieCheckResult("unknown", url, "页面未呈现明确登录或创作者中心状态")


def check_creator_center_cookie(
    cookie: str,
    *,
    proxy_settings: dict | None = None,
    headless: bool = True,
    page_timeout: int = 60000,
    playwright_factory: Callable[[], Any] = sync_playwright,
) -> CookieCheckResult:
    """注入 Cookie 后访问 Creator Center，仅返回 valid/expired/unknown。"""
    cookies = _cookie_list(cookie)
    browser = context = page = None
    try:
        with playwright_factory() as playwright:
            fingerprint = generate_fingerprint(use_real_profile=False)
            browser, context, page = build_stealth_context(playwright, fingerprint, proxy_settings, headless)
            context.add_cookies(cookies)
            page.goto(CREATOR_CENTER_URL, wait_until="domcontentloaded", timeout=page_timeout)
            page.wait_for_timeout(1500)
            body = page.locator("body").inner_text(timeout=5000)
            return classify_creator_page(page.url, body)
    except ValueError:
        raise
    except Exception as exc:
        return CookieCheckResult("unknown", getattr(page, "url", ""), f"检查环境失败: {exc}")
    finally:
        if browser is not None:
            try:
                cleanup_subprocess_browser(browser)
            except Exception:
                pass
