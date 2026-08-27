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
  id TEXT PRIMARY KEY, status TEXT NOT NULL, task_mode TEXT NOT NULL DEFAULT 'login', idempotency_key TEXT UNIQUE, proxy_mode TEXT NOT NULL DEFAULT 'direct',
  proxy_address TEXT, max_accounts_per_proxy INTEGER, total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
  task_group_id TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_groups (
  id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'submitted', total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
  failure_alerted INTEGER NOT NULL DEFAULT 0, completion_notified INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, completed_at TEXT
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
  cookie TEXT NOT NULL, csrftoken TEXT, credential_exported_at TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS execution_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT, job_item_id INTEGER, account_id INTEGER,
  worker_id TEXT, level TEXT NOT NULL, event TEXT NOT NULL, message TEXT, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_nodes (
  id TEXT PRIMARY KEY, name TEXT, status TEXT NOT NULL DEFAULT 'offline', version TEXT,
  last_heartbeat_at TEXT, current_job_item_id INTEGER, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_events (
  event_key TEXT PRIMARY KEY, created_at TEXT NOT NULL
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
        job_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(login_jobs)")}
        if "task_mode" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN task_mode TEXT NOT NULL DEFAULT 'login'")
        if "task_group_id" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN task_group_id TEXT")
        if "idempotency_key" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN idempotency_key TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_login_jobs_idempotency ON login_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL")
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

    def create_task_group(self, total_count: int = 0) -> dict:
        group_id = str(uuid.uuid4())
        with self._lock, self.conn:
            self.conn.execute("INSERT INTO task_groups(id,total_count,created_at) VALUES(?,?,?)", (group_id, total_count, utc_now()))
        return self._one("SELECT * FROM task_groups WHERE id=?", (group_id,))

    def create_job(self, accounts: list[dict], proxy: dict | None = None, *, task_mode: str = "login", task_group_id: str | None = None, idempotency_key: str | None = None) -> dict:
        if not accounts:
            raise ValueError("任务至少需要一个账号")
        proxy = proxy or {}
        task_mode = str(task_mode).strip().lower()
        if task_mode not in {"login", "register"}:
            raise ValueError("task_mode 只支持 login/register")
        mode = str(proxy.get("mode", "direct")).lower()
        if mode not in {"direct", "fixed"}:
            raise ValueError("proxy.mode 只支持 direct/fixed")
        address = proxy.get("address") if mode == "fixed" else None
        limit = proxy.get("max_accounts_per_job") if mode == "fixed" else None
        if mode == "fixed" and (not address or not isinstance(limit, int) or limit <= 0):
            raise ValueError("固定代理必须配置 address 和正整数 max_accounts_per_job")
        now, job_id = utc_now(), str(uuid.uuid4())
        with self._lock, self.conn:
            if idempotency_key:
                existing = self._one("SELECT * FROM login_jobs WHERE idempotency_key=?", (idempotency_key,))
                if existing:
                    return existing
            if task_group_id and not self._one("SELECT id FROM task_groups WHERE id=?", (task_group_id,)):
                raise ValueError("任务组不存在")
            self.conn.execute("INSERT INTO login_jobs(id,status,task_mode,idempotency_key,proxy_mode,proxy_address,max_accounts_per_proxy,total_count,task_group_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?)",
                              (job_id, "submitted", task_mode, idempotency_key, mode, address, limit, len(accounts), task_group_id, now))
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
            if task_group_id:
                self.conn.execute("UPDATE task_groups SET total_count=total_count+? WHERE id=?", (len(accounts), task_group_id))
        return self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))

    def create_relogin_job(self, account_id: int, proxy: dict | None = None) -> dict:
        account = self._one("SELECT email,password,client_id,refresh_token FROM accounts WHERE id=?", (account_id,))
        if not account:
            raise ValueError("账号不存在")
        return self.create_job([account], proxy, task_mode="login")

    def task_group(self, group_id: str):
        group = self._one("SELECT * FROM task_groups WHERE id=?", (group_id,))
        if group:
            rows = self.conn.execute("SELECT * FROM login_jobs WHERE task_group_id=? ORDER BY created_at", (group_id,)).fetchall()
            group["jobs"] = [dict(row) for row in rows]
        return group

    def refresh_task_group(self, group_id: str) -> dict | None:
        with self._lock, self.conn:
            counts = self.conn.execute("""SELECT COUNT(*), SUM(i.status='success'), SUM(i.status IN ('failed','cancelled','proxy_quota_exceeded')),
                SUM(i.status IN ('queued','running','retryable')) FROM login_job_items i JOIN login_jobs j ON j.id=i.job_id WHERE j.task_group_id=?""", (group_id,)).fetchone()
            total, success, failed, pending = counts[0], counts[1] or 0, counts[2] or 0, counts[3] or 0
            status = "completed" if total and pending == 0 else "running"
            self.conn.execute("UPDATE task_groups SET total_count=?,success_count=?,failed_count=?,status=?,completed_at=CASE WHEN ?='completed' THEN COALESCE(completed_at,?) ELSE completed_at END WHERE id=?", (total, success, failed, status, status, utc_now(), group_id))
            return self._one("SELECT * FROM task_groups WHERE id=?", (group_id,))

    def claim_task_group_failure_alert(self, group_id: str) -> bool:
        with self._lock, self.conn:
            row = self._one("SELECT failure_alerted FROM task_groups WHERE id=?", (group_id,))
            if not row or row["failure_alerted"]:
                return False
            group = self.refresh_task_group(group_id)
            if not group or group["total_count"] <= 1:
                return False
            rows = self.conn.execute("SELECT i.status FROM login_job_items i JOIN login_jobs j ON j.id=i.job_id WHERE j.task_group_id=? AND i.completed_at IS NOT NULL ORDER BY i.completed_at DESC", (group_id,)).fetchall()
            streak = 0
            for row in rows:
                if row[0] in {"failed", "cancelled", "proxy_quota_exceeded"}:
                    streak += 1
                else:
                    break
            if streak < 5:
                return False
            return True

    def mark_task_group_failure_alerted(self, group_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE task_groups SET failure_alerted=1 WHERE id=?", (group_id,))

    def claim_task_group_completion_notification(self, group_id: str) -> dict | None:
        with self._lock, self.conn:
            group = self.refresh_task_group(group_id)
            if not group or group["total_count"] <= 1 or group["status"] != "completed" or group["completion_notified"]:
                return None
            return group

    def mark_task_group_completion_notified(self, group_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE task_groups SET completion_notified=1 WHERE id=?", (group_id,))

    def pending_task_group_notifications(self) -> list[str]:
        rows = self.conn.execute("SELECT id FROM task_groups WHERE failure_alerted=0 OR completion_notified=0").fetchall()
        return [row[0] for row in rows]

    def claim_notification_event(self, event_key: str) -> bool:
        with self._lock, self.conn:
            try:
                self.conn.execute("INSERT INTO notification_events(event_key,created_at) VALUES(?,?)", (event_key, utc_now()))
                return True
            except sqlite3.IntegrityError:
                return False

    def release_notification_event(self, event_key: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("DELETE FROM notification_events WHERE event_key=?", (event_key,))

    def get_job(self, job_id: str):
        job = self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))
        if job:
            job["items"] = [dict(r) for r in self.conn.execute("SELECT i.*, a.email FROM login_job_items i JOIN accounts a ON a.id=i.account_id WHERE job_id=? ORDER BY i.id", (job_id,))]
        return job

    def job_status(self, job_id: str) -> str | None:
        row = self.conn.execute("SELECT status FROM login_jobs WHERE id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def worker_payload(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("任务不存在")
        return {
            "job_id": job_id,
            "mode": job["task_mode"],
            "proxy": {"mode": job["proxy_mode"], "address": job["proxy_address"], "max_accounts_per_job": job["max_accounts_per_proxy"]},
            "accounts": [
                {"job_item_id": item["id"], "account_id": item["account_id"], "email": item["email"],
                 **{key: self._one("SELECT password,client_id,refresh_token FROM accounts WHERE id=?", (item["account_id"],))[key]
                    for key in ("password", "client_id", "refresh_token")}}
                for item in job["items"] if item["status"] in {"queued", "running"}
            ],
        }

    def mark_items_running(self, job_id: str, worker_id: str, lease_seconds: int) -> None:
        now = utc_now()
        lease = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + lease_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            self.conn.execute("UPDATE login_job_items SET status='running', worker_id=?, started_at=COALESCE(started_at, ?), lease_expires_at=? WHERE job_id=? AND status='queued'", (worker_id, now, lease, job_id))
            self.record_log("items_claimed", f"worker={worker_id}", job_id=job_id, worker_id=worker_id)

    def renew_lease(self, job_item_id: int, worker_id: str, lease_seconds: int) -> None:
        lease = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + lease_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            updated = self.conn.execute(
                "UPDATE login_job_items SET worker_id=?, lease_expires_at=? WHERE id=? AND status='running' AND worker_id IN (?, 'dispatching')",
                (worker_id, lease, job_item_id, worker_id),
            ).rowcount
            if not updated:
                raise ValueError("任务明细不属于当前 Worker")

    def _refresh_job_state(self, job_id: str, now: str | None = None) -> None:
        counts = self.conn.execute(
            "SELECT SUM(status='success'), SUM(status IN ('failed','cancelled','proxy_quota_exceeded')), COUNT(*) "
            "FROM login_job_items WHERE job_id=?", (job_id,)
        ).fetchone()
        success, failed, total = counts[0] or 0, counts[1] or 0, counts[2]
        done = success + failed
        completed = now if done == total else None
        self.conn.execute(
            "UPDATE login_jobs SET success_count=?,failed_count=?,status=?,completed_at=CASE WHEN ? IS NOT NULL THEN ? ELSE completed_at END WHERE id=?",
            (success, failed, "completed" if done == total else "running", completed, completed, job_id),
        )

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
                self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count + 1 < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_timeout',error_message='Worker lease expired',completed_at=? WHERE id=?", (utc_now(), item[0]))
                self.record_log("worker_timeout", "任务租约过期", job_id=item[1], job_item_id=item[0], level="WARNING")
                self._refresh_job_state(item[1], utc_now())
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
            self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count + 1 < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_dispatch_failed',error_message=? WHERE job_id=? AND status='running'", (message, job_id))
            self.record_log("worker_dispatch_failed", message, job_id=job_id, level="ERROR")
            self._refresh_job_state(job_id, utc_now())

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
            if item["status"] in {"success", "failed", "cancelled", "proxy_quota_exceeded"}:
                return item
            if status not in {"success", "failed", "retryable", "proxy_failed", "rate_limited"}:
                raise ValueError(f"不支持的回调状态: {status}")
            if status == "success" and not payload.get("cookie"):
                raise ValueError("成功回调必须包含 cookie")
            now = utc_now()
            if payload.get("account_id") != item["account_id"] or payload.get("job_id") != item["job_id"]:
                raise ValueError("回调任务明细不匹配")
            retryable = status in {"retryable", "proxy_failed", "rate_limited"}
            if retryable:
                retry_count = item["retry_count"] + 1
                stored_status = "retryable" if retry_count < item["max_retries"] else "failed"
                completed_at = now if stored_status == "failed" else None
                self.conn.execute(
                    "UPDATE login_job_items SET status=?, error_code=?, error_message=?, retry_count=?, completed_at=?, lease_expires_at=NULL WHERE id=?",
                    (stored_status, payload.get("error_code"), payload.get("error_message"), retry_count, completed_at, item_id),
                )
            else:
                self.conn.execute(
                    "UPDATE login_job_items SET status=?, error_code=?, error_message=?, completed_at=?, lease_expires_at=NULL WHERE id=?",
                    (status, payload.get("error_code"), payload.get("error_message"), now, item_id),
                )
            if status == "success" and payload.get("cookie"):
                credential_exported_at = payload.get("credential_exported_at") or now
                self.conn.execute("""INSERT INTO credentials(account_id,cookie,csrftoken,credential_exported_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET cookie=excluded.cookie,csrftoken=excluded.csrftoken,
                    credential_exported_at=excluded.credential_exported_at,updated_at=excluded.updated_at
                    WHERE excluded.credential_exported_at >= credentials.credential_exported_at""",
                    (item["account_id"], payload["cookie"], payload.get("csrftoken"), credential_exported_at, now, now))
            if item.get("proxy_address"):
                self.conn.execute("""UPDATE login_job_proxy_usage SET success_count=success_count+?,failed_count=failed_count+?,last_assigned_at=? WHERE job_id=? AND proxy_address=?""",
                    (1 if status == "success" else 0, 0 if status == "success" else 1, now, item["job_id"], item["proxy_address"]))
            self.record_log("callback_received", f"status={status}", job_id=item["job_id"], job_item_id=item_id, account_id=item["account_id"], worker_id=payload.get("worker_id"))
            self._refresh_job_state(item["job_id"], now)
            job = self._one("SELECT task_group_id FROM login_jobs WHERE id=?", (item["job_id"],))
            if job and job.get("task_group_id"):
                self.refresh_task_group(job["task_group_id"])
            return self._one("SELECT * FROM login_job_items WHERE id=?", (item_id,))

    def credential(self, account_id: int):
        return self._one("SELECT * FROM credentials WHERE account_id=?", (account_id,))

    def workers(self):
        return [dict(r) for r in self.conn.execute("SELECT * FROM worker_nodes ORDER BY id")]

    def logs(self, job_id: str):
        return [dict(r) for r in self.conn.execute("SELECT * FROM execution_logs WHERE job_id=? ORDER BY id", (job_id,))]
