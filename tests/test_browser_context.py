from __future__ import annotations

import unittest

from binance_analyzer.automation.browser_context import build_chrome_launch_command
from binance_analyzer.fingerprint import generate_fingerprint


class BrowserContextLaunchTests(unittest.TestCase):
    def test_native_launch_skips_window_size_and_color_profile(self) -> None:
        fingerprint = generate_fingerprint(mode="native")
        command = build_chrome_launch_command(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            9222,
            "/tmp/pw_chrome",
            fingerprint,
            headless=False,
        )

        self.assertTrue(any(item.startswith("--remote-debugging-port=") for item in command))
        self.assertIn("--disable-blink-features=AutomationControlled", command)
        self.assertFalse(any(item.startswith("--window-size=") for item in command))
        self.assertNotIn("--force-color-profile=srgb", command)
        self.assertNotIn("--headless=new", command)

    def test_spoofed_launch_keeps_window_size_and_can_enable_headless(self) -> None:
        fingerprint = generate_fingerprint(use_real_profile=True, mode="spoofed")
        command = build_chrome_launch_command(
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            9222,
            "/tmp/pw_chrome",
            fingerprint,
            headless=True,
            proxy_server="http://127.0.0.1:8888",
            viewport_height=fingerprint["screen_height"] - 80,
        )

        self.assertIn(
            f"--window-size={fingerprint['screen_width']},{fingerprint['screen_height'] - 80}",
            command,
        )
        self.assertIn("--force-color-profile=srgb", command)
        self.assertIn("--headless=new", command)
        self.assertIn("--proxy-server=http://127.0.0.1:8888", command)


if __name__ == "__main__":
    unittest.main()
