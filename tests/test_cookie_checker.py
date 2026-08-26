import unittest

from binance_cloud.cookie_checker import classify_creator_page


class CookieCheckerTests(unittest.TestCase):
    def test_creator_page_is_valid(self):
        result = classify_creator_page("https://www.binance.com/zh-CN/square/creator-center/home", "创作者中心 查看 API")
        self.assertEqual(result.status, "valid")

    def test_login_page_is_expired(self):
        result = classify_creator_page("https://accounts.binance.com/login", "登录")
        self.assertEqual(result.status, "expired")

    def test_unknown_page_is_not_expired(self):
        result = classify_creator_page("https://www.binance.com/zh-CN/square/creator-center/home", "加载中")
        self.assertEqual(result.status, "unknown")


if __name__ == "__main__":
    unittest.main()
