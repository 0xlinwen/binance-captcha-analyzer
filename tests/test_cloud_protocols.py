import unittest

from binance_cloud.protocols import ExecuteLoginPayload, PROTOCOL_VERSION


class CloudProtocolTests(unittest.TestCase):
    def test_execute_payload_defaults_to_current_protocol(self):
        payload = ExecuteLoginPayload(
            job_id="job-1", mode="login", callback_url="", proxy={"mode": "direct"},
            accounts=[{"job_item_id": 1, "account_id": 1, "email": "a@example.com", "password": "pw"}],
        )
        self.assertEqual(payload.protocol_version, PROTOCOL_VERSION)


if __name__ == "__main__":
    unittest.main()
