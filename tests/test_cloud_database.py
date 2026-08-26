from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from binance_cloud.database import Database


class CloudDatabaseTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
