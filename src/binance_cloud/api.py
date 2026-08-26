"""Linux 云端 HTTP API。"""

from __future__ import annotations

import os
import threading
import requests
from pathlib import Path
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .database import Database


DB_PATH = os.getenv("BINANCE_CLOUD_DB", "data/binance.db")
WINDOWS_WORKER_URL = os.getenv("BINANCE_WINDOWS_WORKER_URL", "")
db = Database(DB_PATH)
app = FastAPI(title="Binance Login Cloud API")


class AccountIn(BaseModel):
    email: str
    password: str
    client_id: str | None = None
    refresh_token: str | None = None


class ProxyIn(BaseModel):
    mode: str = "direct"
    address: str | None = None
    max_accounts_per_job: int | None = Field(default=None, gt=0)


class JobIn(BaseModel):
    accounts: list[AccountIn] = Field(min_length=1)
    proxy: ProxyIn = ProxyIn()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/accounts")
def save_account(account: AccountIn):
    return db.save_account(**account.model_dump())


@app.post("/api/login-jobs")
def create_job(request: JobIn):
    try:
        job = db.create_job([a.model_dump() for a in request.accounts], request.proxy.model_dump())
        db.mark_job_running(job["id"])
        if WINDOWS_WORKER_URL:
            payload = db.worker_payload(job["id"])
            payload["callback_url"] = os.getenv("BINANCE_CALLBACK_URL", "")
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted", "total_count": job["total_count"], "worker_url": WINDOWS_WORKER_URL}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _dispatch_worker(payload: dict) -> None:
    try:
        response = requests.post(f"{WINDOWS_WORKER_URL.rstrip('/')}/worker/execute-login", json=payload, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        db.record_log("worker_dispatch_failed", str(exc), job_id=payload.get("job_id"), level="ERROR")


@app.get("/api/login-jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@app.post("/api/worker/callback")
def callback(payload: dict):
    try:
        return db.save_callback(payload)
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/accounts/{account_id}/credential")
def credential(account_id: int):
    value = db.credential(account_id)
    if not value:
        raise HTTPException(404, "凭证不存在")
    return value


@app.get("/api/login-jobs/{job_id}/logs")
def logs(job_id: str):
    return db.logs(job_id)
