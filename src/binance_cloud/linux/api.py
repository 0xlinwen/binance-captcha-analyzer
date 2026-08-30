"""Linux 云端 HTTP API。"""

from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta, timezone
import requests
import json
from threading import RLock
from pathlib import Path
from fastapi import FastAPI, HTTPException
from fastapi import Header
from pydantic import BaseModel, Field
from typing import Literal

from .database import Database
from ..shared.protocols import CallbackPayload
from binance_analyzer.config import DEFAULT_PROXY_POOL_ID, load_proxy_pool


BASE_DIR = Path(__file__).resolve().parents[3]
_CLOUD_CONFIG_PATH = BASE_DIR / "config" / "cloud.json"
if not _CLOUD_CONFIG_PATH.exists():
    raise FileNotFoundError(f"缺少 Cloud 配置文件: {_CLOUD_CONFIG_PATH}")
_CLOUD_CONFIG = json.loads(_CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
if not isinstance(_CLOUD_CONFIG, dict):
    raise ValueError("Cloud 配置文件必须是 JSON 对象")
for _key in ("database_path", "windows_worker_url", "callback_url", "protocol_version", "task_lease_seconds"):
    if _key not in _CLOUD_CONFIG:
        raise ValueError(f"Cloud 配置缺少 {_key}")
_configured_db_path = Path(_CLOUD_CONFIG["database_path"])
DB_PATH = _configured_db_path if _configured_db_path.is_absolute() else BASE_DIR / _configured_db_path
WINDOWS_WORKER_URL = _CLOUD_CONFIG["windows_worker_url"]
CALLBACK_URL = _CLOUD_CONFIG["callback_url"]
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
CALLBACK_TOKEN = os.getenv("BINANCE_CALLBACK_TOKEN", "")
_FAILURE_POLICY = _CLOUD_CONFIG.get("failure_policy")
_POLICY_LOCK = RLock()
if not isinstance(_FAILURE_POLICY, dict):
    raise ValueError("Cloud 配置缺少 failure_policy 对象")
_FIXED_POLICY = _FAILURE_POLICY.get("fixed_pool") or {}
_DYNAMIC_POLICY = _FAILURE_POLICY.get("dynamic") or {}
_DIRECT_POLICY = _FAILURE_POLICY.get("direct") or {}
_STATIC_POLICY = _FAILURE_POLICY.get("static") or {}
SWITCH_AFTER_ACCOUNT_FAILURES = _FIXED_POLICY.get("account_failures_before_switch", 3)
CONSECUTIVE_FAILURE_LIMIT = _FIXED_POLICY.get("failed_ips_before_stop", 5)
DYNAMIC_FAILURE_LIMIT = _DYNAMIC_POLICY.get("account_failures_before_stop", 5)
DIRECT_FAILURE_LIMIT = _DIRECT_POLICY.get("account_failures_before_stop", 5)
STATIC_FAILURE_LIMIT = _STATIC_POLICY.get("account_failures_before_stop", 5)
COOLDOWN_SECONDS = _FIXED_POLICY.get("cooldown_seconds", 86400)
for _name, _value in (("fixed_pool.account_failures_before_switch", SWITCH_AFTER_ACCOUNT_FAILURES), ("fixed_pool.failed_ips_before_stop", CONSECUTIVE_FAILURE_LIMIT), ("dynamic.account_failures_before_stop", DYNAMIC_FAILURE_LIMIT), ("static.account_failures_before_stop", STATIC_FAILURE_LIMIT), ("direct.account_failures_before_stop", DIRECT_FAILURE_LIMIT), ("fixed_pool.cooldown_seconds", COOLDOWN_SECONDS)):
    if isinstance(_value, bool) or not isinstance(_value, int) or _value <= 0:
        raise ValueError(f"Cloud 配置 failure_policy.{_name} 必须是正整数")
if not isinstance(CONSECUTIVE_FAILURE_LIMIT, int) or CONSECUTIVE_FAILURE_LIMIT <= 0:
    raise ValueError("Cloud 配置 stop_after_consecutive_failed_ips 必须是正整数")


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
    if not bool(_FAILURE_POLICY.get("notify_lark", True)):
        return
    webhook = _load_lark_webhook()
    if not webhook:
        raise RuntimeError("已启用 Lark 通知但未配置 lark.webhook_url")
    response = requests.post(webhook, json={"msg_type": "text", "content": {"text": message}}, timeout=20)
    response.raise_for_status()
    # 飞书自定义机器人可能以 HTTP 200 返回业务错误，不能仅凭 HTTP 状态判定送达。
    try:
        result = response.json()
    except (ValueError, AttributeError):
        result = None
    if isinstance(result, dict):
        code = result.get("code", result.get("StatusCode"))
        if code is not None and str(code) not in {"0", "200"}:
            detail = result.get("msg") or result.get("StatusMessage") or result
            raise RuntimeError(f"Lark Webhook 业务失败: {detail}")


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

    group = db.task_group(group_id) or {}
    modes = {str(job.get("proxy_mode") or "direct") for job in group.get("jobs", [])}
    failure_mode = next(iter(modes)) if len(modes) == 1 else "direct"
    threshold = {"rotating_single_ip": CONSECUTIVE_FAILURE_LIMIT, "dynamic": DYNAMIC_FAILURE_LIMIT, "fixed": STATIC_FAILURE_LIMIT, "direct": DIRECT_FAILURE_LIMIT}.get(failure_mode, DIRECT_FAILURE_LIMIT)
    if db.claim_task_group_failure_alert(group_id, threshold, "fixed_pool" if failure_mode == "rotating_single_ip" else failure_mode):
        db.cancel_task_group(group_id)
        send_once(
            f"task-group-failure:{group_id}",
            f"Binance 任务连续失败达到阈值，任务已停止：任务组 {group_id}，模式 {failure_mode}，阈值 {threshold}，失败数 {group.get('failed_count', 0)}",
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


def _initialize_proxy_pool() -> None:
    config_value = _CLOUD_CONFIG.get("proxy_config_path")
    if not config_value:
        return
    path = Path(config_value)
    if not path.is_absolute():
        path = BASE_DIR / path
    if not path.exists():
        raise FileNotFoundError(f"代理配置文件不存在: {path}")
    document = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(document, dict):
        raise ValueError("代理配置文件必须是 JSON 对象")
    pool = load_proxy_pool(BASE_DIR, document)
    db.configure_proxy_pool(DEFAULT_PROXY_POOL_ID, pool["addresses"], switch_threshold=SWITCH_AFTER_ACCOUNT_FAILURES, allow_parallel=pool["allow_parallel"], cooldown_seconds=COOLDOWN_SECONDS, stop_after_consecutive_failed_ips=CONSECUTIVE_FAILURE_LIMIT)


_initialize_proxy_pool()
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
    mode: Literal["direct", "fixed", "dynamic", "rotating_single_ip"] = "direct"
    proxy_profile: str | None = None
    address: str | None = None
    max_accounts_per_job: int | None = Field(default=None, gt=0)


class JobIn(BaseModel):
    mode: Literal["login", "register"] = "login"
    accounts: list[AccountIn] = Field(min_length=1)
    proxy: ProxyIn = ProxyIn()
    task_group_id: str | None = None
    idempotency_key: str | None = None


class FailedItemsRetryIn(BaseModel):
    job_item_ids: list[int] | None = Field(default=None, min_length=1)
    proxy: ProxyIn | None = None
    idempotency_key: str | None = None


class TaskGroupIn(BaseModel):
    total_count: int = Field(default=0, ge=0)


class ProxyPolicyIn(BaseModel):
    fixed_pool: dict = Field(default_factory=dict)
    dynamic: dict = Field(default_factory=dict)
    direct: dict = Field(default_factory=dict)


def _persist_failure_policy(policy: dict) -> None:
    document = json.loads(_CLOUD_CONFIG_PATH.read_text(encoding="utf-8"))
    document["failure_policy"] = policy
    temporary = _CLOUD_CONFIG_PATH.with_suffix(".tmp")
    temporary.write_text(json.dumps(document, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(_CLOUD_CONFIG_PATH)


def _current_failure_policy() -> dict:
    with _POLICY_LOCK:
        return json.loads(json.dumps(_FAILURE_POLICY))


@app.get("/health")
def health():
    return {"status": "ok"}


@app.patch("/api/failure-policy/{mode}")
def patch_failure_policy(mode: str, request: dict, x_worker_token: str | None = Header(default=None)):
    global SWITCH_AFTER_ACCOUNT_FAILURES, CONSECUTIVE_FAILURE_LIMIT, COOLDOWN_SECONDS, DYNAMIC_FAILURE_LIMIT, STATIC_FAILURE_LIMIT, DIRECT_FAILURE_LIMIT, _FAILURE_POLICY
    _auth(x_worker_token, WORKER_TOKEN, "Cloud")
    if mode == "notification":
        if set(request) != {"notify_lark"} or not isinstance(request["notify_lark"], bool):
            raise HTTPException(400, "notification 只支持布尔字段 notify_lark")
        with _POLICY_LOCK:
            policy_doc = _current_failure_policy()
            policy_doc["notify_lark"] = request["notify_lark"]
            old_text = _CLOUD_CONFIG_PATH.read_text(encoding="utf-8")
            try:
                _persist_failure_policy(policy_doc)
                _FAILURE_POLICY = policy_doc
            except Exception:
                _CLOUD_CONFIG_PATH.write_text(old_text, encoding="utf-8")
                raise
            return {"failure_policy": policy_doc}
    if mode not in {"fixed_pool", "dynamic", "static", "direct"}:
        raise HTTPException(404, "不支持的失败策略模式")
    allowed = {
        "fixed_pool": {"account_failures_before_switch", "failed_ips_before_stop", "cooldown_seconds"},
        "dynamic": {"account_failures_before_stop"},
        "static": {"account_failures_before_stop"},
        "direct": {"account_failures_before_stop"},
    }[mode]
    if not request or set(request) - allowed:
        raise HTTPException(400, f"{mode} 只支持字段: {', '.join(sorted(allowed))}")
    with _POLICY_LOCK:
        policy_doc = _current_failure_policy()
        section = dict(policy_doc.get(mode) or {})
        section.update(request)
    for key, value in section.items():
        if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
            raise HTTPException(400, f"failure_policy.{mode}.{key} 必须是正整数")
    policy_doc[mode] = section
    try:
        old_pool = db._one("SELECT * FROM proxy_pools WHERE id=?", (DEFAULT_PROXY_POOL_ID,)) if mode == "fixed_pool" else None
        if mode == "fixed_pool":
            row = db.update_proxy_pool_policy(DEFAULT_PROXY_POOL_ID, switch_threshold=section.get("account_failures_before_switch", SWITCH_AFTER_ACCOUNT_FAILURES), stop_after_consecutive_failed_ips=section.get("failed_ips_before_stop", CONSECUTIVE_FAILURE_LIMIT), cooldown_seconds=section.get("cooldown_seconds", COOLDOWN_SECONDS))
            SWITCH_AFTER_ACCOUNT_FAILURES, CONSECUTIVE_FAILURE_LIMIT, COOLDOWN_SECONDS = row["switch_threshold"], row["stop_after_consecutive_failed_ips"], row["cooldown_seconds"]
        elif mode == "dynamic":
            DYNAMIC_FAILURE_LIMIT = section["account_failures_before_stop"]
        elif mode == "static":
            STATIC_FAILURE_LIMIT = section["account_failures_before_stop"]
        else:
            DIRECT_FAILURE_LIMIT = section["account_failures_before_stop"]
        old_text = _CLOUD_CONFIG_PATH.read_text(encoding="utf-8")
        try:
            _persist_failure_policy(policy_doc)
        except Exception:
            if old_pool:
                db.update_proxy_pool_policy(DEFAULT_PROXY_POOL_ID, switch_threshold=old_pool["switch_threshold"], stop_after_consecutive_failed_ips=old_pool["stop_after_consecutive_failed_ips"], cooldown_seconds=old_pool["cooldown_seconds"])
            raise
        _FAILURE_POLICY = policy_doc
        db.record_log("failure_policy_updated", f"mode={mode} fields={','.join(sorted(request))}")
        return {"failure_policy": policy_doc}
    except (OSError, ValueError, KeyError) as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/failure-policy")
def get_failure_policy(x_worker_token: str | None = Header(default=None)):
    _auth(x_worker_token, WORKER_TOKEN, "Cloud")
    return {"failure_policy": _current_failure_policy()}


@app.post("/api/proxy-pools/{pool_id}/policy")
def update_proxy_policy(pool_id: str, request: ProxyPolicyIn, x_worker_token: str | None = Header(default=None)):
    raise HTTPException(410, "该接口已弃用，请使用 PATCH /api/failure-policy/{mode} 按模式更新")


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
            worker_id = "dispatching"
            if request.proxy.mode == "rotating_single_ip":
                payload = db.rotating_worker_payload(job["id"], worker_id, LEASE_SECONDS, pool_id=DEFAULT_PROXY_POOL_ID)
                if payload is None:
                    db.mark_pool_exhausted(job["id"])
                    _notify_once(f"proxy-pool-exhausted:{job['id']}", f"Binance 固定代理池耗尽，任务已停止：{job['id']}")
                    raise ValueError("固定代理池没有可用 IP，任务已终止")
            else:
                payload = db.next_worker_payload(job["id"], worker_id, LEASE_SECONDS)
                if payload is None:
                    raise ValueError("任务没有可派发账号")
            payload["callback_url"] = CALLBACK_URL
            threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {"job_id": job["id"], "status": "submitted", "total_count": job["total_count"], "worker_url": WINDOWS_WORKER_URL}
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


@app.get("/api/login-jobs/{job_id}/failed-items")
def failed_items(job_id: str):
    try:
        return {"job_id": job_id, "items": db.failed_items(job_id)}
    except ValueError as exc:
        raise HTTPException(404, str(exc)) from exc


@app.post("/api/login-jobs/{job_id}/retry-failed")
def retry_failed_items(job_id: str, request: FailedItemsRetryIn):
    """基于源任务的失败账号快照创建独立重派任务，绝不在响应中返回凭据。"""
    try:
        if not WINDOWS_WORKER_URL or not CALLBACK_URL:
            raise ValueError("config/cloud.json 必须配置 windows_worker_url 和 callback_url")
        override_proxy = request.proxy.model_dump() if request.proxy is not None else None
        job = db.create_failed_items_retry_job(
            job_id,
            request.job_item_ids,
            override_proxy,
            request.idempotency_key,
        )
        db.mark_job_running(job["id"])
        if job["proxy_mode"] == "rotating_single_ip":
            payload = db.rotating_worker_payload(job["id"], "dispatching", LEASE_SECONDS, pool_id=DEFAULT_PROXY_POOL_ID)
            if payload is None:
                db.mark_pool_exhausted(job["id"])
                _notify_once(f"proxy-pool-exhausted:{job['id']}", f"Binance 固定代理池耗尽，重派任务已停止：{job['id']}")
                raise ValueError("固定代理池没有可用 IP，重派任务已终止")
        else:
            payload = db.next_worker_payload(job["id"], "dispatching", LEASE_SECONDS)
            if payload is None:
                raise ValueError("重派任务没有可派发账号")
        payload["callback_url"] = CALLBACK_URL
        threading.Thread(target=_dispatch_worker, args=(payload,), daemon=True).start()
        return {
            "source_job_id": job_id,
            "job_id": job["id"],
            "status": "submitted",
            "total_count": job["total_count"],
            "worker_url": WINDOWS_WORKER_URL,
        }
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc


def _dispatch_worker(payload: dict) -> None:
    targeted_dispatch_failure = False
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
            if worker_health.get("worker_max_workers") is not None:
                db.set_worker_capacity(worker_id, int(worker_health["worker_max_workers"]))
        payload["protocol_version"] = _protocol_version()
        payloads = [payload]
        if payload.get("proxy_profile") == "rotating_single_ip":
            capacity = int(worker_health.get("worker_max_workers", 1))
            state = db.proxy_pool_state(DEFAULT_PROXY_POOL_ID)
            node = db._one("SELECT active_slots,capacity FROM worker_nodes WHERE id=?", (worker_id,)) if worker_id else None
            available_slots = max(1, capacity - int((node or {}).get("active_slots", 0)))
            slots = available_slots if state["allow_parallel"] else 1
            for _ in range(max(0, slots - 1)):
                extra = db.rotating_worker_payload(payload["job_id"], worker_id or "dispatching", LEASE_SECONDS, pool_id=DEFAULT_PROXY_POOL_ID)
                if extra is None:
                    break
                extra["callback_url"] = CALLBACK_URL
                extra["protocol_version"] = _protocol_version()
                payloads.append(extra)
        for item_payload in payloads:
            response = None
            try:
                account = (item_payload.get("accounts") or [{}])[0]
                if worker_id:
                    for acc in item_payload.get("accounts") or []:
                        if acc.get("lease_id"):
                            db.bind_lease_worker(acc["lease_id"], worker_id)
                    if not db.reserve_worker_slot(worker_id):
                        raise RuntimeError("Worker 没有可用执行槽位")
                response = requests.post(f"{WINDOWS_WORKER_URL.rstrip('/')}/worker/execute-login", json=item_payload, headers=headers, timeout=30)
                response.raise_for_status()
            except Exception as exc:
                detail = f"{type(exc).__name__}: {exc}"
                if response is not None and getattr(response, "text", ""):
                    detail = f"{detail}; response={str(response.text)[:300]}"
                db.dispatch_failed(payload.get("job_id"), f"Worker dispatch failed: {detail}", job_item_id=account.get("job_item_id"), lease_id=account.get("lease_id"))
                for pending in payloads[payloads.index(item_payload) + 1:]:
                    pending_account = (pending.get("accounts") or [{}])[0]
                    if pending_account.get("lease_id"):
                        db.dispatch_failed(payload.get("job_id"), "Worker dispatch aborted before POST", job_item_id=pending_account.get("job_item_id"), lease_id=pending_account.get("lease_id"))
                targeted_dispatch_failure = True
                raise
    except Exception as exc:
        if not targeted_dispatch_failure:
            db.dispatch_failed(payload.get("job_id"), f"Worker dispatch failed: {type(exc).__name__}: {exc}")


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
                    job = db.get_job(job_id)
                    if not job:
                        continue
                    if job["proxy_mode"] == "rotating_single_ip":
                        payload = db.rotating_worker_payload(job_id, "dispatching", LEASE_SECONDS, pool_id=DEFAULT_PROXY_POOL_ID)
                        if payload is None:
                            state = db.proxy_pool_state(DEFAULT_PROXY_POOL_ID)
                            if not state["cooling"] and not state["leased"]:
                                db.mark_pool_exhausted(job_id)
                                _notify_once(f"proxy-pool-exhausted:{job_id}", f"Binance 固定代理池耗尽，任务已停止：{job_id}")
                            continue
                    else:
                        payload = db.next_worker_payload(job_id, "dispatching", LEASE_SECONDS)
                        if payload is None:
                            continue
                    payload["callback_url"] = CALLBACK_URL
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
        if payload.status == "need_register":
            child_job = db.route_need_register_to_child_job(payload.job_item_id)
            if child_job:
                db.mark_job_running(child_job["id"])
                if child_job["proxy_mode"] == "rotating_single_ip":
                    child_payload = db.rotating_worker_payload(child_job["id"], "dispatching", LEASE_SECONDS, pool_id=DEFAULT_PROXY_POOL_ID)
                    if child_payload is None:
                        db.mark_pool_exhausted(child_job["id"])
                        _notify_once(f"proxy-pool-exhausted:{child_job['id']}", f"Binance 固定代理池耗尽，自动注册任务已停止：{child_job['id']}")
                    else:
                        child_payload["callback_url"] = CALLBACK_URL
                        threading.Thread(target=_dispatch_worker, args=(child_payload,), daemon=True).start()
                else:
                    child_payload = db.next_worker_payload(child_job["id"], "dispatching", LEASE_SECONDS)
                    if child_payload:
                        child_payload["callback_url"] = CALLBACK_URL
                        threading.Thread(target=_dispatch_worker, args=(child_payload,), daemon=True).start()
        if payload.status == "failed" and (payload.error_code or "").startswith(("proxy_bootstrap", "proxy_api", "worker_config")):
            _notify_once(f"proxy-init-failed:{payload.job_id}:{payload.job_item_id}", f"Binance 代理初始化失败：任务 {payload.job_id}，账号明细 {payload.job_item_id}，错误 {payload.error_code}")
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
            payload = db.next_worker_payload(job["id"], "dispatching", LEASE_SECONDS)
            if payload is None:
                raise ValueError("任务没有可派发账号")
            payload["callback_url"] = CALLBACK_URL
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


@app.patch("/api/workers/{worker_id}/concurrency")
def update_worker_concurrency(worker_id: str, request: dict, x_worker_token: str | None = Header(default=None)):
    """由 Cloud 转发 Worker 并发热更新，并在成功后同步 SQLite 容量。"""
    _auth(x_worker_token, WORKER_TOKEN, "Cloud")
    request_worker_id = request.get("worker_id") if isinstance(request, dict) else None
    if request_worker_id != worker_id:
        raise HTTPException(400, "路径 worker_id 必须与请求体 worker_id 一致")
    value = request.get("worker_max_workers") if isinstance(request, dict) else None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise HTTPException(400, "worker_max_workers 必须是正整数")
    try:
        response = requests.patch(
            f"{WINDOWS_WORKER_URL.rstrip('/')}/worker/config/concurrency",
            json={"worker_id": worker_id, "worker_max_workers": value},
            headers={"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {},
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        db.set_worker_capacity(worker_id, value)
        db.record_log("worker_concurrency_updated", f"worker={worker_id} worker_max_workers={value}", worker_id=worker_id)
        return {"status": "updated", "worker_id": worker_id, "worker_max_workers": value, "worker": result}
    except requests.RequestException as exc:
        db.record_log("worker_concurrency_update_failed", str(exc), worker_id=worker_id, level="ERROR")
        raise HTTPException(502, f"Worker 并发热更新失败: {exc}") from exc


@app.get("/api/login-jobs/{job_id}/logs")
def logs(job_id: str):
    if not db.get_job(job_id):
        raise HTTPException(404, "任务不存在")
    return db.logs(job_id)


@app.get("/api/login-jobs/{job_id}/diagnostics")
def job_diagnostics(job_id: str):
    value = db.job_diagnostics(job_id)
    if not value:
        raise HTTPException(404, "任务不存在")
    return value
