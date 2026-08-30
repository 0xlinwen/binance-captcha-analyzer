"""本地固定代理池租约。

供 CLI 多进程从 proxy_pool.txt 领取互不重叠的出口；冷却/失败规则与 Cloud 固定池一致：
到期冷却才复活，禁止因为没有可用条目而提前解冻。
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ..config import load_proxy_pool
from ..runtime.file_lock import lock, unlock

POOL_ONLY_KEYS = (
    "pool_file",
    "cooldown_seconds",
    "allow_parallel",
    "switch_after_account_failures",
    "switch_after_consecutive_account_failures",
)
LEASE_TTL = timedelta(hours=2)
STATE_RELATIVE_PATH = Path("data/runtime/local_fixed_proxy_pool.json")
NO_FAILURE_STATUSES = {"success", "already_registered", "retryable"}


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_utc(value: str | None) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def entry_key(entry: dict[str, Any]) -> str:
    """用 scheme/host/port 标识池条目，状态文件不保存密码。"""
    return f"{entry['scheme']}://{entry['host']}:{entry['port']}"


@dataclass(frozen=True)
class LocalProxyLease:
    """一次本地固定池租约。"""

    lease_id: str
    scheme: str
    host: str
    port: int
    username: str
    password: str
    entry_key: str

    def to_static_proxy_config(self, proxy_config: dict[str, Any]) -> dict[str, Any]:
        """把租到的条目转成本地 runtime 可识别的 static 配置。"""
        bound = dict(proxy_config or {})
        gost = bound.get("gost")
        if isinstance(gost, dict):
            bound["gost"] = {**gost, "listen_port": 0}
        bound["mode"] = "static"
        bound["static"] = {
            "scheme": self.scheme,
            "host": self.host,
            "port": self.port,
            "username": self.username,
            "password": self.password,
        }
        for key in POOL_ONLY_KEYS:
            bound.pop(key, None)
        return bound


class LocalFixedProxyPool:
    """基于文件锁的本地固定代理池。"""

    def __init__(self, base_dir: Path, proxy_config: dict[str, Any]) -> None:
        self.base_dir = Path(base_dir)
        self.proxy_config = dict(proxy_config or {})
        self.state_path = self.base_dir / STATE_RELATIVE_PATH
        self.lock_path = self.state_path.with_suffix(self.state_path.suffix + ".lock")
        pool_document = {"profiles": {"rotating_single_ip": self.proxy_config}}
        self.pool = load_proxy_pool(self.base_dir, pool_document)

    def acquire(self) -> LocalProxyLease | None:
        """领取一条未租用且未冷却的代理；没有可用条目时返回 None。"""
        with self._exclusive_lock():
            state = self._load_state()
            entries = self._sync_entries(state)
            now = _utc_now()
            self._expire_entries(entries, now)
            if not self.pool["allow_parallel"] and any(item.get("status") == "leased" for item in entries.values()):
                self._save_state(state, entries)
                return None
            chosen_key = self._select_available(entries)
            if chosen_key is None:
                self._save_state(state, entries)
                return None
            source = self._source_by_key()[chosen_key]
            lease_id = str(uuid.uuid4())
            entries[chosen_key]["status"] = "leased"
            entries[chosen_key]["lease_id"] = lease_id
            entries[chosen_key]["leased_at"] = now.isoformat()
            self._save_state(state, entries)
            return LocalProxyLease(
                lease_id=lease_id,
                scheme=str(source["scheme"]),
                host=str(source["host"]),
                port=int(source["port"]),
                username=str(source.get("username") or ""),
                password=str(source.get("password") or ""),
                entry_key=chosen_key,
            )

    def release(self, lease_id: str, *, result_status: str) -> None:
        """释放租约，并按账号终态更新失败计数/冷却。"""
        lease_id = str(lease_id or "").strip()
        if not lease_id:
            raise ValueError("lease_id 不能为空")
        status = str(result_status or "").strip().lower()
        with self._exclusive_lock():
            state = self._load_state()
            entries = self._sync_entries(state)
            now = _utc_now()
            self._expire_entries(entries, now)
            target_key = None
            for key, item in entries.items():
                if str(item.get("lease_id") or "") == lease_id:
                    target_key = key
                    break
            if target_key is None:
                self._save_state(state, entries)
                return
            item = entries[target_key]
            failure_increment = 0 if status in NO_FAILURE_STATUSES else 1
            next_failures = 0 if status in {"success", "already_registered"} else int(item.get("consecutive_failures") or 0) + failure_increment
            item["consecutive_failures"] = next_failures
            item["lease_id"] = None
            item["leased_at"] = None
            threshold = int(self.pool["switch_threshold"])
            if failure_increment and next_failures >= threshold:
                cooldown_until = now + timedelta(seconds=int(self.pool["cooldown_seconds"]))
                item["status"] = "cooling"
                item["cooldown_until"] = cooldown_until.isoformat()
            else:
                item["status"] = "available"
                if status in {"success", "already_registered"}:
                    item["cooldown_until"] = None
            self._save_state(state, entries)

    def _source_by_key(self) -> dict[str, dict[str, Any]]:
        return {entry_key(entry): entry for entry in self.pool["entries"]}

    def _sync_entries(self, state: dict[str, Any]) -> dict[str, dict[str, Any]]:
        stored = state.get("entries")
        if not isinstance(stored, dict):
            stored = {}
        merged: dict[str, dict[str, Any]] = {}
        for position, entry in enumerate(self.pool["entries"]):
            key = entry_key(entry)
            previous = stored.get(key) if isinstance(stored.get(key), dict) else {}
            merged[key] = {
                "status": str(previous.get("status") or "available"),
                "consecutive_failures": int(previous.get("consecutive_failures") or 0),
                "cooldown_until": previous.get("cooldown_until"),
                "lease_id": previous.get("lease_id"),
                "leased_at": previous.get("leased_at"),
                "position": position,
            }
        return merged

    def _expire_entries(self, entries: dict[str, dict[str, Any]], now: datetime) -> None:
        for item in entries.values():
            if item.get("status") == "cooling":
                cooldown_until = _parse_utc(item.get("cooldown_until"))
                if cooldown_until is not None and cooldown_until <= now:
                    item["status"] = "available"
                    item["cooldown_until"] = None
                    item["consecutive_failures"] = 0
                    item["lease_id"] = None
                    item["leased_at"] = None
            elif item.get("status") == "leased":
                leased_at = _parse_utc(item.get("leased_at"))
                if leased_at is None or leased_at + LEASE_TTL <= now:
                    item["status"] = "available"
                    item["lease_id"] = None
                    item["leased_at"] = None

    def _select_available(self, entries: dict[str, dict[str, Any]]) -> str | None:
        candidates = [
            (int(item.get("position") or 0), key)
            for key, item in entries.items()
            if item.get("status") in {"available", "active"}
        ]
        if not candidates:
            return None
        candidates.sort()
        return candidates[0][1]

    def _load_state(self) -> dict[str, Any]:
        if not self.state_path.exists():
            return {"entries": {}}
        try:
            loaded = json.loads(self.state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"本地固定代理池状态文件无效: {self.state_path}: {exc}") from exc
        if not isinstance(loaded, dict):
            raise ValueError(f"本地固定代理池状态文件必须是 JSON 对象: {self.state_path}")
        return loaded

    def _save_state(self, state: dict[str, Any], entries: dict[str, dict[str, Any]]) -> None:
        payload = dict(state)
        payload["entries"] = entries
        payload["updated_at"] = _utc_now().isoformat()
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        temp_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        temp_path.replace(self.state_path)

    def _exclusive_lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        lock_file = open(self.lock_path, "a+", encoding="utf-8")
        return _LockGuard(lock_file)


class _LockGuard:
    def __init__(self, lock_file) -> None:
        self.lock_file = lock_file

    def __enter__(self):
        lock(self.lock_file)
        return self.lock_file

    def __exit__(self, exc_type, exc, tb) -> None:
        try:
            unlock(self.lock_file)
        finally:
            self.lock_file.close()


def bind_local_rotating_proxy(
    base_dir: Path,
    proxy_config: dict[str, Any] | None,
) -> tuple[dict[str, Any], LocalFixedProxyPool | None, str | None]:
    """rotating_single_ip 领取池条目并转为 static；其他模式只补齐 gost 端口。"""
    runtime_proxy = dict(proxy_config or {})
    gost = runtime_proxy.get("gost")
    if isinstance(gost, dict):
        runtime_proxy["gost"] = {**gost, "listen_port": 0}
    mode = str(runtime_proxy.get("mode") or "").strip().lower()
    if mode != "rotating_single_ip":
        return runtime_proxy, None, None
    if not runtime_proxy.get("enabled"):
        return runtime_proxy, None, None
    pool = LocalFixedProxyPool(base_dir, runtime_proxy)
    lease = pool.acquire()
    if lease is None:
        return runtime_proxy, pool, None
    return lease.to_static_proxy_config(runtime_proxy), pool, lease.lease_id
