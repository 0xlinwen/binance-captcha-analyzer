"""Linux 侧 Cookie 有效性检查。"""

from __future__ import annotations

from dataclasses import dataclass

import requests


CREATOR_CENTER_URL = "https://www.binance.com/zh-CN/square/creator-center/home"
CREATOR_MARKERS = ("创作者", "Creator", "查看 API", "创建 API 密钥", "创作内容")


@dataclass(frozen=True)
class CookieCheckResult:
    status: str
    final_url: str = ""
    reason: str = ""


def classify_creator_response(url: str, body_text: str, status_code: int) -> CookieCheckResult:
    normalized_url = str(url or "").lower()
    text = str(body_text or "")
    if status_code in {401, 403} or "/login" in normalized_url:
        return CookieCheckResult("expired", url, f"登录态失效 HTTP {status_code}")
    if "creator-center" in normalized_url and any(marker.lower() in text.lower() for marker in CREATOR_MARKERS):
        return CookieCheckResult("valid", url, "Creator Center 可访问")
    return CookieCheckResult("unknown", url, f"响应未呈现明确状态 HTTP {status_code}")


def check_creator_center_cookie(cookie: str, *, timeout: int = 20, url: str = CREATOR_CENTER_URL) -> CookieCheckResult:
    if not isinstance(cookie, str) or not cookie.strip():
        raise ValueError("Cookie 不能为空")
    session = requests.Session()
    session.trust_env = False
    session.proxies.clear()
    try:
        response = session.post(
            url,
            headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Accept": "text/html,application/xhtml+xml"},
            timeout=timeout,
            allow_redirects=True,
        )
        return classify_creator_response(response.url, response.text, response.status_code)
    except requests.RequestException as exc:
        return CookieCheckResult("unknown", url, f"请求失败: {exc}")
    finally:
        session.close()
