"""云端 API 与 Worker 的请求协议。"""

from typing import Literal
from pydantic import BaseModel, Field


class CallbackPayload(BaseModel):
    job_id: str
    job_item_id: int
    account_id: int
    worker_id: str
    status: str
    lease_id: str | None = None
    proxy_entry_id: str | None = None
    dispatch_sequence: int | None = None
    proxy_profile: str | None = None
    cookie: str | None = None
    csrftoken: str | None = None
    credential_exported_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class WorkerAccount(BaseModel):
    job_item_id: int
    account_id: int
    email: str
    password: str
    client_id: str | None = None
    refresh_token: str | None = None
    lease_id: str | None = None
    proxy_entry_id: str | None = None
    dispatch_sequence: int | None = None


class ExecuteLoginPayload(BaseModel):
    protocol_version: str
    job_id: str
    mode: Literal["login", "register"]
    callback_url: str
    accounts: list[WorkerAccount] = Field(min_length=1)
    proxy: dict
    # 代理租约字段在阶段 1 先兼容可选，阶段 2 接入 Linux SQLite 后改为受约束必填。
    lease_id: str | None = None
    proxy_entry_id: str | None = None
    dispatch_sequence: int | None = None
    proxy_profile: str | None = None
