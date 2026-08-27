"""Linux 云端 HTTP API。"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
import json
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi import Header
from pydantic import BaseModel, Field
from typing import Literal

from .database import Database
from ..shared.protocols import CallbackPayload


BASE_DIR = Path(__file__).resolve().parents[3]
_CLOUD_CONFIG_PATH = BASE_DIR / "config" / "cloud.json"
if not _CLOUD_CONFIG_PATH.exists():
    raise FileNotFoundError(f"缺少 Cloud 配置文件: {_CLOUD_CONFIG_PATH}")
_CLOUD_CONFIG = json.loads(_CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
if not isinstance(_CLOUD_CONFIG, dict):
    raise ValueError("Cloud 配置文件必须是 JSON 对象")
for _key in ("database_path", "windows_worker_url", "callback_url", "protocol_version", "task_lease_seconds", "consecutive_failure_limit"):
    if _key not in _CLOUD_CONFIG:
        raise ValueError(f"Cloud 配置缺少 {_key}")
_configured_db_path = Path(_CLOUD_CONFIG["database_path"])
DB_PATH = _configured_db_path if _configured_db_path.is_absolute() else BASE_DIR / _configured_db_path
WINDOWS_WORKER_URL = _CLOUD_CONFIG["windows_worker_url"]
CALLBACK_URL = _CLOUD_CONFIG["callback_url"]
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
CALLBACK_TOKEN = os.getenv("BINANCE_CALLBACK_TOKEN", "")
CONSECUTIVE_FAILURE_LIMIT = _CLOUD_CONFIG["consecutive_failure_limit"]
if not isinstance(CONSECUTIVE_FAILURE_LIMIT, int) or CONSECUTIVE_FAILURE_LIMIT <= 0:
    raise ValueError("Cloud 配置 consecutive_failure_limit 必须是正整数")


def _load_lark_webhook() -> str:
    path = _CLOUD_CONFIG_PATH
    if not path.exists():
        return ""
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise RuntimeError(f"云端配置文件读取失败: {path}: {exc}") from exc
    return str((data.get("lark") or {}).get("webhook_url") or "").strip()


def _notify_lark(message: str) -> None:
    webhook = _load_lark_webhook()
    if not webhook:
        return
    response = requests.post(webhook, json={"msg_type": "text", "content": {"text": message}}, timeout=20)
    response.raise_for_status()


def _notify_once(event_key: str, message: str) -> None:
    if not db.claim_notification_event(event_key):
        return
    try:
        _notify_lark(message)
    except Exception:
        db.release_notification_event(event_key)
        db.record_log("lark_notification_failed", message, level="ERROR")


def _notify_task_group(group_id: str) -> None:
    def send_once(event_key: str, message: str, mark_sent) -> None:
        if not db.claim_notification_event(event_key):
            mark_sent(group_id)
            return
        try:
            _notify_lark(message)
        except Exception:
            db.release_notification_event(event_key)
            raise
        mark_sent(group_id)

    if db.claim_task_group_failure_alert(group_id, CONSECUTIVE_FAILURE_LIMIT):
        group = db.task_group(group_id) or {}
        db.cancel_task_group(group_id)
        send_once(
            f"task-group-failure:{group_id}",
            f"Binance 全局任务连续失败达到 {CONSECUTIVE_FAILURE_LIMIT} 个，任务已停止：任务组 {group_id}，失败数 {group.get('failed_count', 0)}",
            db.mark_task_group_failure_alerted,
        )
    group = db.claim_task_group_completion_notification(group_id)
    if group:
        send_once(
            f"task-group-completion:{group_id}",
            f"Binance 全局任务完成：任务组 {group_id}，总数 {group['total_count']}，成功 {group['success_count']}，失败 {group['failed_count']}",
            db.mark_task_group_completion_notified,
        )
LEASE_SECONDS = int(_CLOUD_CONFIG["task_lease_seconds"])
db = Database(DB_PATH)
app = FastAPI(title="Binance Login Cloud API")


def _cloud_config() -> dict:
    return _CLOUD_CONFIG


def _protocol_version() -> str:
    value = _cloud_config().get("protocol_version")
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Cloud 配置缺少 protocol_version")
    return value.strip()


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
    mode: Literal["login", "register"] = "login"
    accounts: list[AccountIn] = Field(min_length=1)
    proxy: ProxyIn = ProxyIn()
    task_group_id: str | None = None
    idempotency_key: str | None = None


class TaskGroupIn(BaseModel):
    total_count: int = Field(default=0, ge=0)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/api/accounts")
def save_account(account: AccountIn):
    return db.save_account(**account.model_dump())


@app.post("/api/login-jobs")
def create_job(request: JobIn):
    try:
        if not WINDOWS_WORKER_URL or not CALLBACK_URL:
            raise ValueError("config/cloud.json 必须配置 windows_worker_url 和 callback_url")
        job = db.create_job([a.model_dump() for a in request.accounts], request.proxy.model_dump(), task_mode=request.mode, task_group_id=request.task_group_id, idempotency_key=request.idempotency_key)
        db.mark_job_running(job["id"])
        if WINDOWS_WORKER_URL:
            payload = db.worker_payload(job["id"])
            payload["callback_url"] = CALLBACK_URL
            db.mark_items_running(job["id"], "dispatching", LEASE_SECONDS)
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted", "total_count": job["total_count"], "worker_url": WINDOWS_WORKER_URL}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _dispatch_worker(payload: dict) -> None:
    try:
        headers = {"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {}
        health = requests.get(f"{WINDOWS_WORKER_URL.rstrip('/')}/health", timeout=10)
        health.raise_for_status()
        worker_health = health.json()
        if worker_health.get("protocol_version") != _protocol_version():
            group_id = payload.get("task_group_id") or payload.get("job_id")
            _notify_once(f"worker-version:{group_id}", f"Binance Worker 协议版本不兼容：任务 {group_id}，Linux={_protocol_version()}，Windows={worker_health.get('protocol_version') or 'unknown'}")
            raise RuntimeError("Windows Worker 协议版本不兼容")
        worker_id = str(worker_health.get("worker_id") or "")
        if worker_id:
            db.register_worker(worker_id, version=_protocol_version())
        payload["protocol_version"] = _protocol_version()
        response = requests.post(f"{WINDOWS_WORKER_URL.rstrip('/')}/worker/execute-login", json=payload, headers=headers, timeout=30)
        response.raise_for_status()
    except Exception as exc:
        db.dispatch_failed(payload.get("job_id"), str(exc))


def _maintenance_loop() -> None:
    while True:
        try:
            db.recover_expired_items()
            db.mark_offline_workers()
            db.requeue_retryable()
            for group_id in db.pending_task_group_notifications():
                try:
                    _notify_task_group(group_id)
                except Exception as exc:
                    db.record_log("lark_notification_failed", str(exc), job_id=group_id, level="ERROR")
            for job_id in db.pending_jobs():
                if WINDOWS_WORKER_URL:
                    payload = db.worker_payload(job_id)
                    payload["callback_url"] = CALLBACK_URL
                    db.mark_items_running(job_id, "dispatching", LEASE_SECONDS)
                    threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
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


@app.post("/api/task-groups")
def create_task_group(request: TaskGroupIn = TaskGroupIn()):
    return db.create_task_group(request.total_count)


@app.get("/api/task-groups/{group_id}")
def get_task_group(group_id: str):
    value = db.task_group(group_id)
    if not value:
        raise HTTPException(404, "任务组不存在")
    return value


@app.get("/api/login-jobs/{job_id}/status")
def job_status(job_id: str):
    status = db.job_status(job_id)
    if not status:
        raise HTTPException(404, "任务不存在")
    return {"job_id": job_id, "status": status}


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
def callback(payload: CallbackPayload, x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, CALLBACK_TOKEN or WORKER_TOKEN, "Worker")
    try:
        result = db.save_callback(payload.model_dump())
        job = db._one("SELECT task_group_id FROM login_jobs WHERE id=?", (payload.job_id,))
        if job and job.get("task_group_id"):
            try:
                _notify_task_group(job["task_group_id"])
            except Exception as exc:
                db.record_log("lark_notification_failed", str(exc), job_id=job["task_group_id"], level="ERROR")
        return result
    except (KeyError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/accounts/{account_id}/credential")
def credential(account_id: int):
    value = db.credential(account_id)
    if not value:
        raise HTTPException(404, "凭证不存在")
    return value


@app.post("/api/accounts/{account_id}/relogin")
def relogin(account_id: int, proxy: ProxyIn = ProxyIn()):
    try:
        if not WINDOWS_WORKER_URL or not CALLBACK_URL:
            raise ValueError("config/cloud.json 必须配置 windows_worker_url 和 callback_url")
        job = db.create_relogin_job(account_id, proxy.model_dump())
        db.mark_job_running(job["id"])
        if WINDOWS_WORKER_URL:
            payload = db.worker_payload(job["id"])
            payload["callback_url"] = CALLBACK_URL
            db.mark_items_running(job["id"], "dispatching", LEASE_SECONDS)
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted"}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.post("/api/workers/{worker_id}/heartbeat")
def worker_heartbeat(worker_id: str, payload: dict | None = None, x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, WORKER_TOKEN, "Worker")
    payload = payload or {}
    item_id = payload.get("current_job_item_id")
    result = db.heartbeat(worker_id, item_id)
    if item_id is not None:
        db.renew_lease(item_id, worker_id, LEASE_SECONDS)
    return result


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
