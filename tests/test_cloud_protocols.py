import unittest

from binance_cloud.protocols import ExecuteLoginPayload


class CloudProtocolTests(unittest.TestCase):
    def test_execute_payload_requires_protocol(self):
        payload = ExecuteLoginPayload(
            protocol_version="1", job_id="job-1", mode="login", callback_url="", proxy={"mode": "direct"},
            accounts=[{"job_item_id": 1, "account_id": 1, "email": "a@example.com", "password": "pw"}],
        )
        self.assertEqual(payload.protocol_version, "1")


if __name__ == "__main__":
    unittest.main()
