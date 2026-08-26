"""SQLite 持久化层。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
import shutil
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL, client_id TEXT, refresh_token TEXT,
  status TEXT NOT NULL DEFAULT 'active', created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_jobs (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, proxy_mode TEXT NOT NULL DEFAULT 'direct',
  proxy_address TEXT, max_accounts_per_proxy INTEGER, total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS login_job_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES login_jobs(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id), status TEXT NOT NULL,
  proxy_address TEXT, error_code TEXT, error_message TEXT, started_at TEXT, completed_at TEXT,
  worker_id TEXT, lease_expires_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 2,
  UNIQUE(job_id, account_id)
);
CREATE TABLE IF NOT EXISTS login_job_proxy_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES login_jobs(id),
  proxy_address TEXT NOT NULL, assigned_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
  last_assigned_at TEXT, UNIQUE(job_id, proxy_address)
);
CREATE TABLE IF NOT EXISTS credentials (
  id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
  cookie TEXT NOT NULL, csrftoken TEXT, cookie_expires_at TEXT, last_verified_at TEXT,
  status TEXT NOT NULL DEFAULT 'unknown', last_check_error TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, job_item_id INTEGER, account_id INTEGER,
  worker_id TEXT, level TEXT NOT NULL, event TEXT NOT NULL, message TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_nodes (
  id TEXT PRIMARY KEY, name TEXT, status TEXT NOT NULL DEFAULT 'offline', version TEXT,
  last_heartbeat_at TEXT, current_job_item_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
"""


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA synchronous=NORMAL")
        self.conn.executescript(SCHEMA)
        self._migrate_columns()
        self.conn.commit()

    def _migrate_columns(self) -> None:
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(login_job_items)")}
        for name, definition in (("worker_id", "TEXT"), ("lease_expires_at", "TEXT"), ("retry_count", "INTEGER NOT NULL DEFAULT 0"), ("max_retries", "INTEGER NOT NULL DEFAULT 2")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE login_job_items ADD COLUMN {name} {definition}")

    def close(self) -> None:
        self.conn.close()

    def _one(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def save_account(self, email: str, password: str, client_id=None, refresh_token=None) -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("""INSERT INTO accounts(email,password,client_id,refresh_token,status,created_at,updated_at)
                VALUES(?,?,?,?, 'active', ?, ?) ON CONFLICT(email) DO UPDATE SET password=excluded.password,
                client_id=excluded.client_id, refresh_token=excluded.refresh_token, updated_at=excluded.updated_at""",
                (email, password, client_id, refresh_token, now, now))
            return self._one("SELECT * FROM accounts WHERE email=?", (email,))

    def create_job(self, accounts: list[dict], proxy: dict | None = None) -> dict:
        if not accounts:
            raise ValueError("任务至少需要一个账号")
        proxy = proxy or {}
        mode = str(proxy.get("mode", "direct")).lower()
        if mode not in {"direct", "fixed"}:
            raise ValueError("proxy.mode 只支持 direct/fixed")
        address = proxy.get("address") if mode == "fixed" else None
        limit = proxy.get("max_accounts_per_job") if mode == "fixed" else None
        if mode == "fixed" and (not address or not isinstance(limit, int) or limit <= 0):
            raise ValueError("固定代理必须配置 address 和正整数 max_accounts_per_job")
        now, job_id = utc_now(), str(uuid.uuid4())
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO login_jobs(id,status,proxy_mode,proxy_address,max_accounts_per_proxy,total_count,created_at) VALUES(?,?,?,?,?,?,?)",
                              (job_id, "submitted", mode, address, limit, len(accounts), now))
            quota_exceeded = 0
            for index, account in enumerate(accounts):
                self.conn.execute("""INSERT INTO accounts(email,password,client_id,refresh_token,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET password=excluded.password,client_id=excluded.client_id,refresh_token=excluded.refresh_token,updated_at=excluded.updated_at""",
                                  (account["email"], account["password"], account.get("client_id"), account.get("refresh_token"), now, now))
                row = self._one("SELECT id FROM accounts WHERE email=?", (account["email"],))
                item_status = "queued"
                if mode == "fixed" and index >= limit:
                    item_status = "proxy_quota_exceeded"
                    quota_exceeded += 1
                self.conn.execute("INSERT INTO login_job_items(job_id,account_id,status,proxy_address) VALUES(?,?,?,?)", (job_id, row["id"], item_status, address))
            if quota_exceeded:
                self.conn.execute("UPDATE login_jobs SET failed_count=? WHERE id=?", (quota_exceeded, job_id))
            if mode == "fixed":
                self.conn.execute("INSERT INTO login_job_proxy_usage(job_id,proxy_address,assigned_count,last_assigned_at) VALUES(?,?,?,?)", (job_id, address, min(len(accounts), limit), now))
        return self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))

    def get_job(self, job_id: str):
        job = self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))
        if job:
            job["items"] = [dict(r) for r in self.conn.execute("SELECT i.*, a.email FROM login_job_items i JOIN accounts a ON a.id=i.account_id WHERE job_id=? ORDER BY i.id", (job_id,))]
        return job

    def worker_payload(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("任务不存在")
        return {
            "job_id": job_id,
            "proxy": {"mode": job["proxy_mode"], "address": job["proxy_address"], "max_accounts_per_job": job["max_accounts_per_proxy"]},
            "accounts": [
                {"job_item_id": item["id"], "account_id": item["account_id"], "email": item["email"],
                 "password": self._one("SELECT password,client_id,refresh_token FROM accounts WHERE id=?", (item["account_id"],))["password"],
                 "client_id": self._one("SELECT client_id FROM accounts WHERE id=?", (item["account_id"],))["client_id"],
                 "refresh_token": self._one("SELECT refresh_token FROM accounts WHERE id=?", (item["account_id"],))["refresh_token"]}
                for item in job["items"] if item["status"] in {"queued", "running"}
            ],
        }

    def mark_items_running(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = utc_now()
        lease = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + lease_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            self.conn.execute("UPDATE login_job_items SET status='running', worker_id=?, started_at=COALESCE(started_at, ?), lease_expires_at=? WHERE job_id=? AND status='queued'", (worker_id, now, lease, job_id))
            self.record_log("items_claimed", f"worker={worker_id}", job_id=job_id, worker_id=worker_id)

    def register_worker(self, worker_id: str, name: str = "", version: str = "") -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("""INSERT INTO worker_nodes(id,name,status,version,last_heartbeat_at,created_at,updated_at)
                VALUES(?,?, 'online', ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='online',version=excluded.version,last_heartbeat_at=excluded.last_heartbeat_at,updated_at=excluded.updated_at""", (worker_id, name, version, now, now, now))
            return self._one("SELECT * FROM worker_nodes WHERE id=?", (worker_id,))

    def heartbeat(self, worker_id: str, current_job_item_id=None) -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("UPDATE worker_nodes SET status='online',last_heartbeat_at=?,current_job_item_id=?,updated_at=? WHERE id=?", (now, current_job_item_id, now, worker_id))
            return self._one("SELECT * FROM worker_nodes WHERE id=?", (worker_id,))

    def recover_expired_items(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        stale = self.conn.execute("SELECT id,job_id FROM login_job_items WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (now,)).fetchall()
        with self._lock, self.conn:
            for item in stale:
                self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_timeout',error_message='Worker lease expired',completed_at=? WHERE id=?", (utc_now(), item[0]))
                self.record_log("worker_timeout", "任务租约过期", job_id=item[1], job_item_id=item[0], level="WARNING")
        return len(stale)

    def mark_offline_workers(self, timeout_seconds: int = 120) -> int:
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - timeout_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            result = self.conn.execute("UPDATE worker_nodes SET status='offline',updated_at=? WHERE status='online' AND last_heartbeat_at < ?", (utc_now(), cutoff))
            return result.rowcount

    def pending_jobs(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT job_id FROM login_job_items WHERE status IN ('queued','retryable')").fetchall()
        return [row[0] for row in rows]

    def dispatch_failed(self, job_id: str, message: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_dispatch_failed',error_message=? WHERE job_id=? AND status='running'", (message, job_id))
            self.record_log("worker_dispatch_failed", message, job_id=job_id, level="ERROR")

    def requeue_retryable(self) -> int:
        with self._lock, self.conn:
            result = self.conn.execute("UPDATE login_job_items SET status='queued' WHERE status='retryable' AND retry_count < max_retries")
            return result.rowcount

    def cancel_job(self, job_id: str) -> dict | None:
        with self._lock, self.conn:
            job = self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))
            if not job:
                return None
            if job["status"] in {"completed", "cancelled"}:
                return job
            self.conn.execute("UPDATE login_job_items SET status='cancelled',completed_at=? WHERE job_id=? AND status IN ('queued','retryable','running')", (utc_now(), job_id))
            self.conn.execute("UPDATE login_jobs SET status='cancelled',completed_at=? WHERE id=?", (utc_now(), job_id))
            self.record_log("job_cancelled", "任务被取消", job_id=job_id, level="WARNING")
            return self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))

    def cleanup_logs(self, before: str) -> int:
        with self._lock, self.conn:
            return self.conn.execute("DELETE FROM execution_logs WHERE created_at < ?", (before,)).rowcount

    def backup(self, destination: str | Path) -> Path:
        target = Path(destination)
        target.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            backup_conn = sqlite3.connect(target)
            try:
                self.conn.backup(backup_conn)
            finally:
                backup_conn.close()
        return target

    def mark_job_running(self, job_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE login_jobs SET status='running', started_at=COALESCE(started_at, ?) WHERE id=?", (utc_now(), job_id))

    def record_log(self, event: str, message: str = "", *, job_id=None, job_item_id=None, account_id=None, worker_id=None, level="INFO") -> None:
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO execution_logs(job_id,job_item_id,account_id,worker_id,level,event,message,created_at) VALUES(?,?,?,?,?,?,?,?)",
                              (job_id, job_item_id, account_id, worker_id, level, event, message, utc_now()))

    def save_callback(self, payload: dict) -> dict:
        item_id, status = payload["job_item_id"], payload["status"]
        with self._lock, self.conn:
            item = self._one("SELECT * FROM login_job_items WHERE id=?", (item_id,))
            if not item:
                raise ValueError("找不到任务明细")
            if item["status"] in {"success", "failed", "proxy_quota_exceeded"}:
                return item
            if status not in {"success", "failed", "retryable", "proxy_failed", "rate_limited"}:
                raise ValueError(f"不支持的回调状态: {status}")
            now = utc_now()
            stored_status = "failed" if status in {"proxy_failed", "rate_limited"} else status
            self.conn.execute("UPDATE login_job_items SET status=?, error_code=?, error_message=?, completed_at=?, lease_expires_at=NULL WHERE id=?",
                              (stored_status, payload.get("error_code"), payload.get("error_message"), now, item_id))
            if status == "success" and payload.get("cookie"):
                self.conn.execute("""INSERT INTO credentials(account_id,cookie,csrftoken,cookie_expires_at,last_verified_at,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'valid',?,?) ON CONFLICT(account_id) DO UPDATE SET cookie=excluded.cookie,csrftoken=excluded.csrftoken,
                    cookie_expires_at=excluded.cookie_expires_at,last_verified_at=excluded.last_verified_at,status='valid',updated_at=excluded.updated_at""",
                    (item["account_id"], payload["cookie"], payload.get("csrftoken"), payload.get("cookie_expires_at"), now, now, now))
            if item.get("proxy_address"):
                self.conn.execute("""UPDATE login_job_proxy_usage SET success_count=success_count+?,failed_count=failed_count+?,last_assigned_at=? WHERE job_id=? AND proxy_address=?""",
                    (1 if status == "success" else 0, 0 if status == "success" else 1, now, item["job_id"], item["proxy_address"]))
            self.record_log("callback_received", f"status={status}", job_id=item["job_id"], job_item_id=item_id, account_id=item["account_id"], worker_id=payload.get("worker_id"))
            counts = self.conn.execute("SELECT SUM(status='success'), SUM(status NOT IN ('queued','running','retryable')), COUNT(*) FROM login_job_items WHERE job_id=?", (item["job_id"],)).fetchone()
            done = (counts[0] or 0) + (counts[1] or 0)
            self.conn.execute("UPDATE login_jobs SET success_count=?,failed_count=?,status=?,completed_at=CASE WHEN ?=total_count THEN ? ELSE completed_at END WHERE id=?",
                              (counts[0] or 0, counts[1] or 0, "completed" if done == counts[2] else "running", done, now if done == counts[2] else None, item["job_id"]))
            return self._one("SELECT * FROM login_job_items WHERE id=?", (item_id,))

    def credential(self, account_id: int):
        return self._one("SELECT * FROM credentials WHERE account_id=?", (account_id,))

    def credentials_for_check(self) -> list[dict]:
        return [dict(row) for row in self.conn.execute("SELECT * FROM credentials WHERE status IN ('valid','unknown')")]

    def update_credential_check(self, account_id: int, status: str, error: str = "") -> dict:
        if status not in {"valid", "expired", "unknown"}:
            raise ValueError("凭证检查状态无效")
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("UPDATE credentials SET status=?,last_verified_at=?,last_check_error=?,updated_at=? WHERE account_id=?", (status, now, error or None, now, account_id))
            return self.credential(account_id)

    def workers(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM worker_nodes ORDER BY id")]

    def logs(self, job_id: str):
        return [dict(r) for r in self.conn.execute("SELECT * FROM execution_logs WHERE job_id=? ORDER BY id", (job_id,))]
