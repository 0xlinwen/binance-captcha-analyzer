from __future__ import annotations

import re
import unittest
from unittest.mock import patch

from binance_analyzer.creator_api import (
    CREATOR_CENTER_URL,
    CreatorCenterApiExtractor,
    _click_by_texts,
    _read_api_value,
    _text_pattern,
    extract_api_key_from_text,
    extract_creator_api,
    is_api_entry_label,
    valid_api_key,
)

CAPTURED_CREATOR_HOME = """
展示最好的自己
管理您的个人资料，吸引更多粉丝关注与互动。
步骤: 1 /1
完成
我对此很熟悉，不需要指导。
跳过
首页
内容管理
数据中心
创作者学院
打赏
创作内容
Efrain Recendez mUgO
@Square-Creator-680f65c3fe836
立即认证
编辑
查看 API
2
关注
0
粉丝
API 管理
API 密钥
ec7d6cea1b1642fa9636ab4035ba8834
该 API 仅用于发布短帖和视频，两者合计的每日限额为 100。
好的
删除 API
数据表现
API 密钥创建成功
"""


class _FakeElement:
    def __init__(self, text: str, *, role: str = "button", visible: bool = True, value: str = "") -> None:
        self.text = text
        self.role = role
        self.visible = visible
        self.value = value
        self.clicked = False
        self.force_clicked = False

    def count(self) -> int:
        return 1

    @property
    def first(self) -> "_FakeElement":
        return self

    def nth(self, index: int) -> "_FakeElement":
        if index != 0:
            raise IndexError(index)
        return self

    def is_visible(self) -> bool:
        return self.visible

    def click(self, **kwargs) -> None:
        self.clicked = True
        if kwargs.get("force"):
            self.force_clicked = True

    def inner_text(self, timeout: int | None = None) -> str:
        return self.text

    def input_value(self) -> str:
        return self.value

    def get_attribute(self, name: str) -> str:
        if name == "value":
            return self.value
        return ""

    def locator(self, _selector: str) -> "_FakeLocator":
        return _FakeLocator([])

    def wait_for(self, **_kwargs) -> None:
        if not self.visible:
            raise RuntimeError("not visible")


class _FakeLocator:
    def __init__(self, elements: list[_FakeElement]) -> None:
        self.elements = elements

    def count(self) -> int:
        return len(self.elements)

    @property
    def first(self) -> _FakeElement | "_FakeLocator":
        return self.elements[0] if self.elements else _FakeLocator([])

    def nth(self, index: int) -> _FakeElement:
        return self.elements[index]

    def is_visible(self) -> bool:
        return any(item.is_visible() for item in self.elements)

    def click(self, **kwargs) -> None:
        if not self.elements:
            raise RuntimeError("nothing to click")
        self.elements[0].click(**kwargs)

    def inner_text(self, timeout: int | None = None) -> str:
        if not self.elements:
            raise RuntimeError("no text")
        return self.elements[0].inner_text()

    def all_inner_texts(self) -> list[str]:
        return [item.inner_text() for item in self.elements]

    def filter(self, has_text: str | re.Pattern[str] | None = None) -> "_FakeLocator":
        if has_text is None:
            return self
        matched = [item for item in self.elements if _text_matches(item.text, has_text)]
        return _FakeLocator(matched)

    def wait_for(self, **_kwargs) -> None:
        if not self.elements:
            raise RuntimeError("timeout")


class _FakeContext:
    def __init__(self, page: "_FakePage") -> None:
        self.pages = [page]


class _FakePage:
    def __init__(self, body_text: str, controls: list[_FakeElement] | None = None) -> None:
        self.body_text = body_text
        self.controls = controls or []
        self.url = CREATOR_CENTER_URL
        self.context = _FakeContext(self)
        self.gotos: list[str] = []

    def goto(self, url: str, **_kwargs) -> None:
        self.gotos.append(url)
        self.url = url

    def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    def wait_for_timeout(self, _timeout_ms: int) -> None:
        return None

    def title(self) -> str:
        return "Creator Center"

    def screenshot(self, **_kwargs) -> None:
        return None

    def locator(self, selector: str) -> _FakeLocator:
        if selector == "body":
            return _FakeLocator([_FakeElement(self.body_text, role="document")])
        if selector == "[role='dialog']":
            if "API 管理" in self.body_text:
                return _FakeLocator([_FakeElement(self.body_text, role="dialog")])
            return _FakeLocator([])
        if selector in {"a, button, [role='button'], [role='link']", "a,button,[role='button']"}:
            return _FakeLocator(self.controls)
        if selector.startswith("input") or selector.startswith("textarea") or selector == "label":
            return _FakeLocator([])
        return _FakeLocator([])

    def get_by_role(self, role: str, name: str | re.Pattern[str] | None = None, exact: bool = False) -> _FakeLocator:
        matched = []
        for item in self.controls:
            if item.role != role:
                continue
            if name is None or _text_matches(item.text, name, exact=exact):
                matched.append(item)
        return _FakeLocator(matched)

    def get_by_text(self, text: str | re.Pattern[str], exact: bool = False) -> _FakeLocator:
        matched = [item for item in self.controls if _text_matches(item.text, text, exact=exact)]
        if not matched and _text_matches(self.body_text, text, exact=exact):
            matched.append(_FakeElement(self.body_text, role="document"))
        return _FakeLocator(matched)


def _text_matches(haystack: str, needle: str | re.Pattern[str], *, exact: bool = False) -> bool:
    if hasattr(needle, "search"):
        return needle.search(haystack) is not None
    text = str(needle)
    if exact:
        return haystack == text
    return text in haystack


class ExtractApiKeyFromTextTests(unittest.TestCase):
    def test_reads_key_after_chinese_label(self) -> None:
        self.assertEqual(
            extract_api_key_from_text(CAPTURED_CREATOR_HOME),
            "ec7d6cea1b1642fa9636ab4035ba8834",
        )

    def test_does_not_use_square_creator_username(self) -> None:
        text = "Efrain Recendez mUgO\n@Square-Creator-680f65c3fe836\n立即认证\n编辑\n查看 API"
        self.assertEqual(extract_api_key_from_text(text), "")

    def test_reads_english_api_key_label(self) -> None:
        text = "API Management\nAPI Key\nabcdEFGH1234567890xyz_key\nOK"
        self.assertEqual(extract_api_key_from_text(text), "abcdEFGH1234567890xyz_key")

    def test_ignores_success_toast_without_key_value(self) -> None:
        self.assertEqual(extract_api_key_from_text("API 密钥创建成功"), "")


class ValidApiKeyTests(unittest.TestCase):
    def test_rejects_username_and_short_values(self) -> None:
        self.assertFalse(valid_api_key("Square-Creator-680f65c3fe836"))
        self.assertFalse(valid_api_key("short-key"))
        self.assertTrue(valid_api_key("ec7d6cea1b1642fa9636ab4035ba8834"))


class ApiEntryLabelTests(unittest.TestCase):
    def test_matches_chevron_and_create_labels(self) -> None:
        self.assertTrue(is_api_entry_label("查看 API >"))
        self.assertTrue(is_api_entry_label("创建 API 密钥"))
        self.assertFalse(is_api_entry_label("创作内容"))
        self.assertFalse(is_api_entry_label("立即认证"))

    def test_text_pattern_allows_chevron_suffix(self) -> None:
        pattern = _text_pattern("查看 API")
        self.assertIsNotNone(pattern.search("查看 API >"))
        self.assertIsNotNone(pattern.search("查看API"))
        self.assertIsNone(pattern.search("创作内容"))


class ClickByTextsTests(unittest.TestCase):
    def test_clicks_view_api_even_when_label_has_chevron(self) -> None:
        view_api = _FakeElement("查看 API >")
        page = _FakePage("创作者中心", controls=[_FakeElement("创作内容"), view_api])

        clicked = _click_by_texts(page, ("查看 API", "创建 API 密钥"))

        self.assertEqual(clicked, "查看 API")
        self.assertTrue(view_api.clicked)


class ReadApiValueTests(unittest.TestCase):
    def test_reads_key_from_dialog_text_without_inputs(self) -> None:
        page = _FakePage(CAPTURED_CREATOR_HOME)
        self.assertEqual(_read_api_value(page), "ec7d6cea1b1642fa9636ab4035ba8834")


class ExtractCreatorApiTests(unittest.TestCase):
    def test_extracts_visible_key_without_requiring_entry_click(self) -> None:
        page = _FakePage(
            CAPTURED_CREATOR_HOME,
            controls=[
                _FakeElement("跳过"),
                _FakeElement("查看 API >"),
                _FakeElement("创作内容"),
            ],
        )

        value = extract_creator_api(page, {})

        self.assertEqual(value, "ec7d6cea1b1642fa9636ab4035ba8834")
        self.assertEqual(page.gotos, [CREATOR_CENTER_URL])
        self.assertTrue(page.controls[0].clicked)
        self.assertFalse(page.controls[1].clicked)

    def test_clicks_view_api_when_key_is_not_visible_yet(self) -> None:
        view_api = _FakeElement("查看 API >")
        page = _FakePage("首页\n数据表现\n创作内容", controls=[view_api, _FakeElement("创作内容")])

        def fake_read(_page: _FakePage) -> str:
            if view_api.clicked:
                return "ec7d6cea1b1642fa9636ab4035ba8834"
            return ""

        with patch("binance_analyzer.creator_api._read_api_value", side_effect=fake_read):
            value = CreatorCenterApiExtractor(page).extract()

        self.assertEqual(value, "ec7d6cea1b1642fa9636ab4035ba8834")
        self.assertTrue(view_api.clicked)


if __name__ == "__main__":
    unittest.main()
