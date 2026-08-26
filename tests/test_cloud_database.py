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
            payload = {"job_item_id": item["id"], "status": "success", "cookie": "x=" + "a" * 6000, "csrftoken": "token"}
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


if __name__ == "__main__":
    unittest.main()
