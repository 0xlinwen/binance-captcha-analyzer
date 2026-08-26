"""Windows 登录执行服务。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Header

from binance_analyzer.config import load_config
from binance_analyzer.orchestrator import register_account


app = FastAPI(title="Binance Login Windows Worker")
BASE_DIR = Path(os.getenv("BINANCE_WORKER_BASE_DIR", ".")).resolve()
CONFIG = None
LINUX_CALLBACK_URL = os.getenv("BINANCE_CALLBACK_URL", "")
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
WORKER_ID = os.getenv("BINANCE_WORKER_ID", "windows-01")


def _config_for_task(proxy: dict) -> dict:
    global CONFIG
    if CONFIG is None:
        CONFIG = load_config(BASE_DIR)
    config = dict(CONFIG)
    configured = dict(CONFIG.get("proxy") or {})
    mode = str(proxy.get("mode", "direct")).lower()
    if mode == "direct":
        configured["enabled"] = False
    elif mode == "fixed":
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
    return config


def execute(payload: dict) -> None:
    job_id = payload["job_id"]
    task_config = _config_for_task(payload.get("proxy") or {})
    callback_url = payload.get("callback_url") or LINUX_CALLBACK_URL
    headers = {"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {}
    callback_base = callback_url.rsplit("/api/", 1)[0] if "/api/" in callback_url else ""
    if callback_base:
        requests.post(callback_base + f"/api/workers/{WORKER_ID}/register", json={"version": "1"}, headers=headers, timeout=20).raise_for_status()
    for index, account in enumerate(payload["accounts"]):
        email, password = account["email"], account["password"]
        heartbeat_stop = threading.Event()
        try:
            heartbeat_thread = None
            if callback_base:
                heartbeat_url = callback_base + f"/api/workers/{WORKER_ID}/heartbeat"
                requests.post(heartbeat_url, json={"current_job_item_id": account["job_item_id"]}, headers=headers, timeout=20).raise_for_status()
                def heartbeat_loop():
                    while not heartbeat_stop.wait(30):
                        try:
                            requests.post(heartbeat_url, json={"current_job_item_id": account["job_item_id"]}, headers=headers, timeout=20).raise_for_status()
                        except requests.RequestException:
                            pass
                heartbeat_thread = threading.Thread(target=heartbeat_loop, daemon=True)
                heartbeat_thread.start()
            result_holder = {}
            status = register_account(BASE_DIR, email, password, task_config, worker_id=index, result_sink=result_holder.update)
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "status": status.value}
            if status.value == "success":
                result.update({"cookie": result_holder["cookie"], "csrftoken": result_holder.get("csrftoken"),
                               "cookie_expires_at": result_holder.get("cookie_expires_at")})
        except Exception as exc:
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "status": "failed", "error_code": "worker_error", "error_message": str(exc)}
        finally:
            heartbeat_stop.set()
        if callback_url:
            last_error = None
            for _ in range(3):
                try:
                    response = requests.post(callback_url, json=result, headers=headers, timeout=30)
                    response.raise_for_status()
                    last_error = None
                    break
                except requests.RequestException as exc:
                    last_error = exc
                    time.sleep(2)
            if last_error is not None:
                raise last_error


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/worker/register")
def register():
    if not LINUX_CALLBACK_URL:
        return {"status": "standalone"}
    response = requests.post(LINUX_CALLBACK_URL.rsplit("/api/", 1)[0] + f"/api/workers/{WORKER_ID}/register", json={"version": "1"}, headers={"X-Worker-Token": WORKER_TOKEN}, timeout=20)
    response.raise_for_status()
    return response.json()


@app.post("/worker/execute-login", status_code=202)
def execute_login(payload: dict, x_worker_token: str | None = Header(default=None)):
    if WORKER_TOKEN and x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "Worker token 无效")
    if not payload.get("job_id") or not payload.get("accounts"):
        raise HTTPException(400, "缺少 job_id 或 accounts")
    threading.Thread(target=execute, args=(payload,), daemon=True).start()
    return {"job_id": payload["job_id"], "status": "accepted"}
