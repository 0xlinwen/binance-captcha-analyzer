"""验证码库的类型定义。"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any


class CaptchaType(str, Enum):
    """支持的验证码类型。"""

    UNKNOWN = "unknown"
    CHECKBOX = "checkbox"
    CLICK = "click"
    SLIDER = "slider"


class CaptchaSolveStatus(str, Enum):
    """验证码求解结果。"""

    PASSED = "passed"
    FAILED = "failed"
    RATE_LIMITED = "rate_limited"
    AUTH_FAILED = "auth_failed"
    NEXT_ROUND = "next_round"


@dataclass(frozen=True)
class CaptchaSolveContext:
    """单次验证码求解所需上下文。"""

    api_key: str
    model: str
    ai_proxy_config: dict[str, Any] | None = None
    email_addr: str = ""
    page_timeout: int = 60000
    click_retry_per_cell: int = 3
