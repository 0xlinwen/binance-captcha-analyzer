import unittest

from binance_cloud.cookie_checker import classify_auth_response


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
