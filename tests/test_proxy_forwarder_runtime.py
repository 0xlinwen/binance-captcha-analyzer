from __future__ import annotations

import unittest
from unittest.mock import patch

from proxy_forwarder.proxy_utils import (
    build_proxy_client_config,
    build_playwright_proxy_config,
    build_proxy_url,
    describe_proxy,
    parse_proxy_text,
    public_proxy_info,
)
from proxy_forwarder.runtime import (
    _validate_exit_ip_with_reason,
    get_proxy_runtime,
)


class RuntimeTests(unittest.TestCase):
    def test_parse_proxy_text_supports_api_response_format(self) -> None:
        proxy_info = parse_proxy_text("10.0.0.1@8080@user@pass")

        self.assertEqual(
            proxy_info,
            {
                "ip": "10.0.0.1",
                "port": "8080",
                "user": "user",
                "password": "pass",
            },
        )

    def test_parse_proxy_text_supports_json_payload(self) -> None:
        proxy_info = parse_proxy_text('{"code":200,"data":[{"ip":"10.0.0.2","port":9000}]}')

        self.assertEqual(
            proxy_info,
            {
                "ip": "10.0.0.2",
                "port": "9000",
                "user": "",
                "password": "",
            },
        )

    def test_build_proxy_url_escapes_credentials(self) -> None:
        proxy_url = build_proxy_url(
            {
                "host": "127.0.0.1",
                "port": "7890",
                "username": "alice@example.com",
                "password": "a b",
            }
        )

        self.assertEqual(proxy_url, "http://alice%40example.com:a%20b@127.0.0.1:7890")

    def test_describe_proxy_supports_nested_runtime(self) -> None:
        text = describe_proxy(
            {
                "local_server": "http://127.0.0.1:18080",
                "bootstrap_upstream": {"ip": "1.1.1.1", "port": "1000"},
                "final_upstream": {"ip": "2.2.2.2", "port": "2000"},
            }
        )

        self.assertIn("http://127.0.0.1:18080", text)
        self.assertIn("1.1.1.1:1000", text)
        self.assertIn("2.2.2.2:2000", text)

    def test_public_proxy_info_hides_private_runtime_fields(self) -> None:
        public_info = public_proxy_info(
            {
                "server": "http://127.0.0.1:18080",
                "_gost_process": object(),
                "final_upstream": {
                    "ip": "2.2.2.2",
                    "port": "2000",
                    "_secret": "hidden",
                },
            }
        )

        self.assertEqual(
            public_info,
            {
                "server": "http://127.0.0.1:18080",
                "final_upstream": {
                    "ip": "2.2.2.2",
                    "port": "2000",
                },
            },
        )

    def test_build_proxy_client_config_keeps_server_shape(self) -> None:
        client_config = build_proxy_client_config({"server": "http://127.0.0.1:18080"})

        self.assertEqual(client_config, {"server": "http://127.0.0.1:18080"})

    def test_build_playwright_proxy_config_splits_server_and_credentials(self) -> None:
        client_config = build_playwright_proxy_config(
            {"server": "http://alice%40example.com:a%20b@127.0.0.1:18080"}
        )

        self.assertEqual(
            client_config,
            {
                "server": "http://127.0.0.1:18080",
                "username": "alice@example.com",
                "password": "a b",
            },
        )

    def test_static_proxy_requires_configured_gost_to_start(self) -> None:
        runtime = get_proxy_runtime(
            {
                "enabled": True,
                "mode": "static",
                "check_timeout_seconds": 1,
                "static": {
                    "host": "3.3.3.3",
                    "port": "8080",
                    "username": "",
                    "password": "",
                },
                "gost": {
                    "binary": "__definitely_missing_gost_binary__",
                    "listen_host": "127.0.0.1",
                    "listen_port": 0,
                },
            },
            max_attempts=1,
        )

        self.assertIsNone(runtime)

    @patch("proxy_forwarder.runtime.fetch_public_ip_via_proxy")
    def test_validate_exit_ip_uses_dynamic_blocked_provider(self, mock_fetch_public_ip_via_proxy) -> None:
        mock_fetch_public_ip_via_proxy.return_value = "9.9.9.9"

        proxy_info, reason = _validate_exit_ip_with_reason(
            {"ip": "3.3.3.3", "port": "8080"},
            timeout=5,
            blocked_exit_ips_provider=lambda: {"9.9.9.9"},
        )

        self.assertIsNone(proxy_info)
        self.assertEqual(reason, "blocked_exit_ip")

    @patch("proxy_forwarder.runtime._start_gost_chain")
    @patch("proxy_forwarder.runtime.probe_url_via_chain")
    @patch("proxy_forwarder.runtime.check_proxy_via_chain")
    @patch("proxy_forwarder.runtime.fetch_proxy_via_bootstrap")
    def test_dynamic_proxy_retries_next_ip_when_quality_latency_exceeds_limit(
        self,
        mock_fetch_proxy_via_bootstrap,
        mock_check_proxy_via_chain,
        mock_probe_url_via_chain,
        mock_start_gost_chain,
    ) -> None:
        mock_fetch_proxy_via_bootstrap.side_effect = [
            {"ip": "2.2.2.1", "port": "2000", "user": "", "password": ""},
            {"ip": "2.2.2.2", "port": "2000", "user": "", "password": ""},
        ]
        mock_check_proxy_via_chain.side_effect = [
            (True, "9.9.9.1"),
            (True, "9.9.9.2"),
        ]
        mock_probe_url_via_chain.side_effect = [
            (True, 200, 2501.0, "ok"),
            (True, 200, 1200.0, "ok"),
        ]
        mock_start_gost_chain.return_value = None

        runtime = get_proxy_runtime(
            {
                "enabled": True,
                "mode": "dynamic",
                "api_url": "https://example.com/proxy",
                "timeout_seconds": 1,
                "check_timeout_seconds": 1,
                "bootstrap": {
                    "host": "1.1.1.1",
                    "port": "1000",
                    "username": "",
                    "password": "",
                },
                "gost": {"binary": "__disabled_gost__", "listen_host": "127.0.0.1", "listen_port": 0},
            },
            max_attempts=2,
            quality_check={
                "enabled": True,
                "target_url": "https://accounts.binance.com/zh-CN/login",
                "timeout_seconds": 1,
                "max_latency_ms": 2500,
            },
            require_exit_ip=True,
        )

        self.assertIsNotNone(runtime)
        self.assertEqual(runtime["final_upstream"]["ip"], "2.2.2.2")
        self.assertEqual(runtime["exit_ip"], "9.9.9.2")
        self.assertEqual(mock_fetch_proxy_via_bootstrap.call_count, 2)


if __name__ == "__main__":
    unittest.main()
