import unittest
from unittest.mock import patch

from binance_cloud.cookie_checker import classify_auth_response
from binance_cloud.cookie_checker import check_creator_center_cookie


class CookieCheckerTests(unittest.TestCase):
    def test_auth_response_is_valid(self):
        result = classify_auth_response("https://www.binance.com/bapi/accounts/v1/public/authcenter/auth", '{"code":"000000","success":true,"data":{}}', 200)
        self.assertEqual(result.status, "valid")

    def test_login_response_is_expired(self):
        result = classify_auth_response("https://www.binance.com/bapi/accounts/v1/public/authcenter/auth", '{"code":"100001","success":false}', 200)
        self.assertEqual(result.status, "expired")

    def test_auth_response_is_expired(self):
        result = classify_auth_response("https://www.binance.com/bapi/accounts/v1/public/authcenter/auth", "", 401)
        self.assertEqual(result.status, "expired")

    def test_unknown_response_is_not_expired(self):
        result = classify_auth_response("https://www.binance.com/bapi/accounts/v1/public/authcenter/auth", "加载中", 503)
        self.assertEqual(result.status, "unknown")

    @patch("binance_cloud.cookie_checker.requests.Session")
    def test_cookie_check_uses_explicit_proxy(self, session_factory):
        session = session_factory.return_value
        session.post.return_value.url = "https://www.binance.com/bapi/accounts/v1/public/authcenter/auth"
        session.post.return_value.text = '{"code":"000000","success":true}'
        session.post.return_value.status_code = 200
        result = check_creator_center_cookie("a=b", proxy="http://127.0.0.1:7890")
        self.assertEqual(result.status, "valid")
        session.proxies.update.assert_called_once_with({"http": "http://127.0.0.1:7890", "https": "http://127.0.0.1:7890"})
        headers = session.post.call_args.kwargs["headers"]
        self.assertEqual(headers["clienttype"], "web")
        self.assertIn("creator-center/home", headers["Referer"])
