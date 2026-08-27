import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from binance_cloud.callback_outbox import CallbackOutbox


class CallbackOutboxTests(unittest.TestCase):
    def test_failed_callback_is_persisted_and_removed_after_delivery(self):
        with TemporaryDirectory() as temp:
            outbox_path = Path(temp) / "callback_outbox.json"
            outbox = CallbackOutbox(outbox_path)
            outbox.enqueue("http://linux/api/worker/callback", {"job_id": "job-1"})
            self.assertEqual(outbox.deliver_due(lambda _url, _payload: False), 0)
            entries = json.loads(outbox_path.read_text(encoding="utf-8"))
            self.assertEqual(entries[0]["attempt_count"], 1)
            entries[0]["next_attempt_at"] = "2000-01-01T00:00:00+00:00"
            outbox_path.write_text(json.dumps(entries), encoding="utf-8")
            self.assertEqual(outbox.deliver_due(lambda _url, _payload: True), 1)
            self.assertEqual(json.loads(outbox_path.read_text(encoding="utf-8")), [])


if __name__ == "__main__":
    unittest.main()
