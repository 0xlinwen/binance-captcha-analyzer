from __future__ import annotations

import unittest
from binance_analyzer.integrations.proxy_integration import (
    create_proxy_runtime,
    normalize_proxy_module_config,
)


class ProxyIntegrationTests(unittest.TestCase):
    def test_rejects_removed_proxy_config_fields(self) -> None:
        with self.assertRaisesRegex(ValueError, "dynamic_api"):
            normalize_proxy_module_config(
                {
                    "enabled": True,
                    "dynamic_api": "https://example.com/proxy",
                }
            )

    def test_rejects_non_object_proxy_config(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy 配置必须是对象"):
            normalize_proxy_module_config("enabled=false")

    def test_native_proxy_forwarder_config_passes_through(self) -> None:
        normalized = normalize_proxy_module_config(
            {
                "enabled": True,
                "mode": "static",
                "static": {"host": "1.2.3.4", "port": "8080"},
            }
        )

        self.assertEqual(normalized["mode"], "static")
        self.assertEqual(normalized["static"]["host"], "1.2.3.4")

    def test_create_proxy_runtime_rejects_dynamic_mode_without_bootstrap(self) -> None:
        with self.assertRaisesRegex(ValueError, "proxy.bootstrap"):
            create_proxy_runtime(
                {
                    "mode": "login",
                    "login": {"start_url": "https://accounts.binance.com/zh-CN/login"},
                    "proxy": {
                        "enabled": True,
                        "mode": "dynamic",
                        "api_url": "https://example.com/direct-proxy",
                    },
                }
            )


if __name__ == "__main__":
    unittest.main()
