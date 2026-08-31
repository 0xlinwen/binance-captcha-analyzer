from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from binance_cloud.linux.database import Database


class CloudDatabaseTests(unittest.TestCase):
    def test_proxy_pool_lease_uses_current_entry_and_releases_idempotently(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            entries = db.configure_proxy_pool("registration", ["socks5://one:1000", "socks5://two:1000"])
            self.assertEqual(entries[0]["status"], "active")
            lease = db.acquire_proxy_lease("registration", job["id"], item["id"], "windows-01", 60, dispatch_sequence=1)
            self.assertEqual(lease["proxy_entry_id"], "registration:0")
            self.assertEqual(lease["state"], "assigned")
            bound_item = db.get_job(job["id"])["items"][0]
            self.assertEqual(bound_item["lease_id"], lease["lease_id"])
            self.assertEqual(bound_item["proxy_entry_id"], "registration:0")
            with self.assertRaisesRegex(ValueError, "未释放"):
                db.acquire_proxy_lease("registration", job["id"], item["id"], "windows-01", 60)

            released = db.release_proxy_lease(lease["lease_id"], result_status="proxy_failed")
            self.assertEqual(released["state"], "released")
            db.release_proxy_lease(lease["lease_id"], result_status="proxy_failed")
            entry = db._one("SELECT * FROM proxy_pool_entries WHERE id=?", ("registration:0",))
            self.assertEqual(entry["active_count"], 0)
            self.assertEqual(entry["consecutive_failures"], 1)

    def test_callback_releases_matching_proxy_lease_and_rejects_mismatch(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            db.configure_proxy_pool("registration", ["socks5://one:1000"])
            lease = db.acquire_proxy_lease("registration", job["id"], item["id"], "windows-01", 60)
            with self.assertRaisesRegex(ValueError, "租约不匹配"):
                db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                                  "worker_id": "windows-01", "status": "failed", "lease_id": "wrong"})
            db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                              "worker_id": "windows-01", "status": "failed", "lease_id": lease["lease_id"],
                              "proxy_entry_id": lease["proxy_entry_id"]})
            stored = db._one("SELECT * FROM proxy_leases WHERE lease_id=?", (lease["lease_id"],))
            self.assertEqual(stored["state"], "released")

    def test_proxy_pool_reconfiguration_disables_removed_entry_without_deleting_lease(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            db.configure_proxy_pool("registration", ["socks5://one:1000", "socks5://two:1000"])
            lease = db.acquire_proxy_lease("registration", job["id"], item["id"], "windows-01", 60)
            entries = db.configure_proxy_pool("registration", ["socks5://one:1000"])
            self.assertEqual(len(entries), 2)
            self.assertEqual(entries[1]["status"], "disabled")
            self.assertEqual(db._one("SELECT lease_id FROM proxy_leases WHERE lease_id=?", (lease["lease_id"],))["lease_id"], lease["lease_id"])

    def test_dynamic_job_keeps_dynamic_profile_in_worker_payload(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}], {"mode": "dynamic", "proxy_profile": "dynamic"})
            payload = db.worker_payload(job["id"])
            self.assertEqual(payload["proxy"]["mode"], "dynamic")
            self.assertEqual(payload["proxy_profile"], "dynamic")

    def test_rotating_pool_switches_only_after_final_proxy_failures(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            accounts = [{"email": f"pool-{i}@example.com", "password": "pw"} for i in range(5)]
            job = db.create_job(accounts, {"mode": "rotating_single_ip", "proxy_profile": "rotating_single_ip"})
            db.configure_proxy_pool("default", ["socks5://one:1000", "socks5://two:1000"], switch_threshold=5)
            db.conn.execute("UPDATE login_job_items SET max_retries=1,proxy_retry_count=1 WHERE job_id=?", (job["id"],))
            for item in db.get_job(job["id"])["items"]:
                payload = db.rotating_worker_payload(job["id"], "worker", 60)
                self.assertIsNotNone(payload)
                current = payload["accounts"][0]
                db.save_callback({"job_id": job["id"], "job_item_id": current["job_item_id"], "account_id": current["account_id"],
                                  "worker_id": "worker", "status": "proxy_failed", "lease_id": current["lease_id"],
                                  "proxy_entry_id": current["proxy_entry_id"]})
            first = db._one("SELECT * FROM proxy_pool_entries WHERE id='default:0'")
            second = db._one("SELECT * FROM proxy_pool_entries WHERE id='default:1'")
            self.assertEqual(first["status"], "cooling")
            self.assertEqual(second["status"], "active")

    def test_rotating_pool_payload_binds_address_to_single_account(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "pool@example.com", "password": "pw"}], {"mode": "rotating_single_ip"})
            db.configure_proxy_pool("default", ["socks5://one:1000"])
            payload = db.rotating_worker_payload(job["id"], "worker", 60)
            self.assertEqual(payload["proxy"]["mode"], "fixed")
            self.assertEqual(payload["proxy"]["address"], "socks5://one:1000")
            self.assertEqual(len(payload["accounts"]), 1)

    def test_active_ip_is_not_reused_while_another_lease_is_running(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": f"parallel-{i}@example.com", "password": "pw"} for i in range(2)], {"mode": "rotating_single_ip"})
            db.configure_proxy_pool("default", ["socks5://one:1000", "socks5://two:1000"])
            first = db.rotating_worker_payload(job["id"], "worker-1", 60)
            second = db.rotating_worker_payload(job["id"], "worker-2", 60)
            self.assertNotEqual(first["accounts"][0]["proxy_entry_id"], second["accounts"][0]["proxy_entry_id"])

    def test_worker_slots_are_reserved_and_released(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            db.register_worker("worker", version="1")
            db.set_worker_capacity("worker", 2)
            self.assertTrue(db.reserve_worker_slot("worker"))
            self.assertTrue(db.reserve_worker_slot("worker"))
            self.assertFalse(db.reserve_worker_slot("worker"))
            self.assertEqual(db._one("SELECT active_slots FROM worker_nodes WHERE id='worker'")["active_slots"], 2)
            db.release_worker_slot("worker")
            self.assertTrue(db.reserve_worker_slot("worker"))

    def test_job_diagnostics_contains_correlation_and_redacts_proxy_credentials(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "diag@example.com", "password": "pw"}], {"mode": "fixed", "address": "socks5://user:secret@127.0.0.1:1080", "max_accounts_per_job": 1})
            item = db.get_job(job["id"])["items"][0]
            db.mark_items_running(job["id"], "worker-1", 60)
            db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                              "worker_id": "worker-1", "status": "failed", "error_code": "login_failed",
                              "error_message": "页面认证失败", "proxy_profile": "static", "dispatch_sequence": 2})
            value = db.job_diagnostics(job["id"])
            self.assertEqual(value["summary"]["failed"], 1)
            self.assertEqual(value["items"][0]["error_code"], "login_failed")
            self.assertIn("callback_received", [row["event"] for row in value["recent_logs"]])
            self.assertNotIn("secret", str(value))

    def test_failed_ip_enters_cooling_for_24_hours(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "cooling@example.com", "password": "pw"}], {"mode": "rotating_single_ip"})
            db.configure_proxy_pool("default", ["socks5://one:1000"], switch_threshold=1)
            db.conn.execute("UPDATE login_job_items SET max_retries=1,proxy_retry_count=1 WHERE job_id=?", (job["id"],))
            payload = db.rotating_worker_payload(job["id"], "worker", 60)
            account = payload["accounts"][0]
            db.save_callback({"job_id": job["id"], "job_item_id": account["job_item_id"], "account_id": account["account_id"], "worker_id": "worker", "status": "proxy_failed", "lease_id": account["lease_id"], "proxy_entry_id": account["proxy_entry_id"]})
            entry = db._one("SELECT * FROM proxy_pool_entries WHERE id='default:0'")
            self.assertEqual(entry["status"], "cooling")
            self.assertTrue(entry["cooldown_until"])

    def test_rate_limited_callback_is_terminal_failed_and_not_requeued(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "limited@example.com", "password": "pw"}], {"mode": "rotating_single_ip"})
            db.configure_proxy_pool("default", ["socks5://one:1000"])
            payload = db.rotating_worker_payload(job["id"], "worker", 60)
            account = payload["accounts"][0]
            db.save_callback({"job_id": job["id"], "job_item_id": account["job_item_id"], "account_id": account["account_id"],
                              "worker_id": "worker", "status": "rate_limited", "error_code": "rate_limited",
                              "error_message": "频率限制", "lease_id": account["lease_id"], "proxy_entry_id": account["proxy_entry_id"]})
            item = db.get_job(job["id"])["items"][0]
            self.assertEqual(item["status"], "failed")
            self.assertEqual(item["retry_count"], 0)
            self.assertEqual(db.get_job(job["id"])["status"], "completed")
            self.assertEqual(db._one("SELECT state FROM proxy_leases WHERE lease_id=?", (account["lease_id"],))["state"], "released")

    def test_already_registered_marks_account_and_future_register_is_skipped(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "registered@example.com", "password": "pw"}], task_mode="register")
            item = db.get_job(job["id"])["items"][0]
            db.mark_items_running(job["id"], "worker", 60)
            db.save_callback({
                "job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                "worker_id": "worker", "status": "already_registered", "error_code": "already_registered",
            })
            account = db._one("SELECT registration_state FROM accounts WHERE id=?", (item["account_id"],))
            self.assertEqual(account["registration_state"], "registered")
            next_job = db.create_job([{"email": "registered@example.com", "password": "pw"}], task_mode="register")
            skipped = db.get_job(next_job["id"])["items"][0]
            self.assertEqual(skipped["status"], "already_registered")
            self.assertEqual(skipped["error_code"], "already_registered")

    def test_login_need_register_routes_to_register_child_without_proxy_failure(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            group = db.create_task_group()
            job = db.create_job(
                [{"email": "unregistered@example.com", "password": "pw"}],
                {"mode": "rotating_single_ip", "proxy_profile": "rotating_single_ip"},
                task_mode="login", task_group_id=group["id"],
            )
            db.configure_proxy_pool("default", ["socks5://one:1000"])
            payload = db.rotating_worker_payload(job["id"], "worker", 60)
            account = payload["accounts"][0]
            db.save_callback({
                "job_id": job["id"], "job_item_id": account["job_item_id"], "account_id": account["account_id"],
                "worker_id": "worker", "status": "need_register", "error_code": "need_register",
                "lease_id": account["lease_id"], "proxy_entry_id": account["proxy_entry_id"],
            })
            source = db.get_job(job["id"])["items"][0]
            self.assertEqual(source["status"], "need_register")
            self.assertEqual(db._one("SELECT registration_state FROM accounts WHERE id=?", (source["account_id"],))["registration_state"], "unregistered")
            self.assertEqual(db._one("SELECT consecutive_failures FROM proxy_pool_entries WHERE id='default:0'")["consecutive_failures"], 0)
            child = db.route_need_register_to_child_job(source["id"])
            self.assertIsNotNone(child)
            child_item = db.get_job(child["id"])["items"][0]
            self.assertEqual(child["task_mode"], "register")
            self.assertEqual(child_item["status"], "queued")
            self.assertEqual(child_item["retry_of_job_item_id"], source["id"])
            self.assertIsNone(db.route_need_register_to_child_job(source["id"]))

    def test_failed_items_can_be_retried_from_original_credential_snapshot(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job(
                [{"email": "retry@example.com", "password": "original-password"}],
                {"mode": "dynamic", "proxy_profile": "dynamic"},
                task_mode="register",
            )
            item = db.get_job(job["id"])["items"][0]
            db.mark_items_running(job["id"], "worker", 60)
            db.save_callback({
                "job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                "worker_id": "worker", "status": "failed", "error_code": "register_failed",
            })
            db.save_account("retry@example.com", "newer-password")

            failed = db.failed_items(job["id"])
            self.assertEqual([row["job_item_id"] for row in failed], [item["id"]])
            self.assertNotIn("password", failed[0])
            self.assertNotIn("original-password", str(failed))

            retry_job = db.create_failed_items_retry_job(job["id"])
            self.assertEqual(retry_job["task_mode"], "register")
            self.assertEqual(retry_job["proxy_mode"], "dynamic")
            retry_item = db.get_job(retry_job["id"])["items"][0]
            self.assertEqual(retry_item["retry_of_job_item_id"], item["id"])
            self.assertNotIn("account_password_snapshot", retry_item)
            payload = db.worker_payload(retry_job["id"])
            self.assertEqual(payload["accounts"][0]["password"], "original-password")

    def test_failed_items_retry_rejects_nonfailed_or_duplicate_items(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "retry-select@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            with self.assertRaisesRegex(ValueError, "最终失败项"):
                db.create_failed_items_retry_job(job["id"], [item["id"]])
            db.mark_items_running(job["id"], "worker", 60)
            db.save_callback({
                "job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                "worker_id": "worker", "status": "failed",
            })
            with self.assertRaisesRegex(ValueError, "不可重复"):
                db.create_failed_items_retry_job(job["id"], [item["id"], item["id"]])

    def test_failed_items_are_requeued_in_original_job_without_duplicate_registered_account(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            group = db.create_task_group()
            job = db.create_job(
                [
                    {"email": "requeue@example.com", "password": "pw"},
                    {"email": "registered@example.com", "password": "pw"},
                ],
                {"mode": "rotating_single_ip", "proxy_profile": "rotating_single_ip"},
                task_mode="register",
                task_group_id=group["id"],
            )
            items = db.get_job(job["id"])["items"]
            for item in items:
                db.mark_items_running(job["id"], "worker", 60)
                db.save_callback({
                    "job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                    "worker_id": "worker", "status": "failed", "error_code": "register_failed",
                })
            db.conn.execute(
                "UPDATE accounts SET registration_state='registered' WHERE id=?",
                (items[1]["account_id"],),
            )
            db.refresh_task_group(group["id"])
            db.mark_task_group_completion_notified(group["id"])
            self.assertTrue(db.claim_notification_event(f"task-group-completion:{group['id']}"))

            result = db.requeue_failed_items(job["id"])
            self.assertEqual(result["job_id"], job["id"])
            self.assertEqual(result["requeued_count"], 1)
            self.assertEqual(result["resolved_registered_count"], 1)
            updated = {item["id"]: item for item in db.get_job(job["id"])["items"]}
            self.assertEqual(updated[items[0]["id"]]["status"], "queued")
            self.assertIsNone(updated[items[0]["id"]]["error_code"])
            self.assertEqual(updated[items[1]["id"]]["status"], "already_registered")
            self.assertEqual(len(db.get_job(job["id"])["items"]), 2)
            updated_group = db.task_group(group["id"])
            self.assertEqual(updated_group["status"], "running")
            self.assertEqual(updated_group["completion_notified"], 0)
            self.assertTrue(db.claim_notification_event(f"task-group-completion:{group['id']}"))

    def test_task_group_cancels_pending_items_after_failure_threshold(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            group = db.create_task_group()
            job = db.create_job([{"email": f"{i}@example.com", "password": "pw"} for i in range(6)], task_group_id=group["id"])
            items = db.get_job(job["id"])["items"]
            for item in items[:5]:
                db.mark_items_running(job["id"], "worker", 60)
                db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                                  "worker_id": "worker", "status": "failed", "error_code": "login_failed"})
            self.assertTrue(db.claim_task_group_failure_alert(group["id"], 5))
            db.cancel_task_group(group["id"])
            self.assertEqual({item["status"] for item in db.get_job(job["id"])["items"]}, {"cancelled", "failed"})
            self.assertEqual(db.task_group(group["id"])["status"], "cancelled")
            current_job = db.get_job(job["id"])
            self.assertEqual(current_job["failed_count"], 5)
            self.assertEqual(current_job["cancelled_count"], 1)
            self.assertEqual(db.task_group(group["id"])["cancelled_count"], 1)
            self.assertEqual(len([row for row in db.logs(job["id"]) if row["event"] == "account_cancelled"]), 1)

    def test_cookie_text_and_idempotent_callback(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            payload = {"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                       "worker_id": "test", "status": "success", "cookie": "x=" + "a" * 6000, "csrftoken": "token"}
            db.save_callback(payload)
            db.save_callback(payload)
            credential = db.credential(item["account_id"])
            self.assertEqual(len(credential["cookie"]), 6002)
            self.assertEqual(db.get_job(job["id"])["success_count"], 1)

    def test_migrates_legacy_credentials_table(self):
        with TemporaryDirectory() as temp:
            path = Path(temp) / "state.db"
            import sqlite3
            conn = sqlite3.connect(path)
            conn.executescript(
                """
                CREATE TABLE accounts (
                  id INTEGER PRIMARY KEY,
                  email TEXT NOT NULL UNIQUE,
                  password TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                INSERT INTO accounts(id,email,password,created_at,updated_at)
                VALUES(1,'legacy@example.com','pw','2026-08-27T00:00:00+00:00','2026-08-27T02:00:00+00:00');
                CREATE TABLE credentials (
                  id INTEGER PRIMARY KEY AUTOINCREMENT,
                  account_id INTEGER NOT NULL UNIQUE,
                  cookie TEXT NOT NULL,
                  csrftoken TEXT,
                  cookie_expires_at TEXT,
                  credential_updated_at TEXT NOT NULL,
                  created_at TEXT NOT NULL,
                  updated_at TEXT NOT NULL
                );
                INSERT INTO credentials(account_id,cookie,csrftoken,cookie_expires_at,credential_updated_at,created_at,updated_at)
                VALUES(1,'legacy-cookie','legacy-token','2026-09-01T00:00:00+00:00','2026-08-27T01:00:00+00:00','2026-08-27T00:00:00+00:00','2026-08-27T02:00:00+00:00');
                """
            )
            conn.commit()
            conn.close()

            db = Database(path)
            credential = db.credential(1)
            self.assertEqual(credential["credential_exported_at"], "2026-08-27T01:00:00+00:00")
            self.assertNotIn("cookie_expires_at", credential)
            self.assertNotIn("credential_updated_at", credential)
            db.close()

    def test_fixed_proxy_quota_is_per_job(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            accounts = [{"email": f"{i}@example.com", "password": "pw"} for i in range(3)]
            first = db.create_job(accounts, {"mode": "fixed", "address": "127.0.0.1:8080", "max_accounts_per_job": 1})
            self.assertEqual([row["status"] for row in db.get_job(first["id"])["items"]], ["queued", "proxy_quota_exceeded", "proxy_quota_exceeded"])
            second = db.create_job(accounts[:1], {"mode": "fixed", "address": "127.0.0.1:8080", "max_accounts_per_job": 1})
            self.assertEqual(db.get_job(second["id"])["items"][0]["status"], "queued")

    def test_cancelled_item_ignores_late_callback(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            db.cancel_job(job["id"])
            result = db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                                       "worker_id": "worker", "status": "success", "cookie": "x=y"})
            self.assertEqual(result["status"], "cancelled")
            self.assertIsNone(db.credential(item["account_id"]))

    def test_retry_exhaustion_finishes_job(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            db.mark_items_running(job["id"], "dispatching", 1)
            db.dispatch_failed(job["id"], "offline")
            self.assertEqual(db.get_job(job["id"])["items"][0]["status"], "retryable")
            db.requeue_retryable()
            db.mark_items_running(job["id"], "dispatching", 1)
            db.dispatch_failed(job["id"], "offline")
            current = db.get_job(job["id"])
            self.assertEqual(current["items"][0]["status"], "failed")
            self.assertEqual(current["status"], "completed")

    def test_proxy_failure_is_requeued_until_retry_limit(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            job = db.create_job([{"email": "a@example.com", "password": "pw"}])
            item = db.get_job(job["id"])["items"][0]
            db.mark_items_running(job["id"], "dispatching", 60)
            db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                              "worker_id": "worker", "status": "proxy_failed"})
            current = db.get_job(job["id"])["items"][0]
            self.assertEqual(current["status"], "retryable")
            self.assertEqual(current["retry_count"], 1)
            db.requeue_retryable()
            self.assertEqual(db.get_job(job["id"])["items"][0]["status"], "queued")

    def test_delayed_older_cookie_does_not_replace_newer_cookie(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            first = db.create_job([{"email": "a@example.com", "password": "pw"}])
            second = db.create_job([{"email": "a@example.com", "password": "pw"}])
            first_item = db.get_job(first["id"])["items"][0]
            second_item = db.get_job(second["id"])["items"][0]
            for job, item, cookie, updated_at in (
                (second, second_item, "new-cookie", "2026-08-27T10:00:00+00:00"),
                (first, first_item, "old-cookie", "2026-08-27T09:00:00+00:00"),
            ):
                db.mark_items_running(job["id"], "dispatching", 60)
                db.save_callback({"job_id": job["id"], "job_item_id": item["id"], "account_id": item["account_id"],
                                  "worker_id": "worker", "status": "success", "cookie": cookie,
                                  "credential_exported_at": updated_at})
            credential = db.credential(second_item["account_id"])
            self.assertEqual(credential["cookie"], "new-cookie")
            self.assertEqual(credential["credential_exported_at"], "2026-08-27T10:00:00+00:00")

    def test_idempotency_key_reuses_existing_job(self):
        with TemporaryDirectory() as temp:
            db = Database(Path(temp) / "state.db")
            first = db.create_job([{"email": "a@example.com", "password": "pw"}], idempotency_key="request-1")
            second = db.create_job([{"email": "b@example.com", "password": "pw"}], idempotency_key="request-1")
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(len(db.get_job(first["id"])["items"]), 1)


if __name__ == "__main__":
    unittest.main()
