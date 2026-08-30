"""Windows 登录执行服务。"""

from __future__ import annotations

import os
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import requests
from urllib.parse import urlparse
from fastapi import FastAPI, HTTPException, Header
from pydantic import BaseModel, Field

from binance_analyzer.config import load_config
from binance_analyzer.automation.orchestrator import register_account
from .callback_outbox import CallbackOutbox
from ..shared.protocols import ExecuteLoginPayload


app = FastAPI(title="Binance Login Windows Worker")
BASE_DIR = Path(os.getenv("BINANCE_WORKER_BASE_DIR", ".")).resolve()
CONFIG = None
WORKER_TOKEN = os.getenv("BINANCE_WORKER_TOKEN", "")
CALLBACK_TOKEN = os.getenv("BINANCE_CALLBACK_TOKEN", WORKER_TOKEN)
CALLBACK_OUTBOX = CallbackOutbox(BASE_DIR / "data" / "runtime" / "callback_outbox.json")
ACCOUNT_EXECUTOR: ThreadPoolExecutor | None = None
ACCOUNT_EXECUTOR_SIZE: int | None = None
ACCOUNT_EXECUTOR_LOCK = threading.Lock()
ACCOUNT_EXECUTOR_GENERATIONS = 0


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
    capacity = data.get("worker_max_workers", 1)
    if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
        raise ValueError("config/worker.json 的 worker_max_workers 必须是正整数")
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
        parsed = urlparse(address if "://" in address else f"socks5://{address}")
        if not parsed.hostname or not parsed.port:
            raise ValueError("固定代理 address 必须是 host:port 或 scheme://host:port")
        static = dict(configured.get("static") or {})
        static.update({"scheme": parsed.scheme, "host": parsed.hostname, "port": parsed.port})
        if parsed.username is not None:
            static["username"] = parsed.username
        if parsed.password is not None:
            static["password"] = parsed.password
        configured.update({"enabled": True, "mode": "static", "static": static})
    elif proxy_mode == "dynamic":
        if str(configured.get("mode") or "").strip().lower() != "dynamic":
            raise ValueError("动态任务要求 Worker automation.json/proxy.json 配置 dynamic profile")
        configured["enabled"] = True
    else:
        raise ValueError("proxy.mode 只支持 direct/fixed/dynamic")
    config["proxy"] = configured
    config["mode"] = task_mode
    return config


def _lease_metadata(payload: dict, account: dict) -> dict:
    """提取账号级租约标识；旧批量 payload 没有这些字段时保持兼容。"""
    return {
        "lease_id": account.get("lease_id") or payload.get("lease_id"),
        "proxy_entry_id": account.get("proxy_entry_id") or payload.get("proxy_entry_id"),
        "dispatch_sequence": account.get("dispatch_sequence") or payload.get("dispatch_sequence"),
        "proxy_profile": payload.get("proxy_profile"),
    }


def _validate_lease_metadata(payload: dict, account: dict, proxy: dict) -> dict:
    metadata = _lease_metadata(payload, account)
    profile = str(metadata.get("proxy_profile") or "").strip().lower()
    if profile == "rotating_single_ip" and (not metadata["lease_id"] or not metadata["proxy_entry_id"]):
        raise ValueError("固定池任务必须携带 lease_id 和 proxy_entry_id")
    if metadata["lease_id"]:
        if not metadata["proxy_entry_id"]:
            raise ValueError("代理租约缺少 proxy_entry_id")
        if not metadata["proxy_profile"]:
            raise ValueError("代理租约缺少 proxy_profile")
        if str(proxy.get("mode") or "direct").strip().lower() != "direct" and not proxy.get("address"):
            if metadata["proxy_profile"] == "dynamic" and str(proxy.get("mode") or "").strip().lower() != "dynamic":
                raise ValueError("动态代理租约与任务代理模式不一致")
        if profile == "rotating_single_ip" and str(proxy.get("mode") or "").strip().lower() != "fixed":
            raise ValueError("固定池租约必须使用 fixed 代理地址")
    return metadata


def _account_executor(max_workers: int) -> ThreadPoolExecutor:
    global ACCOUNT_EXECUTOR, ACCOUNT_EXECUTOR_SIZE
    with ACCOUNT_EXECUTOR_LOCK:
        if ACCOUNT_EXECUTOR is None:
            ACCOUNT_EXECUTOR = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="account")
            ACCOUNT_EXECUTOR_SIZE = max_workers
        elif ACCOUNT_EXECUTOR_SIZE != max_workers:
            raise ValueError("worker_max_workers 只能在 Worker 启动前配置")
        return ACCOUNT_EXECUTOR


def _set_worker_concurrency(max_workers: int) -> dict:
    """热替换账号线程池；旧线程池继续排空，避免中断已接收任务。"""
    global ACCOUNT_EXECUTOR, ACCOUNT_EXECUTOR_SIZE, ACCOUNT_EXECUTOR_GENERATIONS
    if isinstance(max_workers, bool) or not isinstance(max_workers, int) or max_workers <= 0:
        raise ValueError("worker_max_workers 必须是正整数")
    path = BASE_DIR / "config" / "worker.json"
    with ACCOUNT_EXECUTOR_LOCK:
        data = _worker_config()
        previous = int(data.get("worker_max_workers", 1))
        data["worker_max_workers"] = max_workers
        temporary = path.with_suffix(".tmp")
        temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        temporary.replace(path)
        if ACCOUNT_EXECUTOR is not None and ACCOUNT_EXECUTOR_SIZE != max_workers:
            # 不取消旧队列；旧 payload 会在原线程池中完成，新 payload 使用新线程池。
            # 不立即 shutdown：execute 可能刚在锁外拿到旧池并准备 submit，立即关闭会造成竞态提交失败。
            # 旧池没有新任务引用后会在线程结束时自然退出。
            ACCOUNT_EXECUTOR = None
            ACCOUNT_EXECUTOR_SIZE = None
        ACCOUNT_EXECUTOR_GENERATIONS += 1
        return {"worker_max_workers": max_workers, "previous_worker_max_workers": previous,
                "executor_generation": ACCOUNT_EXECUTOR_GENERATIONS}


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


def _worker_heartbeat_loop() -> None:
    """Worker 空闲时也持续报到，避免 Cloud 将正常节点误标为 offline。"""
    while True:
        try:
            config = _worker_config()
            callback_url = str(config.get("callback_url") or "").strip()
            if callback_url:
                base = callback_url.rsplit("/api/", 1)[0]
                headers = {"X-Worker-Token": WORKER_TOKEN} if WORKER_TOKEN else {}
                requests.post(
                    f"{base}/api/workers/{config['worker_id']}/heartbeat",
                    json={}, headers=headers, timeout=20,
                ).raise_for_status()
        except Exception as exc:
            # 心跳失败不应终止 Worker；下次周期继续尝试，并保留可见日志。
            print(f"[worker-heartbeat] 心跳失败: {exc}")
        time.sleep(30)


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
                                            **_lease_metadata(payload, account),
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
        lease_metadata = _lease_metadata(payload, account)
        email, password = account["email"], account["password"]
        if account.get("client_id") and account.get("refresh_token") and "----" not in password:
            password = f"{password}----{account['client_id']}----{account['refresh_token']}"
        heartbeat_stop = threading.Event()
        try:
            lease_metadata = _validate_lease_metadata(payload, account, payload.get("proxy") or {})
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
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "worker_id": worker_id,
                      **lease_metadata, "status": callback_status,
                      "error_code": callback_error_code,
                      "error_message": "登录成功但未导出凭证" if callback_error_code == "credentials_missing" else getattr(automation_result, "error_message", None)}
            if callback_status == "success":
                result.update({"cookie": credentials.cookie, "csrftoken": credentials.csrftoken,
                               "credential_exported_at": credentials.credential_exported_at})
        except Exception as exc:
            result = {"job_id": job_id, "job_item_id": account["job_item_id"], "account_id": account["account_id"], "worker_id": worker_id,
                      **lease_metadata, "status": "failed", "error_code": "worker_error", "error_message": str(exc)}
        finally:
            heartbeat_stop.set()
        if callback_url:
            _queue_callback(callback_url, result)


@app.get("/health")
def health():
    config = _worker_config()
    max_workers = int(config.get("worker_max_workers", 1))
    if max_workers <= 0:
        raise ValueError("worker_max_workers 必须是正整数")
    return {"status": "ok", "worker_id": config["worker_id"], "protocol_version": config["protocol_version"], "worker_max_workers": max_workers}


class ConcurrencyUpdate(BaseModel):
    worker_id: str = Field(min_length=1)
    worker_max_workers: int = Field(gt=0)


@app.patch("/worker/config/concurrency")
def update_concurrency(request: ConcurrencyUpdate, x_worker_token: str | None = Header(default=None)):
    if WORKER_TOKEN and x_worker_token != WORKER_TOKEN:
        raise HTTPException(401, "Worker token 无效")
    try:
        configured_worker_id = _worker_config()["worker_id"]
        if request.worker_id != configured_worker_id:
            raise HTTPException(409, f"worker_id 不匹配: target={configured_worker_id}")
        return {"status": "updated", **_set_worker_concurrency(request.worker_max_workers)}
    except (OSError, ValueError, TypeError) as exc:
        raise HTTPException(400, str(exc)) from exc


def _protocol_version() -> str:
    return _worker_config()["protocol_version"].strip()


@app.on_event("startup")
def start_callback_retry_loop() -> None:
    threading.Thread(target=_callback_retry_loop, daemon=True, name="callback-outbox").start()
    threading.Thread(target=_worker_heartbeat_loop, daemon=True, name="worker-heartbeat").start()


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
