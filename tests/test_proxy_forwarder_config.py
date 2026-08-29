from __future__ import annotations

import unittest

from proxy_forwarder.config import build_proxy_quality_check, resolve_proxy_settings


class ProxyForwarderConfigTests(unittest.TestCase):
    def test_resolve_proxy_settings_escapes_control_characters_in_api_url(self) -> None:
        settings = resolve_proxy_settings(
            {
                "enabled": True,
                "mode": "dynamic",
                "api_url": "http://example.com/gen?split=\r\n&region=JP",
                "bootstrap": {"host": "1.1.1.1", "port": "8080"},
            }
        )

        self.assertEqual(
            settings["api_url"],
            "http://example.com/gen?split=%0D%0A&region=JP",
        )

    def test_proxy_quality_check_defaults_to_enabled_with_2500ms_limit_when_proxy_enabled(self) -> None:
        quality_check = build_proxy_quality_check(
            {"url": "https://accounts.binance.com/zh-CN/login"},
            {"enabled": True},
        )

        self.assertTrue(quality_check["enabled"])
        self.assertEqual(quality_check["max_latency_ms"], 2500.0)

    def test_proxy_quality_check_can_be_disabled_explicitly(self) -> None:
        quality_check = build_proxy_quality_check(
            {"url": "https://accounts.binance.com/zh-CN/login"},
            {"enabled": True, "proxy_quality_check_enabled": False},
        )

        self.assertFalse(quality_check["enabled"])

    def test_resolve_proxy_settings_rejects_string_bool(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy.enabled"):
            resolve_proxy_settings({"enabled": "false", "mode": "dynamic"})

    def test_resolve_proxy_settings_rejects_invalid_bool_string(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy.enabled"):
            resolve_proxy_settings({"enabled": "definitely", "mode": "dynamic"})

    def test_resolve_proxy_settings_preserves_static_proxy_scheme(self) -> None:
        settings = resolve_proxy_settings(
            {
                "enabled": True,
                "mode": "static",
                "static": {"scheme": "socks5", "host": "127.0.0.1", "port": 1080},
            }
        )

        self.assertEqual(settings["static"]["scheme"], "socks5")


if __name__ == "__main__":
    unittest.main()
