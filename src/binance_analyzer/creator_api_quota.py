"""创作者 API 单次运行配额协调。"""

from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .file_lock import lock, unlock


STATE_FILE = ".creator_api_quota.json"


def _path(base_dir: Path, output_file: str) -> Path:
    return base_dir / Path(output_file).parent / STATE_FILE


def initialize_creator_api_quota(base_dir: Path, config: dict) -> None:
    creator = config["creator_api"]
    if not creator["enabled"]:
        return
    path = _path(base_dir, config["output_file"])
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"completed": 0, "active": []}), encoding="utf-8")


def _read(path: Path) -> dict:
    if not path.exists():
        return {"completed": 0, "active": []}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(value, dict) and isinstance(value.get("active"), list):
            return {"completed": int(value.get("completed", 0)), "active": value["active"]}
    except (OSError, TypeError, ValueError):
        pass
    raise RuntimeError("创作者 API 配额状态文件无效")


def _lock(path: Path):
    lock_path = path.with_suffix(".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock_file = open(lock_path, "w", encoding="utf-8")
    lock(lock_file)
    return lock_file


def acquire_creator_api_slot(base_dir: Path, config: dict) -> str | None:
    """取得一个名额。失败任务释放名额，后续成功账号可以补位。"""
    creator = config["creator_api"]
    max_accounts = creator["max_accounts"]
    deadline = time.monotonic() + creator["slot_wait_timeout_sec"]
    path = _path(base_dir, config["output_file"])
    token = uuid.uuid4().hex
    while True:
        lock_file = _lock(path)
        try:
            state = _read(path)
            if state["completed"] >= max_accounts:
                return None
            if len(state["active"]) < max_accounts - state["completed"]:
                state["active"].append(token)
                path.write_text(json.dumps(state), encoding="utf-8")
                return token
        finally:
            unlock(lock_file)
            lock_file.close()
        if time.monotonic() >= deadline:
            raise RuntimeError("等待创作者 API 提取名额超时")
        time.sleep(0.5)


def release_creator_api_slot(base_dir: Path, config: dict, token: str, *, completed: bool) -> None:
    path = _path(base_dir, config["output_file"])
    lock_file = _lock(path)
    try:
        state = _read(path)
        if token not in state["active"]:
            raise RuntimeError("创作者 API 配额令牌不存在")
        state["active"].remove(token)
        if completed:
            state["completed"] += 1
        path.write_text(json.dumps(state), encoding="utf-8")
    finally:
        unlock(lock_file)
        lock_file.close()
