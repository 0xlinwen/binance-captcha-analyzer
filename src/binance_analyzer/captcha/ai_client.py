"""验证码 AI 客户端。"""

from __future__ import annotations

import base64
import json
from typing import Any, Mapping

import requests

from ..exceptions import CaptchaAIError
from proxy_forwarder import build_proxy_url, resolve_proxy_settings

from .prompts import (
    build_checkbox_captcha_prompt,
    build_click_captcha_prompt,
    build_slider_captcha_prompt,
)


def _require_bool(value: Any, *, key: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"配置 {key} 必须是布尔值")
    return value


def screenshot_to_base64(screenshot_bytes: bytes) -> str:
    """将截图 bytes 转为 base64 字符串。"""
    return base64.standard_b64encode(screenshot_bytes).decode("utf-8")


def png_dimensions(png_bytes: bytes) -> tuple[int, int]:
    """从 PNG 字节读取真实像素尺寸 (width, height)。

    Playwright 的 element.screenshot() 在高分屏（Retina）下按设备像素输出，
    实际像素数可能是 CSS bounding_box 的整数倍。识别坐标必须基于截图真实尺寸，
    调用方再按 CSS 尺寸/真实尺寸的比例把 AI 坐标换算回可点击的 CSS 坐标。

    PNG 结构：8 字节签名 + IHDR 块（4 字节长度 + "IHDR" + 4 字节宽 + 4 字节高，大端）。
    """
    if len(png_bytes) < 24 or png_bytes[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("截图不是合法的 PNG，无法读取尺寸")
    width = int.from_bytes(png_bytes[16:20], "big")
    height = int.from_bytes(png_bytes[20:24], "big")
    return width, height


def parse_json_response(result: str) -> dict[str, Any]:
    """解析 AI 返回的 JSON，支持 Markdown code fence 包裹。"""
    clean_result = result.strip()
    if clean_result.startswith("```"):
        if "\n" in clean_result:
            lines = clean_result.splitlines()
            clean_result = "\n".join(lines[1:-1]).strip()
        else:
            clean_result = clean_result.strip("`").strip()
            if clean_result.lower().startswith("json"):
                clean_result = clean_result[4:].strip()
    try:
        parsed = json.loads(clean_result)
    except json.JSONDecodeError as exc:
        raise CaptchaAIError(f"AI 返回内容不是合法 JSON: {clean_result[:200]}") from exc
    if not isinstance(parsed, dict):
        raise CaptchaAIError("AI 返回 JSON 必须是对象")
    return parsed


def _extract_proxy_target(proxy_config: Mapping[str, Any] | None) -> dict[str, str] | None:
    if not isinstance(proxy_config, Mapping):
        return None

    is_enabled = _require_bool(proxy_config.get("enabled", True), key="ai_proxy.enabled")
    if any(key in proxy_config for key in ("mode", "static", "gost", "api_url", "check_timeout_seconds")):
        proxy_settings = resolve_proxy_settings(proxy_config)
        if not proxy_settings.get("enabled"):
            return None
        upstream = proxy_settings.get("bootstrap") or proxy_settings.get("static") or {}
    else:
        if not is_enabled:
            return None
        unsupported_keys = set(proxy_config) - {"enabled", "bootstrap"}
        if unsupported_keys:
            names = ", ".join(sorted(unsupported_keys))
            raise ValueError(f"ai_proxy 配置包含不支持的字段: {names}")
        upstream = proxy_config.get("bootstrap") or {}

    host = str(upstream.get("host") or upstream.get("ip") or "").strip()
    port = str(upstream.get("port") or "").strip()
    if not host or not port:
        if is_enabled:
            raise ValueError("AI 代理已启用但缺少 bootstrap.host/bootstrap.port")
        return None

    return {
        "host": host,
        "port": port,
        "username": str(upstream.get("username") or upstream.get("user") or "").strip(),
        "password": str(upstream.get("password") or ""),
    }


def _build_openrouter_proxies(proxy_config: Mapping[str, Any] | None) -> dict[str, str] | None:
    upstream = _extract_proxy_target(proxy_config)
    if not upstream:
        return None

    proxy_url = build_proxy_url(upstream)
    return {
        "http": proxy_url,
        "https": proxy_url,
    }


def _raise_for_status_with_details(response) -> None:
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        try:
            detail = " ".join(str(response.text or "").split())
        except Exception:
            detail = ""
        if detail:
            raise requests.HTTPError(
                f"{exc} | response={detail[:300]}",
                response=response,
                request=getattr(response, "request", None),
            ) from exc
        raise


class OpenRouterCaptchaClient:
    """OpenRouter 视觉验证码识别客户端。"""

    def __init__(self, api_key: str, model: str, proxy_config: Mapping[str, Any] | None = None) -> None:
        self.api_key = api_key
        self.model = model
        self.proxy_config = proxy_config
        self.url = "https://openrouter.ai/api/v1/chat/completions"

    def analyze_click_captcha(self, screenshot_base64: str, prompt_text: str) -> str:
        """识别点击验证码，返回 AI 原始文本。"""
        payload = self._build_payload(
            screenshot_base64=screenshot_base64,
            prompt=build_click_captcha_prompt(prompt_text),
            max_tokens=1024,
        )
        return self._extract_message_content(self._post(payload))

    def analyze_checkbox_captcha(self, screenshot_base64: str, image_width: int, image_height: int) -> str:
        """识别"进行人机身份验证"复选框位置，返回 AI 原始文本。

        image_width/image_height 必须是截图的真实像素尺寸（见 png_dimensions），
        AI 返回的坐标相对截图左上角。
        """
        payload = self._build_payload(
            screenshot_base64=screenshot_base64,
            prompt=build_checkbox_captcha_prompt(image_width, image_height),
            max_tokens=128,
            temperature=0,
        )
        return self._extract_message_content(self._post(payload))

    def analyze_slider_captcha(self, screenshot_base64: str, image_width: int) -> str:
        """识别滑块验证码，返回 AI 原始文本。"""
        payload = self._build_payload(
            screenshot_base64=screenshot_base64,
            prompt=build_slider_captcha_prompt(image_width),
            max_tokens=128,
            temperature=0,
        )
        return self._extract_message_content(self._post(payload))

    def _build_payload(
        self,
        *,
        screenshot_base64: str,
        prompt: str,
        max_tokens: int,
        temperature: int | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{screenshot_base64}"}},
                        {"type": "text", "text": prompt},
                    ],
                }
            ],
            "max_tokens": max_tokens,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        return payload

    def _post(self, payload: Mapping[str, Any]) -> dict[str, Any]:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        proxies = _build_openrouter_proxies(self.proxy_config)
        with requests.Session() as session:
            session.trust_env = False
            if proxies:
                session.proxies.update(proxies)
            try:
                response = session.post(self.url, headers=headers, json=payload, timeout=60)
                _raise_for_status_with_details(response)
                data = response.json()
            except Exception as exc:
                request_exception_type = getattr(requests, "RequestException", ())
                if request_exception_type and isinstance(exc, request_exception_type):
                    raise CaptchaAIError(f"OpenRouter 请求失败: {exc}") from exc
                if isinstance(exc, (json.JSONDecodeError, ValueError)):
                    raise CaptchaAIError(f"OpenRouter 响应不是合法 JSON: {exc}") from exc
                raise
            if not isinstance(data, dict):
                raise CaptchaAIError("OpenRouter 响应 JSON 必须是对象")
            return data

    def _extract_message_content(self, data: Mapping[str, Any]) -> str:
        """从 OpenRouter 响应中提取模型文本，缺字段时给出可重试错误。"""
        if "error" in data:
            raise CaptchaAIError(f"OpenRouter 返回错误: {data['error']}")
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices:
            raise CaptchaAIError("OpenRouter 响应缺少 choices")
        first_choice = choices[0]
        if not isinstance(first_choice, Mapping):
            raise CaptchaAIError("OpenRouter choices[0] 格式错误")
        message = first_choice.get("message")
        if not isinstance(message, Mapping):
            raise CaptchaAIError("OpenRouter 响应缺少 message")
        content = message.get("content")
        if not isinstance(content, str) or not content.strip():
            raise CaptchaAIError("OpenRouter 响应 content 为空")
        return content
