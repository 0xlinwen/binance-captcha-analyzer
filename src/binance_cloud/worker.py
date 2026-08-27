"""Windows 登录执行服务。"""

from __future__ import annotations

import os
import json
import threading
import time
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Header

from binance_analyzer.config import load_config
from binance_analyzer.orchestrator import register_account
from .callback_outbox import CallbackOutbox
from .protocols import ExecuteLoginPayload


app = FastAPI(title="Binance Login Windows Worker")
BASE_DIR = Path(os.getenv("BINANCE_WORKER_BASE_DIR", ".")).resolve()
CONFIG = None
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
CALLBACK_TOKEN = os.getenv("BINANCE_CALLBACK_TOKEN", WORKER_TOKEN)
CALLBACK_OUTBOX = CallbackOutbox(BASE_DIR / "output" / "callback_outbox.json")
ACCOUNT_EXECUTOR: ThreadPoolExecutor | None = None
ACCOUNT_EXECUTOR_SIZE: int | None = None
ACCOUNT_EXECUTOR_LOCK = threading.Lock()


def _worker_config() -> dict:
    path = BASE_DIR / "config" / "worker.json"
    if not path.exists():
        raise FileNotFoundError(f"缺少 Worker 配置文件: {path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("config/worker.json 必须是 JSON 对象")
    for key in ("protocol_version", "worker_id", "callback_url"):
        if not isinstance(data.get(key), str) or (key != "callback_url" and not data[key].strip()):
            raise ValueError(f"config/worker.json 缺少 {key}")
    return data


def _config_for_task(proxy: dict, mode: str) -> dict:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config(BASE_DIR, "config/automation.json")
    config = dict(CONFIG)
    configured = dict(CONFIG.get("proxy") or {})
    task_mode = str(mode).strip().lower()
    if task_mode not in {"login", "register"}:
        raise ValueError("任务 mode 只支持 login/register")
    proxy_mode = str(proxy.get("mode", "direct")).strip().lower()
    if proxy_mode == "direct":
        configured["enabled"] = False
    elif proxy_mode == "fixed":
        address = str(proxy.get("address") or "")
        if "://" in address:
            address = address.split("://", 1)[1]
        host, sep, port = address.rpartition(":")
        if not sep or not host or not port:
            raise ValueError("固定代理 address 必须是 host:port 或 scheme://host:port")
        static = dict(configured.get("static") or {})
        static.update({"host": host, "port": port})
        configured.update({"enabled": True, "mode": "static", "static": static})
    else:
        raise ValueError("proxy.mode 只支持 direct/fixed")
    config["proxy"] = configured
    config["mode"] = task_mode
    return config


def _account_executor(max_workers: int) -> ThreadPoolExecutor:
    global ACCOUNT_EXECUTOR, ACCOUNT_EXECUTOR_SIZE
    with ACCOUNT_EXECUTOR_LOCK:
        if ACCOUNT_EXECUTOR is None:
            ACCOUNT_EXECUTOR = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="account")
            ACCOUNT_EXECUTOR_SIZE = max_workers
        elif ACCOUNT_EXECUTOR_SIZE != max_workers:
            raise ValueError("worker_max_workers 只能在 Worker 启动前配置")
        return ACCOUNT_EXECUTOR


def _post_callback(url: str, payload: dict) -> bool:
    try:
        headers = {"X-Worker-Token": CALLBACK_TOKEN} if CALLBACK_TOKEN else {}
        requests.post(url, json=payload, headers=headers, timeout=30).raise_for_status()
        return True
    except requests.RequestException:
        return False


def _queue_callback(url: str, payload: dict) -> None:
    if not url:
        return
    CALLBACK_OUTBOX.enqueue(url, payload)
    CALLBACK_OUTBOX.deliver_due(_post_callback)


def _callback_retry_loop() -> None:
    while True:
        try:
            CALLBACK_OUTBOX.deliver_due(_post_callback)
        except Exception as exc:
            print(f"[callback-outbox] 回调重试失败: {exc}")
        time.sleep(5)


def execute(payload: dict) -> None:
    job_id = payload["job_id"]
    worker_config = _worker_config()
    callback_url = payload.get("callback_url") or worker_config["callback_url"]
    worker_id = worker_config["worker_id"]
    headers = {"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {}
    callback_base = callback_url.rsplit("/api/", 1)[0]
    try:
        task_config = _config_for_task(payload.get("proxy") or {}, payload["mode"])
    except Exception as exc:
        for account in payload["accounts"]:
            _queue_callback(callback_url, {"job_id": job_id, "job_item_id": account["job_item_id"],
                                            "account_id": account["account_id"], "worker_id": worker_id,
                                            "status": "failed", "error_code": "worker_config_error",
                                            "error_message": str(exc)})
        return
    accounts = payload["accounts"]
    if len(accounts) > 1 and not payload.get("_parallelized"):
        max_workers = int(worker_config.get("worker_max_workers", 1))
        if max_workers <= 0:
            raise ValueError("worker_max_workers 必须是正整数")
        executor = _account_executor(max_workers)
        futures = [executor.submit(execute, {**payload, "accounts": [account], "_parallelized": True}) for account in accounts]
        for future in futures:
            future.result()
        return
    for account in accounts:
        email, password = account["email"], account["password"]
        if account.get("client_id") and account.get("refresh_token") and "----" not in password:
            password = f"{password}----{account['client_id']}----{account['refresh_token']}"
        heartbeat_stop = threading.Event()
        try:
            debug_mode = bool(worker_config.get("debug_mode", False))
            if not debug_mode:
                state_response = requests.get(callback_base + f"/api/login-jobs/{job_id}/status", headers=headers, timeout=20)
                state_response.raise_for_status()
                if state_response.json()["status"] == "cancelled":
                    break
            heartbeat_thread = None
            if callback_base and not debug_mode:
                heartbeat_url = callback_base + f"/api/workers/{worker_id}/heartbeat"
                requests.post(heartbeat_url, json={"current_job_item_id": account["job_item_id"]}, headers=headers, timeout=20).raise_for_status()
                def heartbeat_loop():
                    while not heartbeat_stop.wait(30):
                        try:
                            requests.post(heartbeat_url, json={"current_job_item_id": account["job_item_id"]}, headers=headers, timeout=20).raise_for_status()
                        except requests.RequestException:
                            pass
                heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
                heartbeat_thread.start()
            worker_slot = abs(hash(f"{job_id}:{account['job_item_id']}")) % 1_000_000_000
            automation_result = register_account(BASE_DIR, email, password, task_config, worker_id=worker_slot)
            status = automation_result.status if hasattr(automation_result, "status") else automation_result
            credentials = getattr(automation_result, "credentials", None)
            if status.value == "success" and credentials is None:
                callback_status = "failed"
                callback_error_code = "credentials_missing"
            else:
                callback_status = status.value if status.value in {"success", "failed", "proxy_failed", "rate_limited"} else "failed"
                callback_error_code = None if callback_status == "success" else (getattr(automation_result, "error_code", None) or status.value)
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "worker_id": worker_id, "status": callback_status,
                      "error_code": callback_error_code,
                      "error_message": "登录成功但未导出凭证" if callback_error_code == "credentials_missing" else getattr(automation_result, "error_message", None)}
            if callback_status == "success":
                result.update({"cookie": credentials.cookie, "csrftoken": credentials.csrftoken,
                               "cookie_expires_at": credentials.cookie_expires_at,
                               "credential_updated_at": credentials.credential_updated_at or datetime.now(timezone.utc).isoformat()})
        except Exception as exc:
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "worker_id": worker_id, "status": "failed", "error_code": "worker_error", "error_message": str(exc)}
        finally:
            heartbeat_stop.set()
        if callback_url:
            _queue_callback(callback_url, result)


@app.get("/health")
def health():
    config = _worker_config()
    return {"status": "ok", "worker_id": config["worker_id"], "protocol_version": config["protocol_version"]}


def _protocol_version() -> str:
    return _worker_config()["protocol_version"].strip()


@app.on_event("startup")
def start_callback_retry_loop() -> None:
    threading.Thread(target=_callback_retry_loop, daemon=True, name="callback-outbox").start()


@app.post("/worker/register")
def register():
    config = _worker_config()
    if not config["callback_url"]:
        return {"status": "standalone"}
    response = requests.post(config["callback_url"].rsplit("/api/", 1)[0] + f"/api/workers/{config['worker_id']}/register", json={"version": config["protocol_version"]}, headers={"X-Worker-Token": WORKER_TOKEN}, timeout=20)
    response.raise_for_status()
    return response.json()


@app.post("/worker/execute-login", status_code=202)
def execute_login(payload: ExecuteLoginPayload, x_worker_token: str | None = Header(default=None)):
    if WORKER_TOKEN and x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "Worker token 无效")
    if payload.protocol_version != _protocol_version():
        raise HTTPException(409, f"Worker 协议版本不兼容: worker={_protocol_version()}, request={payload.protocol_version}")
    value = payload.model_dump()
    threading.Thread(target=execute, args=(value,), daemon=True).start()
    return {"job_id": payload.job_id, "status": "accepted"}
