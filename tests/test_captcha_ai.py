from __future__ import annotations

import importlib
import sys
import types
import unittest
from unittest.mock import MagicMock, patch

from binance_analyzer.exceptions import CaptchaAIError


class CaptchaAITests(unittest.TestCase):
    def test_parse_json_response_accepts_single_line_markdown_fence(self) -> None:
        from binance_analyzer.captcha.ai_client import parse_json_response

        result = parse_json_response('```json {"found": true, "x": 100, "y": 50}```')

        self.assertEqual(result, {"found": True, "x": 100, "y": 50})

    def test_parse_json_response_wraps_invalid_json_as_ai_error(self) -> None:
        from binance_analyzer.captcha.ai_client import parse_json_response

        with self.assertRaises(CaptchaAIError):
            parse_json_response("not-json")

    def test_openrouter_client_uses_direct_session_without_env_proxy(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"positions": [[1, 2]]}'}}],
        }

        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.proxies = {}
        mock_session.post.return_value = mock_response
        fake_requests = types.SimpleNamespace(
            Session=MagicMock(return_value=mock_session),
            HTTPError=Exception,
        )

        with patch.dict(sys.modules, {"requests": fake_requests}):
            sys.modules.pop("binance_analyzer.captcha.ai_client", None)
            ai_client_module = importlib.import_module("binance_analyzer.captcha.ai_client")
            client = ai_client_module.OpenRouterCaptchaClient("sk-test", "model-x")
            result = client.analyze_click_captcha("base64-image", "cat")

        self.assertEqual(result, '{"positions": [[1, 2]]}')
        self.assertFalse(mock_session.trust_env)
        self.assertEqual(mock_session.proxies, {})
        mock_session.post.assert_called_once()

    def test_openrouter_client_uses_bootstrap_proxy_when_configured(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {
            "choices": [{"message": {"content": '{"positions": [[2, 3]]}'}}],
        }

        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.proxies = {}
        mock_session.post.return_value = mock_response
        fake_requests = types.SimpleNamespace(
            Session=MagicMock(return_value=mock_session),
            HTTPError=Exception,
        )

        with patch.dict(sys.modules, {"requests": fake_requests}):
            sys.modules.pop("binance_analyzer.captcha.ai_client", None)
            ai_client_module = importlib.import_module("binance_analyzer.captcha.ai_client")
            client = ai_client_module.OpenRouterCaptchaClient(
                "sk-test",
                "model-x",
                proxy_config={
                    "enabled": True,
                    "bootstrap": {
                        "host": "proxy-bootstrap.example.com",
                        "port": 10000,
                        "username": "PROXY_USERNAME",
                        "password": "PROXY_PASSWORD",
                    },
                },
            )
            result = client.analyze_click_captcha("base64-image", "cat")

        self.assertEqual(result, '{"positions": [[2, 3]]}')
        self.assertFalse(mock_session.trust_env)
        self.assertEqual(
            mock_session.proxies,
            {
                "http": "http://PROXY_USERNAME:PROXY_PASSWORD@proxy-bootstrap.example.com:10000",
                "https": "http://PROXY_USERNAME:PROXY_PASSWORD@proxy-bootstrap.example.com:10000",
            },
        )
        mock_session.post.assert_called_once()

    def test_openrouter_client_rejects_enabled_proxy_without_target(self) -> None:
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.proxies = {}
        fake_requests = types.SimpleNamespace(
            Session=MagicMock(return_value=mock_session),
            HTTPError=Exception,
        )

        with patch.dict(sys.modules, {"requests": fake_requests}):
            sys.modules.pop("binance_analyzer.captcha.ai_client", None)
            ai_client_module = importlib.import_module("binance_analyzer.captcha.ai_client")
            client = ai_client_module.OpenRouterCaptchaClient(
                "sk-test",
                "model-x",
                proxy_config={"enabled": True, "bootstrap": {}},
            )
            with self.assertRaisesRegex(ValueError, "AI 代理"):
                client.analyze_click_captcha("base64-image", "cat")

        mock_session.post.assert_not_called()

    def test_openrouter_client_wraps_empty_choices_as_ai_error(self) -> None:
        mock_response = MagicMock()
        mock_response.json.return_value = {"choices": []}

        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.proxies = {}
        mock_session.post.return_value = mock_response
        fake_requests = types.SimpleNamespace(
            Session=MagicMock(return_value=mock_session),
            HTTPError=Exception,
        )

        with patch.dict(sys.modules, {"requests": fake_requests}):
            sys.modules.pop("binance_analyzer.captcha.ai_client", None)
            ai_client_module = importlib.import_module("binance_analyzer.captcha.ai_client")
            client = ai_client_module.OpenRouterCaptchaClient("sk-test", "model-x")
            with self.assertRaises(CaptchaAIError):
                client.analyze_click_captcha("base64-image", "cat")

    def test_openrouter_client_rejects_non_bool_proxy_enabled(self) -> None:
        mock_session = MagicMock()
        mock_session.__enter__.return_value = mock_session
        mock_session.proxies = {}
        fake_requests = types.SimpleNamespace(
            Session=MagicMock(return_value=mock_session),
            HTTPError=Exception,
        )

        with patch.dict(sys.modules, {"requests": fake_requests}):
            sys.modules.pop("binance_analyzer.captcha.ai_client", None)
            ai_client_module = importlib.import_module("binance_analyzer.captcha.ai_client")
            client = ai_client_module.OpenRouterCaptchaClient(
                "sk-test",
                "model-x",
                proxy_config={"enabled": "false", "bootstrap": {}},
            )
            with self.assertRaisesRegex(ValueError, "ai_proxy.enabled"):
                client.analyze_click_captcha("base64-image", "cat")

        mock_session.post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
