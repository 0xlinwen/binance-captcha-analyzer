from __future__ import annotations

import unittest

from binance_analyzer.automation.browser_context import build_chrome_launch_command


class BrowserContextLaunchTests(unittest.TestCase):
    def test_launch_skips_window_size_and_color_profile(self) -> None:
        command = build_chrome_launch_command(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            9222,
            "/tmp/pw_chrome",
            headless=False,
        )

        self.assertTrue(any(item.startswith("--remote-debugging-port=") for item in command))
        self.assertIn("--disable-blink-features=AutomationControlled", command)
        self.assertFalse(any(item.startswith("--window-size=") for item in command))
        self.assertNotIn("--force-color-profile=srgb", command)
        self.assertNotIn("--headless=new", command)

    def test_launch_keeps_headless_and_proxy_without_spoof_flags(self) -> None:
        command = build_chrome_launch_command(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            9222,
            "/tmp/pw_chrome",
            headless=True,
            proxy_server="http://127.0.0.1:8888",
        )

        self.assertIn("--headless=new", command)
        self.assertIn("--proxy-server=http://127.0.0.1:8888", command)
        self.assertFalse(any(item.startswith("--window-size=") for item in command))
        self.assertNotIn("--force-color-profile=srgb", command)


if __name__ == "__main__":
    unittest.main()
