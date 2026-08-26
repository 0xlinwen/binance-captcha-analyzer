import unittest

from binance_cloud.cookie_checker import classify_creator_response


class CookieCheckerTests(unittest.TestCase):
    def test_creator_response_is_valid(self):
        result = classify_creator_response("https://www.binance.com/zh-CN/square/creator-center/home", "创作者中心 查看 API", 200)
        self.assertEqual(result.status, "valid")

    def test_login_response_is_expired(self):
        result = classify_creator_response("https://accounts.binance.com/login", "登录", 200)
        self.assertEqual(result.status, "expired")

    def test_auth_response_is_expired(self):
        result = classify_creator_response("https://www.binance.com/", "", 401)
        self.assertEqual(result.status, "expired")

    def test_unknown_response_is_not_expired(self):
        result = classify_creator_response("https://www.binance.com/", "加载中", 503)
        self.assertEqual(result.status, "unknown")
