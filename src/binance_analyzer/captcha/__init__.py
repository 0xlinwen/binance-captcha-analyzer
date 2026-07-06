"""验证码识别与求解库。"""

from .service import CaptchaService
from .types import CaptchaSolveContext, CaptchaSolveStatus, CaptchaType

__all__ = ["CaptchaService", "CaptchaSolveContext", "CaptchaSolveStatus", "CaptchaType"]
