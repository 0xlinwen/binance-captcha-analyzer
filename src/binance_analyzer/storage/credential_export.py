"""登录/注册成功后的会话凭证导出。"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


@dataclass(frozen=True)
class CredentialSnapshot:
    cookie: str
    csrftoken: str | None
    credential_exported_at: str


def export_credentials(page: Any) -> CredentialSnapshot:
    """从当前已登录 Context 导出完整 Binance Cookie、CSRF 和导出时间。"""
    cookies = page.context.cookies()
    binance_cookies = [c for c in cookies if "binance" in str(c.get("domain", "")).lower()]
    cookie = "; ".join(f"{c['name']}={c['value']}" for c in binance_cookies)
    values = {c["name"]: c["value"] for c in binance_cookies}
    csrftoken = hashlib.md5(values["cr00"].encode()).hexdigest() if values.get("cr00") else values.get("csrftoken")
    return CredentialSnapshot(cookie, csrftoken, datetime.now(timezone.utc).isoformat())
