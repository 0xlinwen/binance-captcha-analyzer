"""云端 API 与 Worker 的请求协议。"""

from typing import Literal
from pydantic import BaseModel, Field


class CallbackPayload(BaseModel):
    job_id: str
    job_item_id: int
    account_id: int
    worker_id: str
    status: str
    cookie: str | None = None
    csrftoken: str | None = None
    cookie_expires_at: str | None = None
    credential_updated_at: str | None = None
    error_code: str | None = None
    error_message: str | None = None


class WorkerAccount(BaseModel):
    job_item_id: int
    account_id: int
    email: str
    password: str
    client_id: str | None = None
    refresh_token: str | None = None


class ExecuteLoginPayload(BaseModel):
    job_id: str
    mode: Literal["login", "register"]
    callback_url: str
    accounts: list[WorkerAccount] = Field(min_length=1)
    proxy: dict
