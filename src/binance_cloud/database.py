"""SQLite 持久化层。"""

from __future__ import annotations

import json
import sqlite3
import threading
import uuid
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
        self.conn.executescript(SCHEMA)
        self.conn.commit()

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
            for account in accounts:
                self.conn.execute("INSERT OR IGNORE INTO accounts(email,password,created_at,updated_at) VALUES(?,?,?,?)",
                                  (account["email"], account["password"], now, now))
                row = self._one("SELECT id FROM accounts WHERE email=?", (account["email"],))
                self.conn.execute("INSERT INTO login_job_items(job_id,account_id,status) VALUES(?,?, 'queued')", (job_id, row["id"]))
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
                 "password": self._one("SELECT password FROM accounts WHERE id=?", (item["account_id"],))["password"]}
                for item in job["items"] if item["status"] == "queued"
            ],
        }

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
            now = utc_now()
            self.conn.execute("UPDATE login_job_items SET status=?, error_code=?, error_message=?, completed_at=? WHERE id=?",
                              (status, payload.get("error_code"), payload.get("error_message"), now, item_id))
            if status == "success" and payload.get("cookie"):
                self.conn.execute("""INSERT INTO credentials(account_id,cookie,csrftoken,cookie_expires_at,last_verified_at,status,created_at,updated_at)
                    VALUES(?,?,?,?,?,'valid',?,?) ON CONFLICT(account_id) DO UPDATE SET cookie=excluded.cookie,csrftoken=excluded.csrftoken,
                    cookie_expires_at=excluded.cookie_expires_at,last_verified_at=excluded.last_verified_at,status='valid',updated_at=excluded.updated_at""",
                    (item["account_id"], payload["cookie"], payload.get("csrftoken"), payload.get("cookie_expires_at"), now, now, now))
            counts = self.conn.execute("SELECT SUM(status='success'), SUM(status IN ('failed','proxy_quota_exceeded')), COUNT(*) FROM login_job_items WHERE job_id=?", (item["job_id"],)).fetchone()
            done = (counts[0] or 0) + (counts[1] or 0)
            self.conn.execute("UPDATE login_jobs SET success_count=?,failed_count=?,status=?,completed_at=CASE WHEN ?=total_count THEN ? ELSE completed_at END WHERE id=?",
                              (counts[0] or 0, counts[1] or 0, "completed" if done == counts[2] else "running", done, now if done == counts[2] else None, item["job_id"]))
            return self._one("SELECT * FROM login_job_items WHERE id=?", (item_id,))

    def credential(self, account_id: int):
        return self._one("SELECT * FROM credentials WHERE account_id=?", (account_id,))

    def logs(self, job_id: str):
        return [dict(r) for r in self.conn.execute("SELECT * FROM execution_logs WHERE job_id=? ORDER BY id", (job_id,))]
