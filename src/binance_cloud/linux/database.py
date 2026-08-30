"""SQLite 持久化层。"""

from __future__ import annotations

import sqlite3
import threading
import uuid
import shutil
from urllib.parse import urlsplit, urlunsplit
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _redact_proxy_address(value: str | None) -> str | None:
    if not value:
        return value
    try:
        parsed = urlsplit(str(value))
        if parsed.username is None and parsed.password is None:
            return str(value)
        host = parsed.hostname or ""
        if parsed.port:
            host = f"{host}:{parsed.port}"
        return urlunsplit((parsed.scheme, "***:***@" + host, parsed.path, parsed.query, parsed.fragment))
    except ValueError:
        return "***"


SCHEMA = """
PRAGMA foreign_keys = ON;
CREATE TABLE IF NOT EXISTS accounts (
  id INTEGER PRIMARY KEY AUTOINCREMENT, email TEXT NOT NULL UNIQUE,
  password TEXT NOT NULL, client_id TEXT, refresh_token TEXT,
  status TEXT NOT NULL DEFAULT 'active', registration_state TEXT NOT NULL DEFAULT 'unknown',
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS login_jobs (
  id TEXT PRIMARY KEY, status TEXT NOT NULL, task_mode TEXT NOT NULL DEFAULT 'login', idempotency_key TEXT UNIQUE, proxy_mode TEXT NOT NULL DEFAULT 'direct',
  proxy_profile TEXT NOT NULL DEFAULT 'direct', proxy_address TEXT, max_accounts_per_proxy INTEGER, total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0, cancelled_count INTEGER NOT NULL DEFAULT 0,
  task_group_id TEXT, created_at TEXT NOT NULL, started_at TEXT, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS task_groups (
  id TEXT PRIMARY KEY, status TEXT NOT NULL DEFAULT 'submitted', total_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0, cancelled_count INTEGER NOT NULL DEFAULT 0,
  failure_alerted INTEGER NOT NULL DEFAULT 0, completion_notified INTEGER NOT NULL DEFAULT 0,
  consecutive_failed_ips INTEGER NOT NULL DEFAULT 0,
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, completed_at TEXT
);
CREATE TABLE IF NOT EXISTS login_job_items (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES login_jobs(id),
  account_id INTEGER NOT NULL REFERENCES accounts(id), status TEXT NOT NULL,
  account_email_snapshot TEXT, account_password_snapshot TEXT,
  account_client_id_snapshot TEXT, account_refresh_token_snapshot TEXT,
  retry_of_job_item_id INTEGER,
  proxy_address TEXT, error_code TEXT, error_message TEXT, started_at TEXT, completed_at TEXT,
  worker_id TEXT, lease_expires_at TEXT, retry_count INTEGER NOT NULL DEFAULT 0, max_retries INTEGER NOT NULL DEFAULT 2,
  lease_id TEXT, proxy_entry_id TEXT, dispatch_sequence INTEGER,
  UNIQUE(job_id, account_id)
);
CREATE TABLE IF NOT EXISTS login_job_proxy_usage (
  id INTEGER PRIMARY KEY AUTOINCREMENT, job_id TEXT NOT NULL REFERENCES login_jobs(id),
  proxy_address TEXT NOT NULL, assigned_count INTEGER NOT NULL DEFAULT 0,
  success_count INTEGER NOT NULL DEFAULT 0, failed_count INTEGER NOT NULL DEFAULT 0,
  last_assigned_at TEXT, UNIQUE(job_id, proxy_address)
);
CREATE TABLE IF NOT EXISTS proxy_pool_entries (
  id TEXT PRIMARY KEY, pool_id TEXT NOT NULL, position INTEGER NOT NULL,
  address TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'available',
  consecutive_failures INTEGER NOT NULL DEFAULT 0,
  assigned_count INTEGER NOT NULL DEFAULT 0, active_count INTEGER NOT NULL DEFAULT 0,
  last_used_at TEXT, last_switched_at TEXT, cooldown_until TEXT,
  UNIQUE(pool_id, position)
);
CREATE TABLE IF NOT EXISTS proxy_pools (
  id TEXT PRIMARY KEY, switch_threshold INTEGER NOT NULL DEFAULT 5,
  allow_parallel INTEGER NOT NULL DEFAULT 0, cooldown_seconds INTEGER NOT NULL DEFAULT 86400,
  stop_after_consecutive_failed_ips INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS proxy_leases (
  lease_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES login_jobs(id),
  job_item_id INTEGER NOT NULL REFERENCES login_job_items(id),
  worker_id TEXT, proxy_entry_id TEXT NOT NULL REFERENCES proxy_pool_entries(id),
  state TEXT NOT NULL DEFAULT 'assigned', dispatch_sequence INTEGER,
  assigned_at TEXT NOT NULL, expires_at TEXT NOT NULL,
  released_at TEXT, release_reason TEXT, result_status TEXT,
  UNIQUE(lease_id)
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
  last_heartbeat_at TEXT, current_job_item_id INTEGER, capacity INTEGER NOT NULL DEFAULT 1,
  active_slots INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL, updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS notification_events (
  event_key TEXT PRIMARY KEY, created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS worker_active_items (
  worker_id TEXT NOT NULL, job_item_id INTEGER NOT NULL, last_heartbeat_at TEXT NOT NULL,
  PRIMARY KEY(worker_id, job_item_id)
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
        account_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(accounts)")}
        if "registration_state" not in account_columns:
            self.conn.execute("ALTER TABLE accounts ADD COLUMN registration_state TEXT NOT NULL DEFAULT 'unknown'")
        if "task_mode" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN task_mode TEXT NOT NULL DEFAULT 'login'")
        if "task_group_id" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN task_group_id TEXT")
        if "idempotency_key" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN idempotency_key TEXT")
            self.conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_login_jobs_idempotency ON login_jobs(idempotency_key) WHERE idempotency_key IS NOT NULL")
        if "cancelled_count" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN cancelled_count INTEGER NOT NULL DEFAULT 0")
        if "proxy_profile" not in job_columns:
            self.conn.execute("ALTER TABLE login_jobs ADD COLUMN proxy_profile TEXT NOT NULL DEFAULT 'direct'")
        group_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(task_groups)")}
        if "cancelled_count" not in group_columns:
            self.conn.execute("ALTER TABLE task_groups ADD COLUMN cancelled_count INTEGER NOT NULL DEFAULT 0")
        if "consecutive_failed_ips" not in group_columns:
            self.conn.execute("ALTER TABLE task_groups ADD COLUMN consecutive_failed_ips INTEGER NOT NULL DEFAULT 0")
        if "consecutive_failures" not in group_columns:
            self.conn.execute("ALTER TABLE task_groups ADD COLUMN consecutive_failures INTEGER NOT NULL DEFAULT 0")
        columns = {row[1] for row in self.conn.execute("PRAGMA table_info(login_job_items)")}
        for name, definition in (("worker_id", "TEXT"), ("lease_expires_at", "TEXT"), ("retry_count", "INTEGER NOT NULL DEFAULT 0"), ("proxy_retry_count", "INTEGER NOT NULL DEFAULT 0"), ("max_retries", "INTEGER NOT NULL DEFAULT 2"), ("lease_id", "TEXT"), ("proxy_entry_id", "TEXT"), ("dispatch_sequence", "INTEGER"), ("account_email_snapshot", "TEXT"), ("account_password_snapshot", "TEXT"), ("account_client_id_snapshot", "TEXT"), ("account_refresh_token_snapshot", "TEXT"), ("retry_of_job_item_id", "INTEGER")):
            if name not in columns:
                self.conn.execute(f"ALTER TABLE login_job_items ADD COLUMN {name} {definition}")
        pool_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(proxy_pool_entries)")}
        if "cooldown_until" not in pool_columns:
            self.conn.execute("ALTER TABLE proxy_pool_entries ADD COLUMN cooldown_until TEXT")
        config_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(proxy_pools)")}
        if "cooldown_seconds" not in config_columns:
            self.conn.execute("ALTER TABLE proxy_pools ADD COLUMN cooldown_seconds INTEGER NOT NULL DEFAULT 86400")
        if "stop_after_consecutive_failed_ips" not in config_columns:
            self.conn.execute("ALTER TABLE proxy_pools ADD COLUMN stop_after_consecutive_failed_ips INTEGER NOT NULL DEFAULT 0")
        worker_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(worker_nodes)")}
        if "capacity" not in worker_columns:
            self.conn.execute("ALTER TABLE worker_nodes ADD COLUMN capacity INTEGER NOT NULL DEFAULT 1")
        if "active_slots" not in worker_columns:
            self.conn.execute("ALTER TABLE worker_nodes ADD COLUMN active_slots INTEGER NOT NULL DEFAULT 0")
        lease_sql = self.conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='proxy_leases'").fetchone()
        if lease_sql and "UNIQUE(job_item_id, state)" in (lease_sql[0] or ""):
            self.conn.execute("ALTER TABLE proxy_leases RENAME TO proxy_leases_legacy")
            self.conn.execute("""CREATE TABLE proxy_leases (
              lease_id TEXT PRIMARY KEY, job_id TEXT NOT NULL REFERENCES login_jobs(id),
              job_item_id INTEGER NOT NULL REFERENCES login_job_items(id), worker_id TEXT,
              proxy_entry_id TEXT NOT NULL REFERENCES proxy_pool_entries(id), state TEXT NOT NULL DEFAULT 'assigned',
              dispatch_sequence INTEGER, assigned_at TEXT NOT NULL, expires_at TEXT NOT NULL,
              released_at TEXT, release_reason TEXT, result_status TEXT, UNIQUE(lease_id)
            )""")
            self.conn.execute("""INSERT INTO proxy_leases(lease_id,job_id,job_item_id,worker_id,proxy_entry_id,state,dispatch_sequence,assigned_at,expires_at,released_at,release_reason,result_status)
              SELECT lease_id,job_id,job_item_id,worker_id,proxy_entry_id,state,dispatch_sequence,assigned_at,expires_at,released_at,release_reason,result_status FROM proxy_leases_legacy""")
            self.conn.execute("DROP TABLE proxy_leases_legacy")
        credential_columns = {row[1] for row in self.conn.execute("PRAGMA table_info(credentials)")}
        legacy_credential_columns = {"cookie_expires_at", "credential_updated_at"} & credential_columns
        if legacy_credential_columns:
            exported_expression = "credential_exported_at" if "credential_exported_at" in credential_columns else "credential_updated_at"
            self.conn.execute("DROP TABLE IF EXISTS credentials_new")
            self.conn.execute("""CREATE TABLE credentials_new (
                id INTEGER PRIMARY KEY AUTOINCREMENT, account_id INTEGER NOT NULL UNIQUE REFERENCES accounts(id),
                cookie TEXT NOT NULL, csrftoken TEXT, credential_exported_at TEXT NOT NULL,
                created_at TEXT NOT NULL, updated_at TEXT NOT NULL
            )""")
            self.conn.execute(f"""INSERT INTO credentials_new(id,account_id,cookie,csrftoken,credential_exported_at,created_at,updated_at)
                SELECT id,account_id,cookie,csrftoken,COALESCE({exported_expression},updated_at,created_at),created_at,updated_at
                FROM credentials""")
            self.conn.execute("DROP TABLE credentials")
            self.conn.execute("ALTER TABLE credentials_new RENAME TO credentials")
        elif "credential_exported_at" not in credential_columns:
            self.conn.execute("ALTER TABLE credentials ADD COLUMN credential_exported_at TEXT")
            self.conn.execute("UPDATE credentials SET credential_exported_at=COALESCE(updated_at, created_at) WHERE credential_exported_at IS NULL")
    def close(self) -> None:
        self.conn.close()

    def _one(self, sql: str, params=()):
        row = self.conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def configure_proxy_pool(self, pool_id: str, addresses: list[str], *, switch_threshold: int = 3, allow_parallel: bool = False, cooldown_seconds: int = 86400, stop_after_consecutive_failed_ips: int = 0) -> list[dict]:
        """按文件顺序写入固定代理池；不做随机、循环或动态出口去重。"""
        pool_id = str(pool_id or "").strip()
        values = [str(address or "").strip() for address in addresses if str(address or "").strip()]
        if not pool_id:
            raise ValueError("proxy pool_id 不能为空")
        if not values:
            raise ValueError("代理池至少需要一个代理地址")
        if isinstance(switch_threshold, bool) or int(switch_threshold) <= 0:
            raise ValueError("代理池切换阈值必须是正整数")
        switch_threshold = int(switch_threshold)
        if isinstance(cooldown_seconds, bool) or int(cooldown_seconds) <= 0:
            raise ValueError("代理冷却时间必须是正整数秒")
        cooldown_seconds = int(cooldown_seconds)
        if isinstance(stop_after_consecutive_failed_ips, bool) or int(stop_after_consecutive_failed_ips) < 0:
            raise ValueError("连续失败 IP 阈值必须是非负整数")
        stop_after_consecutive_failed_ips = int(stop_after_consecutive_failed_ips)
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute(
                "INSERT INTO proxy_pools(id,switch_threshold,allow_parallel,cooldown_seconds,stop_after_consecutive_failed_ips,created_at,updated_at) VALUES(?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET switch_threshold=excluded.switch_threshold,allow_parallel=excluded.allow_parallel,cooldown_seconds=excluded.cooldown_seconds,stop_after_consecutive_failed_ips=excluded.stop_after_consecutive_failed_ips,updated_at=excluded.updated_at",
                (pool_id, switch_threshold, 1 if allow_parallel else 0, cooldown_seconds, stop_after_consecutive_failed_ips, now, now),
            )
            existing = self.conn.execute(
                "SELECT id,status FROM proxy_pool_entries WHERE pool_id=? ORDER BY position", (pool_id,)
            ).fetchall()
            current_id = next((row[0] for row in existing if row[1] == "active"), None)
            valid_ids = [f"{pool_id}:{i}" for i in range(len(values))]
            placeholders = ",".join("?" for _ in valid_ids)
            self.conn.execute(
                f"UPDATE proxy_pool_entries SET status='disabled' WHERE pool_id=? AND id NOT IN ({placeholders})",
                (pool_id, *valid_ids),
            )
            if current_id not in valid_ids:
                current_id = None
            # 重载代理文件时，不能因没有 active 条目就把冷却 IP 提前复活。
            # 仅从新增或原本可用的条目中选一个作为 active；全部仍在冷却时保持无 active。
            if current_id is None:
                for position, address in enumerate(values):
                    entry_id = f"{pool_id}:{position}"
                    old = self._one("SELECT status,address FROM proxy_pool_entries WHERE id=?", (entry_id,))
                    if old is None or old["address"] != address or old["status"] == "available":
                        current_id = entry_id
                        break
            for position, address in enumerate(values):
                entry_id = f"{pool_id}:{position}"
                old = self._one("SELECT status,address FROM proxy_pool_entries WHERE id=?", (entry_id,))
                changed = old and old["address"] != address
                status = "active" if current_id == entry_id else ("available" if changed else (old["status"] if old and old["status"] in {"exhausted", "disabled", "cooling"} else "available"))
                self.conn.execute(
                    """INSERT INTO proxy_pool_entries(id,pool_id,position,address,status,last_switched_at)
                       VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET address=excluded.address,position=excluded.position,status=excluded.status,
                       consecutive_failures=CASE WHEN excluded.address != proxy_pool_entries.address THEN 0 ELSE proxy_pool_entries.consecutive_failures END,
                       cooldown_until=CASE WHEN excluded.address != proxy_pool_entries.address THEN NULL ELSE proxy_pool_entries.cooldown_until END""",
                    (entry_id, pool_id, position, address, status, now if status == "active" else None),
                )
            return [dict(row) for row in self.conn.execute("SELECT * FROM proxy_pool_entries WHERE pool_id=? ORDER BY position", (pool_id,))]

    def update_proxy_pool_policy(self, pool_id: str, *, switch_threshold: int, stop_after_consecutive_failed_ips: int, cooldown_seconds: int) -> dict:
        """运行时更新代理策略；只影响后续结算，不重置已有 lease 或失败计数。"""
        if isinstance(switch_threshold, bool) or int(switch_threshold) <= 0:
            raise ValueError("switch_after_account_failures 必须是正整数")
        if isinstance(stop_after_consecutive_failed_ips, bool) or int(stop_after_consecutive_failed_ips) <= 0:
            raise ValueError("stop_after_consecutive_failed_ips 必须是正整数")
        if isinstance(cooldown_seconds, bool) or int(cooldown_seconds) <= 0:
            raise ValueError("cooldown_seconds 必须是正整数")
        with self._lock, self.conn:
            updated = self.conn.execute("UPDATE proxy_pools SET switch_threshold=?,stop_after_consecutive_failed_ips=?,cooldown_seconds=?,updated_at=? WHERE id=?", (int(switch_threshold), int(stop_after_consecutive_failed_ips), int(cooldown_seconds), utc_now(), pool_id)).rowcount
            if not updated:
                raise ValueError("代理池不存在")
            return self._one("SELECT * FROM proxy_pools WHERE id=?", (pool_id,))

    def acquire_proxy_lease(
        self, pool_id: str, job_id: str, job_item_id: int, worker_id: str | None, lease_seconds: int,
        *, dispatch_sequence: int | None = None,
    ) -> dict:
        if lease_seconds <= 0:
            raise ValueError("lease_seconds 必须是正整数")
        now_dt = datetime.now(timezone.utc)
        expires_at = datetime.fromtimestamp(now_dt.timestamp() + lease_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            now_iso = utc_now()
            self.conn.execute("UPDATE proxy_pool_entries SET status='available', cooldown_until=NULL, consecutive_failures=0 WHERE pool_id=? AND status='cooling' AND cooldown_until IS NOT NULL AND cooldown_until <= ?", (pool_id, now_iso))
            entry = self._one(
                "SELECT * FROM proxy_pool_entries WHERE pool_id=? AND status IN ('active','available') AND active_count=0 ORDER BY CASE WHEN status='active' THEN 0 ELSE 1 END, position LIMIT 1",
                (pool_id,),
            )
            if not entry:
                raise ValueError("代理池没有 active entry")
            previous = self._one(
                "SELECT * FROM proxy_leases WHERE job_item_id=? AND state IN ('assigned','running')",
                (job_item_id,),
            )
            if previous:
                raise ValueError("任务明细已有未释放代理租约")
            lease_id = str(uuid.uuid4())
            self.conn.execute(
                """INSERT INTO proxy_leases(lease_id,job_id,job_item_id,worker_id,proxy_entry_id,state,dispatch_sequence,assigned_at,expires_at)
                   VALUES(?,?,?,?,?,'assigned',?,?,?)""",
                (lease_id, job_id, job_item_id, worker_id, entry["id"], dispatch_sequence, utc_now(), expires_at),
            )
            self.conn.execute(
                "UPDATE proxy_pool_entries SET assigned_count=assigned_count+1,active_count=active_count+1,last_used_at=? WHERE id=?",
                (utc_now(), entry["id"]),
            )
            self.conn.execute(
                "UPDATE login_job_items SET lease_id=?,proxy_entry_id=?,proxy_address=?,dispatch_sequence=?,worker_id=?,lease_expires_at=? WHERE id=? AND status IN ('queued','retryable','running')",
                (lease_id, entry["id"], entry["address"], dispatch_sequence, worker_id, expires_at, job_item_id),
            )
            return self._one("SELECT * FROM proxy_leases WHERE lease_id=?", (lease_id,))

    def release_proxy_lease(self, lease_id: str, *, result_status: str, release_reason: str = "completed") -> dict:
        """释放租约并幂等结算活动计数；IP 切换由后续阶段单独处理。"""
        with self._lock, self.conn:
            lease = self._one("SELECT * FROM proxy_leases WHERE lease_id=?", (lease_id,))
            if not lease:
                raise ValueError("代理租约不存在")
            if lease["state"] in {"released", "expired"}:
                return lease
            now = utc_now()
            self.conn.execute(
                "UPDATE proxy_leases SET state='released',released_at=?,release_reason=?,result_status=? WHERE lease_id=? AND state IN ('assigned','running')",
                (now, release_reason, result_status, lease_id),
            )
            entry = self._one("SELECT * FROM proxy_pool_entries WHERE id=?", (lease["proxy_entry_id"],))
            if entry:
                # 统一按账号最终结果计数：除成功外均视为该 IP 的一次失败。
                # retryable 只是中间状态，不代表账号最终失败；其租约释放不应污染 IP 计数。
                failure_increment = 0 if result_status in {"success", "already_registered", "retryable"} else 1
                next_failures = entry["consecutive_failures"] + failure_increment
                self.conn.execute(
                    "UPDATE proxy_pool_entries SET active_count=MAX(active_count-1,0),consecutive_failures=?,last_used_at=? WHERE id=?",
                    (0 if result_status in {"success", "already_registered"} else next_failures, now, lease["proxy_entry_id"]),
                )
                job_ref = self._one("SELECT task_group_id FROM login_jobs WHERE id=?", (lease["job_id"],))
                if result_status in {"success", "already_registered"} and job_ref and job_ref.get("task_group_id"):
                    self.conn.execute("UPDATE task_groups SET consecutive_failed_ips=0 WHERE id=?", (job_ref["task_group_id"],))
                if failure_increment:
                    pool = self._one("SELECT * FROM proxy_pools WHERE id=?", (entry["pool_id"],))
                    threshold = pool["switch_threshold"] if pool else 5
                    if next_failures >= threshold:
                        pool = self._one("SELECT * FROM proxy_pools WHERE id=?", (entry["pool_id"],))
                        cooldown_seconds = pool["cooldown_seconds"] if pool else 86400
                        cooldown_until = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + cooldown_seconds, timezone.utc).isoformat()
                        self.conn.execute("UPDATE proxy_pool_entries SET status='cooling',cooldown_until=?,last_switched_at=? WHERE id=? AND status IN ('active','available')", (cooldown_until, now, entry["id"]))
                        # 仅在 IP 首次达到阈值时计入任务的连续失败 IP 链，避免重复回调重复累计。
                        stop_threshold = int((pool or {}).get("stop_after_consecutive_failed_ips") or 0)
                        if stop_threshold > 0:
                            job = job_ref
                            if job and job.get("task_group_id"):
                                self.conn.execute("UPDATE task_groups SET consecutive_failed_ips=consecutive_failed_ips+1 WHERE id=?", (job["task_group_id"],))
                                self.record_log("proxy_ip_failed", f"proxy_entry={entry['id']} consecutive_failed_ips threshold={stop_threshold}", job_id=lease["job_id"], level="WARNING")
                            else:
                                self.record_log("proxy_ip_failed", f"proxy_entry={entry['id']} threshold={threshold}", job_id=lease["job_id"], level="WARNING")
                        nxt = self._one("SELECT id FROM proxy_pool_entries WHERE pool_id=? AND status='available' AND active_count=0 ORDER BY position LIMIT 1", (entry["pool_id"],))
                        if nxt:
                            self.conn.execute("UPDATE proxy_pool_entries SET status='active',last_switched_at=? WHERE id=?", (now, nxt["id"]))
            self.conn.execute(
                "UPDATE login_job_items SET lease_expires_at=NULL WHERE id=? AND lease_id=?",
                (lease["job_item_id"], lease_id),
            )
            if lease.get("worker_id"):
                self.conn.execute("DELETE FROM worker_active_items WHERE worker_id=? AND job_item_id=?", (lease["worker_id"], lease["job_item_id"]))
            if lease.get("worker_id"):
                self.release_worker_slot(lease["worker_id"])
            return self._one("SELECT * FROM proxy_leases WHERE lease_id=?", (lease_id,))

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

    def create_job(self, accounts: list[dict], proxy: dict | None = None, *, task_mode: str = "login", task_group_id: str | None = None, idempotency_key: str | None = None, update_accounts: bool = True) -> dict:
        if not accounts:
            raise ValueError("任务至少需要一个账号")
        proxy = proxy or {}
        task_mode = str(task_mode).strip().lower()
        if task_mode not in {"login", "register"}:
            raise ValueError("task_mode 只支持 login/register")
        mode = str(proxy.get("mode", "direct")).lower()
        requested_profile = proxy.get("proxy_profile") or proxy.get("profile")
        profile = str(requested_profile or mode).strip().lower()
        if mode not in {"direct", "fixed", "dynamic", "rotating_single_ip"}:
            raise ValueError("proxy.mode 只支持 direct/fixed/dynamic/rotating_single_ip")
        if mode == "direct":
            profile = "direct"
        if mode == "fixed":
            profile = "static"
        if mode == "rotating_single_ip":
            profile = "rotating_single_ip"
        if profile not in {"direct", "static", "dynamic", "rotating_single_ip"}:
            raise ValueError("proxy_profile 只支持 direct/static/dynamic/rotating_single_ip")
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
            if not task_group_id:
                task_group_id = str(uuid.uuid4())
                self.conn.execute("INSERT INTO task_groups(id,total_count,created_at) VALUES(?,?,?)", (task_group_id, 0, now))
            if task_group_id and not self._one("SELECT id FROM task_groups WHERE id=?", (task_group_id,)):
                raise ValueError("任务组不存在")
            self.conn.execute("INSERT INTO login_jobs(id,status,task_mode,idempotency_key,proxy_mode,proxy_profile,proxy_address,max_accounts_per_proxy,total_count,task_group_id,created_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                              (job_id, "submitted", task_mode, idempotency_key, mode, profile, address, limit, len(accounts), task_group_id, now))
            quota_exceeded = 0
            for index, account in enumerate(accounts):
                if update_accounts:
                    self.conn.execute("""INSERT INTO accounts(email,password,client_id,refresh_token,created_at,updated_at)
                        VALUES(?,?,?,?,?,?) ON CONFLICT(email) DO UPDATE SET password=excluded.password,client_id=excluded.client_id,refresh_token=excluded.refresh_token,updated_at=excluded.updated_at""",
                                      (account["email"], account["password"], account.get("client_id"), account.get("refresh_token"), now, now))
                row = self._one("SELECT id FROM accounts WHERE email=?", (account["email"],))
                if not row:
                    raise ValueError(f"重派账号不存在: {account['email']}")
                account_state = self._one("SELECT registration_state FROM accounts WHERE id=?", (row["id"],))["registration_state"]
                # 已知业务状态在创建时直接终止，绝不再把账号派给错误场景。
                item_status = "queued"
                item_error_code = None
                item_error_message = None
                if task_mode == "register" and account_state == "registered":
                    item_status = "already_registered"
                    item_error_code = "already_registered"
                    item_error_message = "账号已知已注册，跳过注册任务"
                elif task_mode == "login" and account_state == "unregistered":
                    item_status = "need_register"
                    item_error_code = "need_register"
                    item_error_message = "账号已知未注册，跳过登录任务"
                if mode == "fixed" and index >= limit:
                    item_status = "proxy_quota_exceeded"
                    quota_exceeded += 1
                self.conn.execute(
                    """INSERT INTO login_job_items(job_id,account_id,status,proxy_address,error_code,error_message,completed_at,
                         account_email_snapshot,account_password_snapshot,
                         account_client_id_snapshot,account_refresh_token_snapshot,retry_of_job_item_id)
                       VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (job_id, row["id"], item_status, address, item_error_code, item_error_message,
                     now if item_status in {"already_registered", "need_register"} else None,
                     account["email"], account["password"],
                     account.get("client_id"), account.get("refresh_token"), account.get("retry_of_job_item_id")),
                )
            if quota_exceeded:
                self.conn.execute("UPDATE login_jobs SET failed_count=? WHERE id=?", (quota_exceeded, job_id))
            if mode == "fixed":
                self.conn.execute("INSERT INTO login_job_proxy_usage(job_id,proxy_address,assigned_count,last_assigned_at) VALUES(?,?,?,?)", (job_id, address, min(len(accounts), limit), now))
            if task_group_id:
                self.conn.execute("UPDATE task_groups SET total_count=total_count+? WHERE id=?", (len(accounts), task_group_id))
        return self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))

    def route_need_register_to_child_job(self, source_item_id: int) -> dict | None:
        """登录发现未注册时，以原账号快照在同一任务组创建一个注册子任务。"""
        with self._lock, self.conn:
            row = self._one(
                """SELECT i.*, j.task_mode, j.task_group_id, j.proxy_mode, j.proxy_profile,
                          j.proxy_address, j.max_accounts_per_proxy,
                          a.email AS account_email, a.password AS account_password,
                          a.client_id AS account_client_id, a.refresh_token AS account_refresh_token
                     FROM login_job_items i JOIN login_jobs j ON j.id=i.job_id
                     JOIN accounts a ON a.id=i.account_id WHERE i.id=?""",
                (source_item_id,),
            )
            if not row or row["task_mode"] != "login" or row["status"] != "need_register":
                return None
            existing = self._one(
                "SELECT id FROM login_job_items WHERE retry_of_job_item_id=? AND job_id IN (SELECT id FROM login_jobs WHERE task_mode='register')",
                (source_item_id,),
            )
            if existing:
                return None
            account = {
                "email": row["account_email_snapshot"] or row["account_email"],
                "password": row["account_password_snapshot"] or row["account_password"],
                "client_id": row["account_client_id_snapshot"] or row["account_client_id"],
                "refresh_token": row["account_refresh_token_snapshot"] or row["account_refresh_token"],
                "retry_of_job_item_id": source_item_id,
            }
            proxy = {"mode": row["proxy_mode"], "proxy_profile": row["proxy_profile"],
                     "address": row["proxy_address"], "max_accounts_per_job": row["max_accounts_per_proxy"]}
            job = self.create_job([account], proxy, task_mode="register", task_group_id=row["task_group_id"], update_accounts=False)
            self.record_log("need_register_routed", f"register_job={job['id']}", job_id=row["job_id"], job_item_id=source_item_id, account_id=row["account_id"], level="INFO")
            return job

    def create_relogin_job(self, account_id: int, proxy: dict | None = None) -> dict:
        account = self._one("SELECT email,password,client_id,refresh_token FROM accounts WHERE id=?", (account_id,))
        if not account:
            raise ValueError("账号不存在")
        return self.create_job([account], proxy, task_mode="login")

    def failed_items(self, job_id: str) -> list[dict]:
        """返回可重派的最终失败项，不返回密码或邮箱 OAuth 凭证。"""
        if not self._one("SELECT id FROM login_jobs WHERE id=?", (job_id,)):
            raise ValueError("任务不存在")
        items = [dict(row) for row in self.conn.execute(
            """SELECT i.id AS job_item_id, i.account_id,
                      COALESCE(i.account_email_snapshot, a.email) AS email,
                      i.status, i.error_code, i.error_message, i.worker_id,
                      i.proxy_address, i.proxy_entry_id, i.dispatch_sequence,
                      i.retry_count, i.proxy_retry_count, i.started_at, i.completed_at,
                      i.retry_of_job_item_id
                 FROM login_job_items i JOIN accounts a ON a.id=i.account_id
                WHERE i.job_id=? AND i.status='failed' ORDER BY i.id""",
            (job_id,),
        )]
        for item in items:
            item["proxy_address"] = _redact_proxy_address(item.get("proxy_address"))
        return items

    def create_failed_items_retry_job(self, source_job_id: str, item_ids: list[int] | None = None, proxy: dict | None = None, idempotency_key: str | None = None) -> dict:
        """基于失败项创建时快照重派，不因账号表后续更新而改变历史凭据。"""
        source = self._one("SELECT * FROM login_jobs WHERE id=?", (source_job_id,))
        if not source:
            raise ValueError("源任务不存在")
        failed = self.failed_items(source_job_id)
        allowed_ids = {row["job_item_id"] for row in failed}
        selected_ids = list(allowed_ids) if item_ids is None else item_ids
        if not selected_ids:
            raise ValueError("源任务没有可重派的失败账号")
        if len(selected_ids) != len(set(selected_ids)):
            raise ValueError("任务明细 ID 不可重复")
        invalid_ids = [item_id for item_id in selected_ids if item_id not in allowed_ids]
        if invalid_ids:
            raise ValueError(f"仅可重派当前任务最终失败项: {invalid_ids}")

        rows = {row["id"]: row for row in self.conn.execute(
            """SELECT i.*, a.email AS account_email, a.password AS account_password,
                      a.client_id AS account_client_id, a.refresh_token AS account_refresh_token
                 FROM login_job_items i JOIN accounts a ON a.id=i.account_id
                WHERE i.job_id=?""",
            (source_job_id,),
        )}
        accounts = [
            {
                "email": row["account_email_snapshot"] or row["account_email"],
                "password": row["account_password_snapshot"] or row["account_password"],
                "client_id": row["account_client_id_snapshot"] or row["account_client_id"],
                "refresh_token": row["account_refresh_token_snapshot"] or row["account_refresh_token"],
                "retry_of_job_item_id": item_id,
            }
            for item_id in selected_ids
            for row in [rows[item_id]]
        ]
        inherited_proxy = {
            "mode": source["proxy_mode"],
            "proxy_profile": source["proxy_profile"],
            "address": source["proxy_address"],
            "max_accounts_per_job": source["max_accounts_per_proxy"],
        }
        return self.create_job(
            accounts,
            proxy if proxy is not None else inherited_proxy,
            task_mode=source["task_mode"],
            idempotency_key=idempotency_key,
            update_accounts=False,
        )

    def task_group(self, group_id: str):
        group = self._one("SELECT * FROM task_groups WHERE id=?", (group_id,))
        if group:
            rows = self.conn.execute("SELECT * FROM login_jobs WHERE task_group_id=? ORDER BY created_at", (group_id,)).fetchall()
            group["jobs"] = [dict(row) for row in rows]
        return group

    def refresh_task_group(self, group_id: str) -> dict | None:
        with self._lock, self.conn:
            counts = self.conn.execute("""SELECT COUNT(*), SUM(i.status IN ('success','already_registered','need_register')), SUM(i.status IN ('failed','proxy_quota_exceeded')), SUM(i.status='cancelled'),
                SUM(i.status IN ('queued','running','retryable')) FROM login_job_items i JOIN login_jobs j ON j.id=i.job_id WHERE j.task_group_id=?""", (group_id,)).fetchone()
            total, success, failed, cancelled, pending = counts[0], counts[1] or 0, counts[2] or 0, counts[3] or 0, counts[4] or 0
            status = "completed" if total and pending == 0 else "running"
            self.conn.execute("UPDATE task_groups SET total_count=?,success_count=?,failed_count=?,cancelled_count=?,status=?,completed_at=CASE WHEN ?='completed' THEN COALESCE(completed_at,?) ELSE completed_at END WHERE id=?", (total, success, failed, cancelled, status, status, utc_now(), group_id))
            return self._one("SELECT * FROM task_groups WHERE id=?", (group_id,))

    def claim_task_group_failure_alert(self, group_id: str, threshold: int, failure_mode: str = "fixed_pool") -> bool:
        with self._lock, self.conn:
            row = self._one("SELECT failure_alerted FROM task_groups WHERE id=?", (group_id,))
            if not row or row["failure_alerted"]:
                return False
            group = self.refresh_task_group(group_id)
            if not group or group["total_count"] <= 0:
                return False
            if failure_mode in {"dynamic", "direct"}:
                return int(group.get("consecutive_failures") or 0) >= threshold
            failed_ip_streak = int(group.get("consecutive_failed_ips") or 0)
            if failed_ip_streak:
                return failed_ip_streak >= threshold
            # 兼容未启用固定代理池的旧任务组：仍按连续最终失败账号告警。
            rows = self.conn.execute("SELECT i.status FROM login_job_items i JOIN login_jobs j ON j.id=i.job_id WHERE j.task_group_id=? AND i.completed_at IS NOT NULL ORDER BY i.completed_at DESC", (group_id,)).fetchall()
            streak = 0
            for row in rows:
                if row[0] == "failed":
                    streak += 1
                else:
                    break
            return streak >= threshold

    def cancel_task_group(self, group_id: str) -> None:
        with self._lock, self.conn:
            now = utc_now()
            item_rows = self.conn.execute("SELECT id,job_id,account_id FROM login_job_items WHERE job_id IN (SELECT id FROM login_jobs WHERE task_group_id=?) AND status IN ('queued','retryable','running')", (group_id,)).fetchall()
            leases = self.conn.execute("SELECT lease_id FROM proxy_leases WHERE job_id IN (SELECT id FROM login_jobs WHERE task_group_id=?) AND state IN ('assigned','running')", (group_id,)).fetchall()
            job_rows = self.conn.execute("SELECT id FROM login_jobs WHERE task_group_id=? AND status NOT IN ('completed','cancelled')", (group_id,)).fetchall()
            self.conn.execute("UPDATE login_job_items SET status='cancelled',completed_at=? WHERE job_id IN (SELECT id FROM login_jobs WHERE task_group_id=?) AND status IN ('queued','retryable','running')", (now, group_id))
            for lease in leases:
                self.release_proxy_lease(lease[0], result_status="retryable", release_reason="task_group_cancelled")
            for row in job_rows:
                self._refresh_job_state(row[0], now)
            self.conn.execute("UPDATE login_jobs SET status='cancelled',completed_at=? WHERE task_group_id=? AND status NOT IN ('completed','cancelled')", (now, group_id))
            for row in job_rows:
                self.record_log("job_cancelled", "任务组连续失败达到阈值，任务停止", job_id=row[0], level="WARNING")
            for row in item_rows:
                self.record_log("account_cancelled", "任务组连续失败达到阈值，账号未完成执行", job_id=row[1], job_item_id=row[0], account_id=row[2], level="WARNING")
            self.refresh_task_group(group_id)
            self.conn.execute("UPDATE task_groups SET status='cancelled', completion_notified=1 WHERE id=?", (group_id,))

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
            job["items"] = [dict(r) for r in self.conn.execute(
                """SELECT i.id, i.job_id, i.account_id, i.status,
                          COALESCE(i.account_email_snapshot, a.email) AS email,
                          i.proxy_address, i.error_code, i.error_message,
                          i.started_at, i.completed_at, i.worker_id, i.lease_expires_at,
                          i.retry_count, i.proxy_retry_count, i.max_retries, i.lease_id,
                          i.proxy_entry_id, i.dispatch_sequence, i.retry_of_job_item_id
                     FROM login_job_items i JOIN accounts a ON a.id=i.account_id
                    WHERE i.job_id=? ORDER BY i.id""",
                (job_id,),
            )]
        return job

    def job_status(self, job_id: str) -> str | None:
        row = self.conn.execute("SELECT status FROM login_jobs WHERE id=?", (job_id,)).fetchone()
        return row[0] if row else None

    def worker_payload(self, job_id: str) -> dict:
        job = self.get_job(job_id)
        if not job:
            raise ValueError("任务不存在")
        items = [dict(row) for row in self.conn.execute(
            "SELECT i.*, COALESCE(i.account_email_snapshot, a.email) AS email "
            "FROM login_job_items i JOIN accounts a ON a.id=i.account_id WHERE i.job_id=? ORDER BY i.id",
            (job_id,),
        )]
        return {
            "job_id": job_id,
            "mode": job["task_mode"],
            "proxy": {"mode": job["proxy_mode"], "proxy_profile": job.get("proxy_profile"), "address": job["proxy_address"], "max_accounts_per_job": job["max_accounts_per_proxy"]},
            "proxy_profile": job.get("proxy_profile") or job["proxy_mode"],
            "accounts": [
                {"job_item_id": item["id"], "account_id": item["account_id"], "email": item["email"],
                 "lease_id": item.get("lease_id"), "proxy_entry_id": item.get("proxy_entry_id"), "dispatch_sequence": item.get("dispatch_sequence"),
                 "password": item.get("account_password_snapshot") or self._one("SELECT password FROM accounts WHERE id=?", (item["account_id"],))["password"],
                 "client_id": item.get("account_client_id_snapshot") or self._one("SELECT client_id FROM accounts WHERE id=?", (item["account_id"],))["client_id"],
                 "refresh_token": item.get("account_refresh_token_snapshot") or self._one("SELECT refresh_token FROM accounts WHERE id=?", (item["account_id"],))["refresh_token"]}
                for item in items if item["status"] in {"queued", "running"}
            ],
        }

    def rotating_worker_payload(self, job_id: str, worker_id: str, lease_seconds: int, *, pool_id: str = "default") -> dict | None:
        """为固定池任务原子领取一个当前 IP，再生成单账号 Worker payload。"""
        job = self.get_job(job_id)
        if not job or job["proxy_mode"] != "rotating_single_ip":
            return self.worker_payload(job_id) if job else None
        with self._lock, self.conn:
            self._skip_known_incompatible_items(job_id, job["task_mode"])
            item = self._one("SELECT id FROM login_job_items WHERE job_id=? AND status IN ('queued','retryable') ORDER BY id LIMIT 1", (job_id,))
            if not item:
                return None
            sequence_row = self.conn.execute("SELECT COALESCE(MAX(dispatch_sequence),0)+1 FROM login_job_items WHERE job_id=?", (job_id,)).fetchone()
            self.acquire_proxy_lease(pool_id, job_id, item["id"], worker_id, lease_seconds, dispatch_sequence=sequence_row[0])
            now = utc_now()
            self.conn.execute("UPDATE login_job_items SET status='running',started_at=COALESCE(started_at,?) WHERE id=?", (now, item["id"]))
            payload = self.worker_payload(job_id)
            payload["accounts"] = [account for account in payload["accounts"] if account["job_item_id"] == item["id"]]
            if not payload["accounts"]:
                raise ValueError("固定池任务租约领取后找不到任务明细")
            account = payload["accounts"][0]
            payload["proxy"] = {"mode": "fixed", "proxy_profile": job["proxy_profile"], "address": account["proxy_entry_id"] and self._one("SELECT address FROM proxy_pool_entries WHERE id=?", (account["proxy_entry_id"],))["address"]}
            self.record_log("item_claimed", f"worker={worker_id} item={item['id']} lease={account.get('lease_id')} proxy_entry={account.get('proxy_entry_id')} dispatch={account.get('dispatch_sequence')}", job_id=job_id, job_item_id=item["id"], worker_id=worker_id)
            return payload

    def next_worker_payload(self, job_id: str, worker_id: str, lease_seconds: int) -> dict | None:
        """领取普通模式的一个账号，避免批量 payload 与 Worker 槽位失配。"""
        with self._lock, self.conn:
            job = self._one("SELECT task_mode FROM login_jobs WHERE id=?", (job_id,))
            if not job:
                raise ValueError("任务不存在")
            self._skip_known_incompatible_items(job_id, job["task_mode"])
            item = self._one("SELECT id FROM login_job_items WHERE job_id=? AND status IN ('queued','retryable') ORDER BY id LIMIT 1", (job_id,))
            if not item:
                return None
            now = utc_now()
            expires = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() + lease_seconds, timezone.utc).isoformat()
            self.conn.execute("UPDATE login_job_items SET status='running',worker_id=?,started_at=COALESCE(started_at,?),lease_expires_at=? WHERE id=? AND status IN ('queued','retryable')", (worker_id, now, expires, item["id"]))
            payload = self.worker_payload(job_id)
            payload["accounts"] = [a for a in payload["accounts"] if a["job_item_id"] == item["id"]]
            self.record_log("item_claimed", f"worker={worker_id} item={item['id']}", job_id=job_id, job_item_id=item["id"], worker_id=worker_id)
            return payload if payload["accounts"] else None

    def _skip_known_incompatible_items(self, job_id: str, task_mode: str) -> int:
        """派发前复核账号注册状态，避免跨任务状态更新后仍执行错误场景。"""
        now = utc_now()
        if task_mode == "register":
            updated = self.conn.execute(
                """UPDATE login_job_items SET status='already_registered',error_code='already_registered',
                       error_message='账号已知已注册，跳过注册任务',completed_at=?,lease_expires_at=NULL
                     WHERE job_id=? AND status IN ('queued','retryable')
                       AND account_id IN (SELECT id FROM accounts WHERE registration_state='registered')""",
                (now, job_id),
            ).rowcount
        elif task_mode == "login":
            updated = self.conn.execute(
                """UPDATE login_job_items SET status='need_register',error_code='need_register',
                       error_message='账号已知未注册，跳过登录任务',completed_at=?,lease_expires_at=NULL
                     WHERE job_id=? AND status IN ('queued','retryable')
                       AND account_id IN (SELECT id FROM accounts WHERE registration_state='unregistered')""",
                (now, job_id),
            ).rowcount
        else:
            raise ValueError("task_mode 只支持 login/register")
        if updated:
            self.record_log("items_skipped_by_account_state", f"mode={task_mode} count={updated}", job_id=job_id, level="INFO")
            self._refresh_job_state(job_id, now)
        return updated

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
        current_job = self._one("SELECT status FROM login_jobs WHERE id=?", (job_id,))
        if current_job and current_job["status"] == "proxy_pool_exhausted":
            return
        counts = self.conn.execute(
            "SELECT SUM(status IN ('success','already_registered','need_register')), SUM(status IN ('failed','proxy_quota_exceeded')), SUM(status='cancelled'), COUNT(*) "
            "FROM login_job_items WHERE job_id=?", (job_id,)
        ).fetchone()
        success, failed, cancelled, total = counts[0] or 0, counts[1] or 0, counts[2] or 0, counts[3]
        done = success + failed + cancelled
        completed = now if done == total else None
        self.conn.execute(
            "UPDATE login_jobs SET success_count=?,failed_count=?,cancelled_count=?,status=?,completed_at=CASE WHEN ? IS NOT NULL THEN ? ELSE completed_at END WHERE id=?",
            (success, failed, cancelled, "completed" if done == total else "running", completed, completed, job_id),
        )

    def register_worker(self, worker_id: str, name: str = "", version: str = "") -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("""INSERT INTO worker_nodes(id,name,status,version,last_heartbeat_at,created_at,updated_at)
                VALUES(?,?, 'online', ?, ?, ?, ?) ON CONFLICT(id) DO UPDATE SET name=excluded.name,status='online',version=excluded.version,last_heartbeat_at=excluded.last_heartbeat_at,updated_at=excluded.updated_at""", (worker_id, name, version, now, now, now))
            return self._one("SELECT * FROM worker_nodes WHERE id=?", (worker_id,))

    def set_worker_capacity(self, worker_id: str, capacity: int) -> dict:
        if isinstance(capacity, bool) or int(capacity) <= 0:
            raise ValueError("Worker capacity 必须是正整数")
        with self._lock, self.conn:
            self.conn.execute("UPDATE worker_nodes SET capacity=?,updated_at=? WHERE id=?", (int(capacity), utc_now(), worker_id))
            return self._one("SELECT * FROM worker_nodes WHERE id=?", (worker_id,))

    def reserve_worker_slot(self, worker_id: str, job_item_id: int | None = None) -> bool:
        """原子预约 Worker 槽位，并可同步登记具体任务项。

        槽位和 ``worker_active_items`` 必须在同一个事务内维护。否则多个
        Cloud 派发线程会分别看到空闲容量，造成浏览器数超过 Worker 上限。
        """
        with self._lock, self.conn:
            worker = self._one("SELECT capacity,status,active_slots FROM worker_nodes WHERE id=?", (worker_id,))
            if not worker or worker["status"] != "online":
                return False
            item_used = self.conn.execute(
                "SELECT COUNT(*) FROM worker_active_items WHERE worker_id=?", (worker_id,)
            ).fetchone()[0]
            # 兼容尚未绑定具体 job_item 的旧调用，同时以明细登记纠正旧的
            # 槽位计数漂移。
            used = max(int(worker.get("active_slots") or 0), item_used)
            if used >= int(worker["capacity"]):
                self.conn.execute(
                    "UPDATE worker_nodes SET active_slots=?,updated_at=? WHERE id=?",
                    (used, utc_now(), worker_id),
                )
                return False
            if job_item_id is not None:
                self.conn.execute(
                    "INSERT INTO worker_active_items(worker_id,job_item_id,last_heartbeat_at) VALUES(?,?,?) "
                    "ON CONFLICT(worker_id,job_item_id) DO UPDATE SET last_heartbeat_at=excluded.last_heartbeat_at",
                    (worker_id, job_item_id, utc_now()),
                )
            self.conn.execute(
                "UPDATE worker_nodes SET active_slots=?,updated_at=? WHERE id=?",
                (used + 1, utc_now(), worker_id),
            )
            return True

    def release_worker_slot(self, worker_id: str, job_item_id: int | None = None) -> None:
        with self._lock, self.conn:
            if job_item_id is not None:
                self.conn.execute(
                    "DELETE FROM worker_active_items WHERE worker_id=? AND job_item_id=?",
                    (worker_id, job_item_id),
                )
            used = self.conn.execute(
                "SELECT COUNT(*) FROM worker_active_items WHERE worker_id=?", (worker_id,)
            ).fetchone()[0]
            self.conn.execute("UPDATE worker_nodes SET active_slots=?,updated_at=? WHERE id=?", (used, utc_now(), worker_id))

    def defer_dispatch_for_capacity(self, job_item_id: int, lease_id: str | None = None) -> None:
        """无可用 Worker 槽位时归还已预领资源，保留账号为待派发状态。"""
        with self._lock, self.conn:
            item = self._one("SELECT * FROM login_job_items WHERE id=?", (job_item_id,))
            if not item:
                raise ValueError("找不到任务明细")
            target_lease = lease_id or item.get("lease_id")
            if target_lease:
                self.release_proxy_lease(target_lease, result_status="retryable", release_reason="worker_capacity_full")
            self.conn.execute(
                "UPDATE login_job_items SET status='queued',worker_id=NULL,lease_id=NULL,proxy_entry_id=NULL,proxy_address=NULL,lease_expires_at=NULL WHERE id=? AND status='running'",
                (job_item_id,),
            )
            self.record_log("dispatch_deferred_capacity", "Worker 槽位已满，任务回到队列", job_id=item["job_id"], job_item_id=job_item_id, level="INFO")
            self._refresh_job_state(item["job_id"], utc_now())

    def bind_lease_worker(self, lease_id: str, worker_id: str) -> None:
        with self._lock, self.conn:
            self.conn.execute("UPDATE proxy_leases SET worker_id=? WHERE lease_id=? AND state IN ('assigned','running')", (worker_id, lease_id))
            self.conn.execute("UPDATE login_job_items SET worker_id=? WHERE lease_id=?", (worker_id, lease_id))

    def heartbeat(self, worker_id: str, current_job_item_id=None) -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("UPDATE worker_nodes SET status='online',last_heartbeat_at=?,current_job_item_id=?,updated_at=? WHERE id=?", (now, current_job_item_id, now, worker_id))
            if current_job_item_id is not None:
                self.conn.execute("INSERT INTO worker_active_items(worker_id,job_item_id,last_heartbeat_at) VALUES(?,?,?) ON CONFLICT(worker_id,job_item_id) DO UPDATE SET last_heartbeat_at=excluded.last_heartbeat_at", (worker_id, current_job_item_id, now))
            return self._one("SELECT * FROM worker_nodes WHERE id=?", (worker_id,))

    def recover_expired_items(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        stale = self.conn.execute("SELECT id,job_id FROM login_job_items WHERE status='running' AND lease_expires_at IS NOT NULL AND lease_expires_at < ?", (now,)).fetchall()
        with self._lock, self.conn:
            for item in stale:
                if item["id"]:
                    lease = self._one("SELECT lease_id FROM proxy_leases WHERE job_item_id=? AND state IN ('assigned','running')", (item["id"],))
                    if lease:
                        self.release_proxy_lease(lease["lease_id"], result_status="failed", release_reason="expired")
                self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count + 1 < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_timeout',error_message='Worker lease expired',completed_at=? WHERE id=?", (utc_now(), item[0]))
                self.record_log("worker_timeout", "任务租约过期", job_id=item[1], job_item_id=item[0], level="WARNING")
                self._refresh_job_state(item[1], utc_now())
        return len(stale)

    def mark_offline_workers(self, timeout_seconds: int = 120) -> int:
        cutoff = datetime.fromtimestamp(datetime.now(timezone.utc).timestamp() - timeout_seconds, timezone.utc).isoformat()
        with self._lock, self.conn:
            result = self.conn.execute("UPDATE worker_nodes SET status='offline',updated_at=? WHERE status='online' AND last_heartbeat_at < ?", (utc_now(), cutoff))
            self.conn.execute("DELETE FROM worker_active_items WHERE last_heartbeat_at < ?", (cutoff,))
            return result.rowcount

    def pending_jobs(self) -> list[str]:
        rows = self.conn.execute("SELECT DISTINCT job_id FROM login_job_items WHERE status IN ('queued','retryable')").fetchall()
        return [row[0] for row in rows]

    def proxy_pool_state(self, pool_id: str) -> dict:
        now = utc_now()
        with self._lock, self.conn:
            self.conn.execute("UPDATE proxy_pool_entries SET status='available',cooldown_until=NULL,consecutive_failures=0 WHERE pool_id=? AND status='cooling' AND cooldown_until IS NOT NULL AND cooldown_until <= ?", (pool_id, now))
            row = self._one("SELECT SUM(status IN ('active','available') AND active_count=0) AS available, SUM(status='cooling') AS cooling, SUM(status='active' AND active_count>0) AS leased FROM proxy_pool_entries WHERE pool_id=?", (pool_id,))
            pool = self._one("SELECT allow_parallel FROM proxy_pools WHERE id=?", (pool_id,)) or {"allow_parallel": 0}
            return {**row, "allow_parallel": bool(pool["allow_parallel"])}

    def mark_pool_exhausted(self, job_id: str) -> dict | None:
        """固定池无可领取 IP 时结束剩余明细，保留明确的任务终止原因。"""
        with self._lock, self.conn:
            job = self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))
            if not job:
                return None
            now = utc_now()
            self.conn.execute("UPDATE login_job_items SET status='failed',error_code='proxy_pool_exhausted',error_message='固定代理池没有可用 IP',completed_at=? WHERE job_id=? AND status IN ('queued','retryable')", (now, job_id))
            self._refresh_job_state(job_id, now)
            self.conn.execute("UPDATE login_jobs SET status='proxy_pool_exhausted',completed_at=? WHERE id=? AND status NOT IN ('completed','cancelled')", (now, job_id))
            self.record_log("proxy_pool_exhausted", "固定代理池没有可用 IP，任务已终止", job_id=job_id, level="ERROR")
            return self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))

    def dispatch_failed(self, job_id: str, message: str, *, job_item_id: int | None = None, lease_id: str | None = None) -> None:
        with self._lock, self.conn:
            if lease_id:
                leases = self.conn.execute("SELECT lease_id FROM proxy_leases WHERE lease_id=? AND state IN ('assigned','running')", (lease_id,)).fetchall()
            elif job_item_id:
                leases = self.conn.execute("SELECT lease_id FROM proxy_leases WHERE job_item_id=? AND state IN ('assigned','running')", (job_item_id,)).fetchall()
            else:
                leases = self.conn.execute("SELECT lease_id FROM proxy_leases WHERE job_id=? AND state IN ('assigned','running')", (job_id,)).fetchall()
            for lease in leases:
                self.release_proxy_lease(lease[0], result_status="failed", release_reason="dispatch_failed")
            if job_item_id:
                failed_item = self._one("SELECT worker_id FROM login_job_items WHERE id=?", (job_item_id,))
                self.conn.execute("UPDATE login_job_items SET status=CASE WHEN retry_count + 1 < max_retries THEN 'retryable' ELSE 'failed' END,retry_count=retry_count+1,error_code='worker_dispatch_failed',error_message=? WHERE id=? AND status='running'", (message, job_item_id))
                if not lease_id and failed_item and failed_item.get("worker_id"):
                    self.release_worker_slot(failed_item["worker_id"], job_item_id)
            else:
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
            item_rows = self.conn.execute("SELECT id,account_id FROM login_job_items WHERE job_id=? AND status IN ('queued','retryable','running')", (job_id,)).fetchall()
            leases = self.conn.execute("SELECT lease_id FROM proxy_leases WHERE job_id=? AND state IN ('assigned','running')", (job_id,)).fetchall()
            now = utc_now()
            for lease in leases:
                self.release_proxy_lease(lease[0], result_status="retryable", release_reason="job_cancelled")
            self.conn.execute("UPDATE login_job_items SET status='cancelled',completed_at=? WHERE job_id=? AND status IN ('queued','retryable','running')", (now, job_id))
            for row in item_rows:
                self.record_log("account_cancelled", "任务被取消，账号未完成执行", job_id=job_id, job_item_id=row[0], account_id=row[1], level="WARNING")
            self.conn.execute("UPDATE login_jobs SET status='cancelled',cancelled_count=cancelled_count+?,completed_at=? WHERE id=?", (len(item_rows), now, job_id))
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
            had_lease = bool(item.get("lease_id"))
            if status not in {"success", "failed", "retryable", "proxy_failed", "rate_limited", "already_registered", "need_register"}:
                raise ValueError(f"不支持的回调状态: {status}")
            if status == "success" and not payload.get("cookie"):
                raise ValueError("成功回调必须包含 cookie")
            now = utc_now()
            if payload.get("account_id") != item["account_id"] or payload.get("job_id") != item["job_id"]:
                raise ValueError("回调任务明细不匹配")
            if payload.get("lease_id") and payload.get("lease_id") != item.get("lease_id"):
                raise ValueError("回调代理租约不匹配")
            if payload.get("proxy_entry_id") and payload.get("proxy_entry_id") != item.get("proxy_entry_id"):
                raise ValueError("回调代理 entry 不匹配")
            # frequency limit/208061 已明确表示当前账号终止；保存为 failed
            # 以完成任务统计，同时保留 error_code=rate_limited 并结算代理失败。
            # 普通 proxy_failed 继续支持换代理重试。
            business_terminal = status in {"already_registered", "need_register"}
            retryable = status in {"retryable", "proxy_failed"}
            if retryable:
                retry_count = item["retry_count"] + 1
                proxy_retry_count = item.get("proxy_retry_count", 0)
                switch_retry = status == "proxy_failed" and proxy_retry_count < 1
                stored_status = "retryable" if (retry_count < item["max_retries"] or switch_retry) else "failed"
                completed_at = now if stored_status == "failed" else None
                self.conn.execute(
                    "UPDATE login_job_items SET status=?, error_code=?, error_message=?, retry_count=?, proxy_retry_count=proxy_retry_count+?, completed_at=?, lease_expires_at=NULL WHERE id=?",
                    (stored_status, payload.get("error_code"), payload.get("error_message"), retry_count, 1 if switch_retry else 0, completed_at, item_id),
                )
            else:
                stored_status = "failed" if status == "rate_limited" else status
                self.conn.execute(
                    "UPDATE login_job_items SET status=?, error_code=?, error_message=?, completed_at=?, lease_expires_at=NULL WHERE id=?",
                    (stored_status, payload.get("error_code"), payload.get("error_message"), now, item_id),
                )
            if status == "already_registered":
                self.conn.execute("UPDATE accounts SET registration_state='registered',updated_at=? WHERE id=?", (now, item["account_id"]))
            elif status == "need_register":
                self.conn.execute("UPDATE accounts SET registration_state='unregistered',updated_at=? WHERE id=?", (now, item["account_id"]))
            elif status == "success":
                self.conn.execute("UPDATE accounts SET registration_state='registered',updated_at=? WHERE id=?", (now, item["account_id"]))
            if payload.get("lease_id"):
                # 只有账号最终结束时才结算固定池失败；中间 retryable 不污染连续失败计数。
                lease_result = "success" if business_terminal else (status if (status == "rate_limited" or (retryable and stored_status == "failed")) else (stored_status if retryable else status))
                self.release_proxy_lease(payload["lease_id"], result_status=lease_result, release_reason="worker_callback")
            elif not had_lease and payload.get("worker_id"):
                self.release_worker_slot(payload["worker_id"], item_id)
            if status == "success" and payload.get("cookie"):
                credential_exported_at = payload.get("credential_exported_at") or now
                self.conn.execute("""INSERT INTO credentials(account_id,cookie,csrftoken,credential_exported_at,created_at,updated_at)
                    VALUES(?,?,?,?,?,?) ON CONFLICT(account_id) DO UPDATE SET cookie=excluded.cookie,csrftoken=excluded.csrftoken,
                    credential_exported_at=excluded.credential_exported_at,updated_at=excluded.updated_at
                    WHERE excluded.credential_exported_at >= credentials.credential_exported_at""",
                    (item["account_id"], payload["cookie"], payload.get("csrftoken"), credential_exported_at, now, now))
            # 动态/直连模式按账号最终结果维护任务组连续失败；固定池使用 IP 链单独统计。
            final_status = stored_status if retryable or status == "rate_limited" else status
            if final_status in {"success", "already_registered", "need_register"}:
                self.conn.execute("UPDATE task_groups SET consecutive_failures=0 WHERE id=(SELECT task_group_id FROM login_jobs WHERE id=?)", (item["job_id"],))
            elif final_status == "failed":
                self.conn.execute("UPDATE task_groups SET consecutive_failures=consecutive_failures+1 WHERE id=(SELECT task_group_id FROM login_jobs WHERE id=?)", (item["job_id"],))
            if item.get("proxy_address"):
                self.conn.execute("""UPDATE login_job_proxy_usage SET success_count=success_count+?,failed_count=failed_count+?,last_assigned_at=? WHERE job_id=? AND proxy_address=?""",
                    (1 if status in {"success", "already_registered", "need_register"} else 0, 0 if status in {"success", "already_registered", "need_register"} else 1, now, item["job_id"], item["proxy_address"]))
            details = " ".join(filter(None, (
                f"status={status}",
                f"error_code={payload.get('error_code')}" if payload.get("error_code") else None,
                f"proxy_profile={payload.get('proxy_profile')}" if payload.get("proxy_profile") else None,
                f"proxy_entry={payload.get('proxy_entry_id')}" if payload.get("proxy_entry_id") else None,
                f"lease={payload.get('lease_id')}" if payload.get("lease_id") else None,
                f"dispatch={payload.get('dispatch_sequence')}" if payload.get("dispatch_sequence") is not None else None,
                f"message={str(payload.get('error_message'))[:240]}" if payload.get("error_message") else None,
            )))
            self.record_log("callback_received", details, job_id=item["job_id"], job_item_id=item_id, account_id=item["account_id"], worker_id=payload.get("worker_id"), level="ERROR" if status in {"failed", "proxy_failed", "rate_limited"} else "INFO")
            self._refresh_job_state(item["job_id"], now)
            job = self._one("SELECT task_group_id FROM login_jobs WHERE id=?", (item["job_id"],))
            if job and job.get("task_group_id"):
                self.refresh_task_group(job["task_group_id"])
            return self._one("SELECT * FROM login_job_items WHERE id=?", (item_id,))

    def credential(self, account_id: int):
        return self._one("SELECT * FROM credentials WHERE account_id=?", (account_id,))

    def workers(self):
        workers = [dict(r) for r in self.conn.execute("SELECT * FROM worker_nodes ORDER BY id")]
        for worker in workers:
            worker["active_items"] = [
                dict(r) for r in self.conn.execute(
                    """SELECT a.job_item_id, a.last_heartbeat_at, i.job_id, i.account_id,
                              ac.email, i.status, i.proxy_address, i.lease_id, i.proxy_entry_id,
                              i.dispatch_sequence
                       FROM worker_active_items a
                       JOIN login_job_items i ON i.id=a.job_item_id
                       JOIN accounts ac ON ac.id=i.account_id
                      WHERE a.worker_id=? ORDER BY a.job_item_id""",
                    (worker["id"],),
                )
            ]
        return workers

    def logs(self, job_id: str):
        return [dict(r) for r in self.conn.execute("SELECT * FROM execution_logs WHERE job_id=? ORDER BY id", (job_id,))]

    def job_diagnostics(self, job_id: str) -> dict | None:
        """返回不含敏感凭证的任务诊断快照，便于一次请求定位卡在哪一层。"""
        job = self._one("SELECT * FROM login_jobs WHERE id=?", (job_id,))
        if not job:
            return None
        job["proxy_address"] = _redact_proxy_address(job.get("proxy_address"))
        items = [dict(r) for r in self.conn.execute(
            """SELECT i.id, i.account_id, a.email, i.status, i.proxy_address,
                      i.error_code, i.error_message, i.worker_id, i.lease_id,
                      i.proxy_entry_id, i.dispatch_sequence, i.retry_count,
                      i.proxy_retry_count, i.started_at, i.completed_at,
                      i.lease_expires_at
                 FROM login_job_items i JOIN accounts a ON a.id=i.account_id
                WHERE i.job_id=? ORDER BY i.id""", (job_id,)
        )]
        usage = [dict(r) for r in self.conn.execute(
            "SELECT * FROM login_job_proxy_usage WHERE job_id=? ORDER BY last_assigned_at, id", (job_id,)
        )]
        leases = [dict(r) for r in self.conn.execute(
            """SELECT l.lease_id, l.job_item_id, l.worker_id, l.proxy_entry_id,
                      p.address AS proxy_address, l.state, l.dispatch_sequence,
                      l.assigned_at, l.expires_at, l.released_at, l.release_reason,
                      l.result_status
                 FROM proxy_leases l JOIN proxy_pool_entries p ON p.id=l.proxy_entry_id
                WHERE l.job_id=? ORDER BY l.assigned_at""", (job_id,)
        )]
        group = None
        if job.get("task_group_id"):
            group = self._one("SELECT * FROM task_groups WHERE id=?", (job["task_group_id"],))
        recent_logs = [dict(r) for r in self.conn.execute(
            "SELECT * FROM execution_logs WHERE job_id=? ORDER BY id DESC LIMIT 50", (job_id,)
        )]
        for row in items:
            row["proxy_address"] = _redact_proxy_address(row.get("proxy_address"))
        for row in usage:
            row["proxy_address"] = _redact_proxy_address(row.get("proxy_address"))
        for row in leases:
            row["proxy_address"] = _redact_proxy_address(row.get("proxy_address"))
        return {
            "job": job,
            "task_group": group,
            "items": items,
            "proxy_usage": usage,
            "leases": leases,
            "recent_logs": recent_logs,
            "summary": {
                "total": len(items),
                "queued": sum(i["status"] in {"queued", "retryable"} for i in items),
                "running": sum(i["status"] == "running" for i in items),
                "success": sum(i["status"] == "success" for i in items),
                "failed": sum(i["status"] == "failed" for i in items),
                "cancelled": sum(i["status"] == "cancelled" for i in items),
                "active_leases": sum(l["state"] in {"assigned", "running"} for l in leases),
            },
        }
