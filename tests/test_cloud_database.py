from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from binance_cloud.database import Database


class CloudDatabaseTests(unittest.TestCase):
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
