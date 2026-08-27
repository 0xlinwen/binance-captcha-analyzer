import unittest
from unittest.mock import Mock, patch

from binance_cloud.tools.batch_submit import submit_batches


class BatchSubmitTests(unittest.TestCase):
    @patch("binance_cloud.tools.batch_submit.time.sleep")
    @patch("binance_cloud.tools.batch_submit.requests.get")
    @patch("binance_cloud.tools.batch_submit.requests.post")
    @patch("binance_cloud.tools.batch_submit.load_accounts", return_value=[(f"a{i}@x.com", "pw") for i in range(5)])
    def test_sends_failure_and_completion_once(self, _load, post, get, _sleep):
        group_response = Mock(); group_response.json.return_value = {"id": "g1"}; group_response.raise_for_status.return_value = None
        job_response = Mock(); job_response.json.return_value = {"job_id": "j1"}; job_response.raise_for_status.return_value = None
        post.side_effect = [group_response, job_response]
        get_response = Mock(); get_response.raise_for_status.return_value = None
        get_response.json.return_value = {"items": [{"status": "failed"} for _ in range(5)]}
        get.return_value = get_response
        submit_batches("http://cloud", __import__("pathlib").Path("accounts.txt"), "login", 20, None, 1)

    @patch("binance_cloud.tools.batch_submit.time.sleep")
    @patch("binance_cloud.tools.batch_submit.time.monotonic", side_effect=[0, 2])
    @patch("binance_cloud.tools.batch_submit.requests.get")
    @patch("binance_cloud.tools.batch_submit.requests.post")
    @patch("binance_cloud.tools.batch_submit.load_accounts", return_value=[("a@x.com", "pw")])
    def test_stops_at_global_timeout(self, _load, post, get, _monotonic, _sleep):
        group = Mock(); group.json.return_value = {"id": "g1"}; group.raise_for_status.return_value = None
        job = Mock(); job.json.return_value = {"job_id": "j1"}; job.raise_for_status.return_value = None
        cancel = Mock(); cancel.raise_for_status.return_value = None
        post.side_effect = [job, cancel]
        response = Mock(); response.raise_for_status.return_value = None; response.json.return_value = {"items": [{"status": "running"}]}
        get.return_value = response
        with self.assertRaises(TimeoutError):
            submit_batches("http://cloud", __import__("pathlib").Path("accounts.txt"), "login", 20, None, 1, 1)


if __name__ == "__main__":
    unittest.main()
