"""账号处理结果模型。

集中定义账号流程的状态值，避免入口层、编排层和流程层散落字符串判断。
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum


class AccountStatus(str, Enum):
    """账号处理状态枚举。

    典型用法:
        if status.should_retry_proxy:
            ...
    """

    SUCCESS = "success"
    FAILED = "failed"
    ALREADY_REGISTERED = "already_registered"
    NEED_REGISTER = "need_register"
    IMAP_AUTH_FAILED = "imap_auth_failed"
    EMAIL_VERIFICATION_REQUIRED = "email_verification_required"
    AUTH_FAILED = "auth_failed"
    RATE_LIMITED = "rate_limited"
    PROXY_FAILED = "proxy_failed"

    @property
    def is_success_like(self) -> bool:
        """返回该状态是否应计入成功类结果。"""
        return self in {self.SUCCESS, self.ALREADY_REGISTERED}

    @property
    def should_retry_proxy(self) -> bool:
        """返回该状态是否表示当前代理会话失败，可由外层换代理重试。"""
        return self in {self.PROXY_FAILED, self.RATE_LIMITED}

    @property
    def is_environment_failure(self) -> bool:
        """返回该状态是否属于代理或网络环境失败，不应消耗账号队列。"""
        return self in {self.PROXY_FAILED, self.RATE_LIMITED}

    @property
    def keeps_account_in_queue(self) -> bool:
        """返回该状态是否不应写失败文件或移除账号队列。"""
        return self in {
            self.PROXY_FAILED,
            self.RATE_LIMITED,
            self.EMAIL_VERIFICATION_REQUIRED,
        }

    @property
    def is_terminal_without_retry(self) -> bool:
        """返回该状态是否不应由入口层继续重试。"""
        return self in {
            self.ALREADY_REGISTERED,
            self.NEED_REGISTER,
            self.IMAP_AUTH_FAILED,
            self.EMAIL_VERIFICATION_REQUIRED,
            self.AUTH_FAILED,
        }


@dataclass(frozen=True)
class AccountResult:
    """单账号处理结果。

    参数:
        email: 账号邮箱。
        password: 账号密码，作为账号复用凭据在队列、结果文件和注册结果 JSON 中完整保留。
        status: 账号处理状态。
    """

    email: str
    password: str
    status: AccountStatus

    def to_process_tuple(self) -> tuple[str, str, AccountStatus]:
        """转换为 ProcessPool 使用的强类型三元组协议。"""
        return self.email, self.password, self.status
