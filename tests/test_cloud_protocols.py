import unittest

from binance_cloud.shared.protocols import ExecuteLoginPayload


class CloudProtocolTests(unittest.TestCase):
    def test_execute_payload_requires_protocol(self):
        payload = ExecuteLoginPayload(
            protocol_version="1", job_id="job-1", mode="login", callback_url="", proxy={"mode": "direct"},
            accounts=[{"job_item_id": 1, "account_id": 1, "email": "a@example.com", "password": "pw"}],
        )
        self.assertEqual(payload.protocol_version, "1")

    def test_proxy_lease_metadata_is_supported_per_account(self):
        payload = ExecuteLoginPayload(
            protocol_version="1", job_id="job-1", mode="login", callback_url="",
            proxy={"mode": "direct"}, lease_id="batch-lease", proxy_profile="direct",
            accounts=[{
                "job_item_id": 1, "account_id": 1, "email": "a@example.com", "password": "pw",
                "lease_id": "item-lease", "proxy_entry_id": "entry-1", "dispatch_sequence": 7,
            }],
        )
        self.assertEqual(payload.accounts[0].lease_id, "item-lease")
        self.assertEqual(payload.accounts[0].dispatch_sequence, 7)


if __name__ == "__main__":
    unittest.main()
