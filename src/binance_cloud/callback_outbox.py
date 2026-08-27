"""Windows Worker 的持久化回调队列。"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Callable

from binance_analyzer.file_lock import lock, unlock


def _now() -> datetime:
    return datetime.now(timezone.utc)


class CallbackOutbox:
    """回调成功前保留载荷；Worker 重启后也会继续投递。"""

    def __init__(self, path: Path):
        self.path = path
        self.lock_path = path.with_suffix(f"{path.suffix}.lock")

    def enqueue(self, callback_url: str, payload: dict) -> None:
        self._update(lambda entries: entries + [{
            "callback_url": callback_url,
            "payload": payload,
            "attempt_count": 0,
            "next_attempt_at": _now().isoformat(),
        }])

    def deliver_due(self, send: Callable[[str, dict], bool]) -> int:
        now = _now()
        delivered = 0

        def update(entries: list[dict]) -> list[dict]:
            nonlocal delivered
            pending = []
            for entry in entries:
                due = datetime.fromisoformat(entry["next_attempt_at"])
                if due > now:
                    pending.append(entry)
                    continue
                if send(entry["callback_url"], entry["payload"]):
                    delivered += 1
                    continue
                attempts = entry["attempt_count"] + 1
                entry["attempt_count"] = attempts
                entry["next_attempt_at"] = (now + timedelta(seconds=min(300, 5 * (2 ** min(attempts, 6))))).isoformat()
                pending.append(entry)
            return pending

        self._update(update)
        return delivered

    def _update(self, transform: Callable[[list[dict]], list[dict]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.lock_path, "w", encoding="utf-8") as lock_file:
            lock(lock_file)
            try:
                entries = []
                if self.path.exists():
                    with open(self.path, "r", encoding="utf-8") as file:
                        entries = json.load(file)
                updated = transform(entries)
                temp_path = self.path.with_suffix(f"{self.path.suffix}.tmp")
                with open(temp_path, "w", encoding="utf-8") as file:
                    json.dump(updated, file, ensure_ascii=False)
                temp_path.replace(self.path)
            finally:
                unlock(lock_file)
