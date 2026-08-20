"""Binance 创作者中心 API 提取。"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

CREATOR_CENTER_URL = "https://www.binance.com/zh-CN/square/creator-center/home"

COOKIE_TEXTS = ("全部允许", "Accept All", "全部拒绝", "Reject All", "确认我的选择")
GUIDE_TEXTS = ("跳过", "Skip", "我对此很熟悉，不需要指导")
ENTRY_TEXTS = (
    "查看 API",
    "查看API",
    "View API",
    "创建 API 密钥",
    "创建API密钥",
    "Create API Key",
    "API 管理",
    "API管理",
)
CREATE_TEXTS = (
    "创建 API 密钥",
    "创建API密钥",
    "Create API Key",
    "创建 API",
    "创建API",
    "生成 API",
    "生成API",
)
CLICKABLE_SELECTOR = "a, button, [role='button'], [role='link']"
HOME_READY_TEXTS = ("查看 API", "创建 API 密钥", "创作内容", "数据表现", "创作者学院")

_API_KEY_TEXT_PATTERNS = (
    re.compile(r"API\s*密钥\s*[:：]?\s*([A-Za-z0-9_-]{16,64})", re.IGNORECASE),
    re.compile(r"API\s*Key\s*[:：]?\s*([A-Za-z0-9_-]{16,64})", re.IGNORECASE),
)


def normalize_control_label(text: str) -> str:
    """去掉空白和箭头，便于匹配「查看 API >」这类控件文案。"""
    return re.sub(r"[\s>›＞·•]+", "", str(text or ""))


def is_api_entry_label(text: str) -> bool:
    """判断控件文案是否为创作者 API 入口。"""
    normalized = normalize_control_label(text)
    if not normalized:
        return False
    return any(normalize_control_label(expected) in normalized for expected in ENTRY_TEXTS + CREATE_TEXTS)


def valid_api_key(value: str) -> bool:
    """校验读取到的值是否像 API 密钥，排除创作者用户名。"""
    value = str(value or "").strip()
    if not value or value.startswith("Square-Creator-"):
        return False
    return 16 <= len(value) <= 64 and bool(re.fullmatch(r"[A-Za-z0-9_-]+", value))


def _mask_key(value: str) -> str:
    """日志脱敏，只保留密钥首尾各 4 位。"""
    if len(value) <= 8:
        return "***"
    return f"{value[:4]}...{value[-4:]}"


def extract_api_key_from_text(text: str) -> str:
    """从「API 密钥」标签后的文本读取密钥，禁止整页乱猜。"""
    raw = str(text or "")
    if not raw:
        return ""
    for pattern in _API_KEY_TEXT_PATTERNS:
        match = pattern.search(raw)
        if match and valid_api_key(match.group(1)):
            return match.group(1)
    return ""


def _visible(locator: Any) -> bool:
    try:
        return locator.count() > 0 and locator.first.is_visible()
    except Exception:
        return False


def _locator_text(locator: Any) -> str:
    try:
        return str(locator.inner_text(timeout=5000) or "")
    except Exception:
        try:
            return str(locator.first.inner_text(timeout=5000) or "")
        except Exception:
            return ""


def _text_pattern(text: str) -> re.Pattern[str]:
    parts = [re.escape(part) for part in re.split(r"\s+", str(text or "").strip()) if part]
    if not parts:
        return re.compile(r"$^")
    return re.compile(r"\s*".join(parts), re.IGNORECASE)


def _click_locator(locator: Any) -> bool:
    try:
        locator.click(timeout=8000)
        return True
    except Exception:
        try:
            locator.click(timeout=3000, force=True)
            return True
        except Exception:
            return False


def _candidate_locators(page: Any, text: str) -> list[Any]:
    pattern = _text_pattern(text)
    factories = (
        lambda: page.locator(CLICKABLE_SELECTOR).filter(has_text=pattern),
        lambda: page.get_by_role("button", name=pattern),
        lambda: page.get_by_role("link", name=pattern),
        lambda: page.get_by_text(pattern),
    )
    locators = []
    for factory in factories:
        try:
            locators.append(factory())
        except Exception:
            continue
    return locators


def _click_by_texts(page: Any, texts: tuple[str, ...]) -> str:
    """按文案点击第一个可见控件，允许箭头/空白差异；返回实际命中的文案。"""
    for text in texts:
        for locator in _candidate_locators(page, text):
            try:
                target = locator.first
            except Exception:
                continue
            if not _visible(target):
                continue
            if _click_locator(target):
                return text
    return ""


def _pages_after(page: Any, before_pages: list[Any]) -> Any:
    """返回点击后新打开的页面，避免在旧 tab 上读取 API。"""
    try:
        pages = list(page.context.pages)
    except Exception:
        return page
    for candidate in pages:
        if candidate not in before_pages:
            return candidate
    return page


def _settle(page: Any, timeout: int = 8000) -> None:
    """只等 DOM，不等 networkidle。广场页常有长连接，networkidle 会空等满超时。"""
    try:
        page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    page.wait_for_timeout(800)


def _debug_page(page: Any, label: str) -> None:
    """保存页面证据，仅用于本地调试，不写入账号结果。"""
    debug_dir = Path("output/creator_api_debug")
    debug_dir.mkdir(parents=True, exist_ok=True)
    safe_label = re.sub(r"[^A-Za-z0-9_.-]+", "_", label)
    try:
        page.screenshot(path=str(debug_dir / f"{safe_label}.png"), full_page=True)
    except Exception:
        pass
    try:
        url = getattr(page, "url", "")
        title = page.title() if hasattr(page, "title") else ""
        html = page.locator("body").inner_text(timeout=5000)
        (debug_dir / f"{safe_label}.txt").write_text(
            f"url={url}\ntitle={title}\n\n{html}",
            encoding="utf-8",
        )
    except Exception:
        pass


def _read_named_inputs(page: Any) -> str:
    selectors = (
        "input[aria-label*='API' i]",
        "input[name*='api' i]",
        "input[id*='api' i]",
        "input[data-testid*='api' i]",
        "textarea[aria-label*='API' i]",
        "textarea[name*='api' i]",
        "textarea[id*='api' i]",
    )
    for selector in selectors:
        locator = page.locator(selector)
        for index in range(min(_safe_count(locator), 20)):
            item = locator.nth(index)
            if not _visible(item):
                continue
            try:
                value = (item.input_value() or "").strip()
            except Exception:
                value = str(item.get_attribute("value") or "").strip()
            if valid_api_key(value):
                return value
    return ""


def _read_labeled_inputs(page: Any) -> str:
    labels = page.locator("label")
    for index in range(min(_safe_count(labels), 20)):
        label = labels.nth(index)
        if not _visible(label):
            continue
        text = normalize_control_label(label.inner_text()).lower()
        if "api" not in text or ("key" not in text and "密钥" not in text):
            continue
        target_id = label.get_attribute("for")
        candidates = page.locator(f"#{target_id}") if target_id else label.locator("xpath=following::input[1]")
        if not _visible(candidates):
            continue
        try:
            value = candidates.first.input_value().strip()
        except Exception:
            value = str(candidates.first.get_attribute("value") or "").strip()
        if valid_api_key(value):
            return value
    return ""


def _safe_count(locator: Any) -> int:
    try:
        return int(locator.count())
    except Exception:
        return 0


def _read_api_value(page: Any) -> str:
    """只读取明确标记为 API key 的字段或「API 密钥」标签后的值。"""
    value = _read_named_inputs(page)
    if value:
        return value
    value = _read_labeled_inputs(page)
    if value:
        return value
    for selector in ("[role='dialog']", "body"):
        locator = page.locator(selector)
        if selector != "body" and not _visible(locator):
            continue
        value = extract_api_key_from_text(_locator_text(locator))
        if value:
            return value
    return ""


def _describe_page(page: Any) -> str:
    try:
        title = page.title()
        url = page.url
        labels = page.locator(CLICKABLE_SELECTOR).all_inner_texts()[:30]
    except Exception:
        title, url, labels = "", getattr(page, "url", ""), []
    return f"url={url}, title={title}, controls={labels}"


class CreatorCenterApiExtractor:
    """进入创作者中心，关闭引导层，打开 API 管理并读取密钥。"""

    def __init__(self, page: Any, *, page_timeout: int = 60000) -> None:
        self.page = page
        self.page_timeout = page_timeout

    def extract(self) -> str:
        page = self.page
        print("[creator-api] 打开创作者中心")
        page.goto(CREATOR_CENTER_URL, wait_until="domcontentloaded", timeout=self.page_timeout)
        _settle(page)
        self._wait_for_home()
        print("[creator-api] 关闭 Cookie / 新手引导")
        self._dismiss_overlays()

        value = _read_api_value(page)
        if value:
            print(f"[creator-api] 页面已展示 API 密钥，直接读取 ({_mask_key(value)})")
            return value

        print("[creator-api] 尝试点击 API 管理入口")
        page = self._click_and_settle(ENTRY_TEXTS)
        value = _read_api_value(page)
        if value:
            print(f"[creator-api] 已从 API 管理入口读取密钥 ({_mask_key(value)})")
            return value

        print("[creator-api] 尝试创建 API 密钥")
        page = self._click_and_settle(CREATE_TEXTS)
        page.wait_for_timeout(1500)
        value = _read_api_value(page)
        if value:
            print(f"[creator-api] 已创建并读取 API 密钥 ({_mask_key(value)})")
            return value

        _debug_page(page, "api_key_not_found")
        raise RuntimeError(f"创作者中心未找到 API 密钥 ({_describe_page(page)})")

    def _wait_for_home(self) -> None:
        pattern = re.compile("|".join(_text_pattern(text).pattern for text in HOME_READY_TEXTS))
        try:
            self.page.get_by_text(pattern).first.wait_for(state="visible", timeout=15000)
        except Exception:
            pass

    def _dismiss_overlays(self) -> None:
        if _click_by_texts(self.page, COOKIE_TEXTS):
            self.page.wait_for_timeout(800)
        if _click_by_texts(self.page, GUIDE_TEXTS):
            self.page.wait_for_timeout(800)

    def _click_and_settle(self, texts: tuple[str, ...]) -> Any:
        try:
            before_pages = list(self.page.context.pages)
        except Exception:
            before_pages = [self.page]
        clicked = _click_by_texts(self.page, texts)
        if not clicked:
            print("[creator-api] 未找到可点击入口")
            return self.page
        print(f"[creator-api] 已点击: {clicked}")
        self.page = _pages_after(self.page, before_pages)
        _settle(self.page)
        self._dismiss_overlays()
        self._wait_for_api_dialog()
        return self.page

    def _wait_for_api_dialog(self) -> None:
        pattern = re.compile("|".join(_text_pattern(text).pattern for text in ("API 密钥", "API Key", "API 管理")))
        try:
            self.page.get_by_text(pattern).first.wait_for(state="visible", timeout=8000)
        except Exception:
            pass


def extract_creator_api(page: Any, config: dict, *, page_timeout: int = 60000) -> str:
    """进入创作者中心并读取 API；缺失 API 时尝试创建。"""
    _ = config
    return CreatorCenterApiExtractor(page, page_timeout=page_timeout).extract()


def api_metadata(api_key: str) -> dict:
    return {"api_key": api_key, "api_extracted_at": datetime.now(timezone.utc).isoformat()}
