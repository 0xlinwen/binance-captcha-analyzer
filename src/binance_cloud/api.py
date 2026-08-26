"""Linux 云端 HTTP API。"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi import Header
from pydantic import BaseModel, Field

from .database import Database
from .cookie_checker import check_creator_center_cookie


DB_PATH = os.getenv("BINANCE_CLOUD_DB", "data/binance.db")
WINDOWS_WORKER_URL = os.getenv("BINANCE_WINDOWS_WORKER_URL", "")
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
CALLBACK_TOKEN = os.getenv("BINANCE_CALLBACK_TOKEN", "")
COOKIE_CHECK_URL = os.getenv("BINANCE_COOKIE_CHECK_URL", "https://www.binance.com/zh-CN/my/dashboard")
LEASE_SECONDS = int(os.getenv("BINANCE_TASK_LEASE_SECONDS", "1800"))
COOKIE_CHECK_INTERVAL = int(os.getenv("BINANCE_COOKIE_CHECK_INTERVAL", "900"))
db = Database(DB_PATH)
app = FastAPI(title="Binance Login Cloud API")


def _auth(value: str | None, expected: str, label: str) -> None:
    if expected and value != expected:
        raise HTTPException(401, f"{label} token 无效")


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
            db.mark_items_running(job["id"], "dispatching", LEASE_SECONDS)
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted", "total_count": job["total_count"], "worker_url": WINDOWS_WORKER_URL}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _dispatch_worker(payload: dict) -> None:
    try:
        headers = {"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {}
        response = requests.post(f"{WINDOWS_WORKER_URL.rstrip('/')}/worker/execute-login", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        db.dispatch_failed(payload.get("job_id"), str(exc))


def _maintenance_loop() -> None:
    last_cookie_check = 0.0
    while True:
        try:
            db.recover_expired_items()
            db.mark_offline_workers()
            db.requeue_retryable()
            for job_id in db.pending_jobs():
                if WINDOWS_WORKER_URL:
                    payload = db.worker_payload(job_id)
                    payload["callback_url"] = os.getenv("BINANCE_CALLBACK_URL", "")
                    db.mark_items_running(job_id, "dispatching", LEASE_SECONDS)
                    threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
            if time.monotonic() - last_cookie_check >= COOKIE_CHECK_INTERVAL:
                for credential_row in db.credentials_for_check():
                    _check_cookie(credential_row)
                last_cookie_check = time.monotonic()
        except Exception as exc:
            db.record_log("maintenance_failed", str(exc), level="ERROR")
        time.sleep(60)


@app.on_event("startup")
def start_maintenance() -> None:
    threading.Thread(target=_maintenance_loop, daemon=True, name="cloud-maintenance").start()


@app.get("/api/login-jobs/{job_id}")
def get_job(job_id: str):
    job = db.get_job(job_id)
    if not job:
        raise HTTPException(404, "任务不存在")
    return job


@app.post("/api/login-jobs/{job_id}/cancel")
def cancel_job(job_id: str):
    value = db.cancel_job(job_id)
    if not value:
        raise HTTPException(404, "任务不存在")
    return value


@app.post("/api/database/backup")
def backup_database():
    target = Path(DB_PATH).with_suffix(".backup.db")
    return {"path": str(db.backup(target))}


@app.delete("/api/logs")
def cleanup_logs(days: int = 30):
    if days <= 0:
        raise HTTPException(400, "days 必须为正整数")
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    return {"deleted": db.cleanup_logs(cutoff)}


@app.post("/api/worker/callback")
def callback(payload: dict, x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, CALLBACK_TOKEN or WORKER_TOKEN, "Worker")
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


@app.post("/api/accounts/{account_id}/check-cookie")
def check_cookie(account_id: int):
    value = db.credential(account_id)
    if not value:
        raise HTTPException(404, "凭证不存在")
    return _check_cookie(value)


@app.post("/api/accounts/{account_id}/relogin")
def relogin(account_id: int, proxy: ProxyIn = ProxyIn()):
    try:
        job = db.create_relogin_job(account_id, proxy.model_dump())
        db.mark_job_running(job["id"])
        if WINDOWS_WORKER_URL:
            payload = db.worker_payload(job["id"])
            payload["callback_url"] = os.getenv("BINANCE_CALLBACK_URL", "")
            db.mark_items_running(job["id"], "dispatching", LEASE_SECONDS)
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _check_cookie(value: dict):
    try:
        expires = value.get("cookie_expires_at")
        if expires and expires < datetime.now(timezone.utc).isoformat():
            return db.update_credential_check(value["account_id"], "expired", "cookie_expires_at 已过期")
        result = check_creator_center_cookie(value["cookie"], page_timeout=20000)
        status, error = result.status, result.reason
    except requests.RequestException as exc:
        status, error = "unknown", str(exc)
    except Exception as exc:
        status, error = "unknown", str(exc)
    return db.update_credential_check(value["account_id"], status, error)


@app.post("/api/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, payload: dict | None = None, x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, WORKER_TOKEN, "Worker")
    payload = payload or {}
    return db.heartbeat(worker_id, payload.get("current_job_item_id"))


@app.post("/api/workers/{worker_id}/register")
def worker_register(worker_id: str, payload: dict | None = None, x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, WORKER_TOKEN, "Worker")
    payload = payload or {}
    return db.register_worker(worker_id, payload.get("name", ""), payload.get("version", ""))


@app.get("/api/workers")
def workers():
    return db.workers()


@app.get("/api/login-jobs/{job_id}/logs")
def logs(job_id: str):
    return db.logs(job_id)
