"""Linux 侧 Cookie 有效性检查。"""

from __future__ import annotations

from dataclasses import dataclass

import requests


AUTH_CHECK_URL = "https://www.binance.com/bapi/accounts/v1/public/authcenter/auth"


@dataclass(frozen=True)
class CookieCheckResult:
    status: str
    final_url: str = ""
    reason: str = ""


def classify_auth_response(url: str, body_text: str, status_code: int) -> CookieCheckResult:
    if status_code in {401, 403}:
        return CookieCheckResult("expired", url, f"登录态失效 HTTP {status_code}")
    try:
        payload = __import__("json").loads(body_text)
    except (TypeError, ValueError):
        return CookieCheckResult("unknown", url, f"响应不是 JSON HTTP {status_code}")
    if status_code == 200 and payload.get("success") is True and str(payload.get("code")) == "000000":
        return CookieCheckResult("valid", url, "Binance authcenter/auth 返回成功")
    if status_code == 200 and ("code" in payload or "success" in payload):
        return CookieCheckResult("expired", url, f"登录态接口返回失败 code={payload.get('code')}")
    return CookieCheckResult("unknown", url, f"响应未呈现明确状态 HTTP {status_code}")


def check_creator_center_cookie(cookie: str, *, timeout: int = 20, url: str = AUTH_CHECK_URL, proxy: str | None = None) -> CookieCheckResult:
    if not isinstance(cookie, str) or not cookie.strip():
        raise ValueError("Cookie 不能为空")
    session = requests.Session()
    # 默认继承 requests 的 HTTP_PROXY/HTTPS_PROXY/ALL_PROXY；传入 proxy 时覆盖为指定代理。
    if proxy:
        session.proxies.update({"http": proxy, "https": proxy})
    try:
        response = session.post(
            url,
            json={},
            headers={"Cookie": cookie, "User-Agent": "Mozilla/5.0", "Accept": "application/json"},
            timeout=timeout,
            allow_redirects=True,
        )
        return classify_auth_response(response.url, response.text, response.status_code)
    except requests.RequestException as exc:
        return CookieCheckResult("unknown", url, f"请求失败: {exc}")
    finally:
        session.close()
