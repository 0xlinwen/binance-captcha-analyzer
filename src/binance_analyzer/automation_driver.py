"""登录/注册自动化驱动的最小抽象。"""

from __future__ import annotations

from typing import Any, Protocol

from .login_flow import login_with_url_state
from .register_flow import register_with_url_state
from .results import AccountStatus, AutomationResult


class AutomationDriver(Protocol):
    """在已创建浏览器页面上执行一次登录或注册。"""

    def run(self, page: Any, email: str, password: str, config: dict, *, page_timeout: int) -> AutomationResult: ...


class LoginDriver:
    def run(self, page: Any, email: str, password: str, config: dict, *, page_timeout: int) -> AutomationResult:
        return AutomationResult.from_status(login_with_url_state(page, email, password, config, page_timeout=page_timeout))


class RegisterDriver:
    def run(self, page: Any, email: str, password: str, config: dict, *, page_timeout: int) -> AutomationResult:
        return AutomationResult.from_status(register_with_url_state(page, email, password, config, page_timeout=page_timeout))


def build_driver(mode: str) -> AutomationDriver:
    normalized = mode.strip().lower()
    if normalized == "login":
        return LoginDriver()
    if normalized == "register":
        return RegisterDriver()
    raise ValueError("配置 mode 只支持 login/register")
