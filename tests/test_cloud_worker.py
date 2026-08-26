import unittest
from unittest.mock import patch

from binance_cloud import worker


class CloudWorkerConfigTests(unittest.TestCase):
    @patch("binance_cloud.worker.load_config", return_value={"proxy": {"enabled": True}, "mode": "login"})
    def test_task_mode_is_not_overwritten_by_proxy_mode(self, _load):
        worker.CONFIG = None
        config = worker._config_for_task({"mode": "direct"}, "register")
        self.assertEqual(config["mode"], "register")
        self.assertFalse(config["proxy"]["enabled"])

    @patch("binance_cloud.worker.load_config", return_value={"proxy": {}, "mode": "login"})
    def test_unknown_task_mode_fails_fast(self, _load):
        worker.CONFIG = None
        with self.assertRaises(ValueError):
            worker._config_for_task({"mode": "direct"}, "unknown")


if __name__ == "__main__":
    unittest.main()
