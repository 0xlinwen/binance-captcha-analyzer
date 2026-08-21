"""Binance 创作者中心 API 提取。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
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
_DISPLAY_NAME_PATTERN = re.compile(r"([^\n@]{1,80})\s*@Square-Creator-[A-Za-z0-9]+")
_DEFAULT_USER_CODE = re.compile(r"^User-[0-9a-fA-F]{6,}$")
_DISPLAY_NAME_BLOCKLIST = frozenset(
    {
        "立即认证",
        "编辑",
        "查看api",
        "查看api>",
        "创建api密钥",
        "创作内容",
        "首页",
        "内容管理",
        "数据中心",
        "创作者学院",
        "打赏",
        "完成",
        "跳过",
        "关注",
        "粉丝",
        "点赞",
        "分享",
        "好的",
        "删除api",
        "api管理",
        "api密钥",
        "verify",
        "verify now",
        "edit",
        "view api",
        "create api key",
    }
)


@dataclass(frozen=True)
class CreatorCenterProfile:
    """创作者中心提取结果：API 密钥、编辑资料中的昵称和用户名。"""

    api_key: str
    display_name: str = ""
    username: str = ""


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


def is_default_user_code(value: str) -> bool:
    """判断是否为广场默认用户名 User-xxxxxxxx，不是资料卡展示昵称。"""
    return bool(_DEFAULT_USER_CODE.fullmatch(str(value or "").strip()))


def valid_display_name(value: str) -> bool:
    """校验资料卡展示名称，排除默认用户名、创作者句柄和页面按钮文案。"""
    name = str(value or "").strip()
    if not name or name.startswith("@") or name.startswith("Square-Creator-"):
        return False
    if is_default_user_code(name):
        return False
    if not (1 <= len(name) <= 80):
        return False
    normalized = normalize_control_label(name).lower()
    if not normalized or normalized in _DISPLAY_NAME_BLOCKLIST:
        return False
    return not name.isdigit()


def is_profile_card_title_text(value: str) -> bool:
    """资料卡标题允许 User-xxxx（没改昵称时页面上就是它），但排除按钮和句柄。"""
    name = str(value or "").strip()
    if not name or name.startswith("@") or "Square-Creator-" in name:
        return False
    if not (1 <= len(name) <= 80):
        return False
    normalized = normalize_control_label(name).lower()
    if not normalized or normalized in _DISPLAY_NAME_BLOCKLIST:
        return False
    return not name.isdigit()


def pick_visible_display_name(candidates: list[dict[str, Any]]) -> str:
    """从页面实际绘制的文本里选资料卡标题。

    在 @Square-Creator- 左侧资料卡区域内取字号最大的可见文本。
    同时存在自定义昵称和 User-xxxx 时优先昵称；页面上只显示 User-xxxx 时就存它。
    """
    nodes: list[dict[str, Any]] = []
    for item in candidates or []:
        text = re.sub(r"\s+", " ", str(item.get("text") or "")).strip()
        if not text:
            continue
        nodes.append(
            {
                "text": text,
                "fontSize": float(item.get("fontSize") or 0),
                "fontWeight": int(item.get("fontWeight") or 0),
                "x": float(item.get("x") or 0),
                "y": float(item.get("y") or 0),
            }
        )
    handle = next((node for node in nodes if re.search(r"@?Square-Creator-[A-Za-z0-9]+", node["text"])), None)
    if handle is None:
        return ""
    in_card = [
        node
        for node in nodes
        if handle["x"] - 640 <= node["x"] < handle["x"]
        and abs(node["y"] - handle["y"]) <= 96
        and is_profile_card_title_text(node["text"])
    ]
    if not in_card:
        return ""
    max_size = max(node["fontSize"] for node in in_card)
    cluster = [node for node in in_card if abs(node["fontSize"] - max_size) <= 1]
    nicknames = [node for node in cluster if not is_default_user_code(node["text"])]
    chosen = nicknames or cluster
    chosen.sort(key=lambda node: (node["y"], node["x"]))
    merged = " ".join(node["text"] for node in chosen).strip()
    if is_profile_card_title_text(merged):
        return merged
    chosen.sort(key=lambda node: (node["fontSize"], node["fontWeight"]), reverse=True)
    return chosen[0]["text"] if chosen else ""


def extract_display_name_from_text(text: str) -> str:
    """读取资料卡昵称：取 @Square-Creator- 前方文本，跳过 User-xxxx 默认用户名。"""
    raw = str(text or "")
    handle_match = re.search(r"@Square-Creator-[A-Za-z0-9]+", raw)
    if handle_match:
        before = raw[: handle_match.start()]
        lines = [line.strip() for line in before.splitlines() if line.strip()]
        considered = 0
        for line in reversed(lines):
            line = re.split(r"\s{2,}", line)[0].strip()
            if is_default_user_code(line):
                continue
            considered += 1
            if valid_display_name(line):
                return line
            if considered >= 2:
                break
    for match in _DISPLAY_NAME_PATTERN.finditer(raw):
        candidate = match.group(1).strip()
        candidate = re.split(r"\s{2,}", candidate)[0].strip()
        if valid_display_name(candidate):
            return candidate
    return ""


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


_COLLECT_VISIBLE_TEXT_JS = """() => {
  const inDialog = (el) => !!(el && el.closest && el.closest('[role="dialog"]'));
  const isPainted = (el) => {
    if (!el) return false;
    const style = window.getComputedStyle(el);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity) === 0) {
      return false;
    }
    const rect = el.getBoundingClientRect();
    return rect.width > 1 && rect.height > 1;
  };
  const items = [];
  const collect = (root) => {
    if (!root) return;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
    let node = walker.nextNode();
    while (node) {
      const text = String(node.textContent || '').replace(/\\s+/g, ' ').trim();
      const el = node.parentElement;
      if (text && text.length <= 80 && el && !inDialog(el) && isPainted(el)) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        items.push({
          text,
          fontSize: parseFloat(style.fontSize) || 0,
          fontWeight: parseInt(style.fontWeight, 10) || 400,
          x: rect.x,
          y: rect.y,
        });
      }
      node = walker.nextNode();
    }
    const elements = root.querySelectorAll ? root.querySelectorAll('*') : [];
    for (const el of elements) {
      if (el.shadowRoot) collect(el.shadowRoot);
    }
  };
  collect(document.body);
  return items;
}"""


def _read_visible_display_name(page: Any) -> str:
    """读取资料卡上实际画出来的昵称，不读接口/隐藏字段。"""
    try:
        candidates = page.evaluate(_COLLECT_VISIBLE_TEXT_JS)
    except Exception as exc:
        print(f"[creator-api] 读取可见文本失败: {exc}")
        return ""
    if not isinstance(candidates, list):
        return ""
    handle_count = sum(
        1
        for item in candidates
        if re.search(r"@?Square-Creator-[A-Za-z0-9]+", str(item.get("text") or ""))
    )
    print(f"[creator-api] 可见文本 {len(candidates)} 个，句柄 {handle_count} 个")
    if handle_count == 0:
        sample = [str(item.get("text") or "") for item in candidates[:40]]
        print(f"[creator-api] 可见文本样例: {sample}")
        try:
            debug_dir = Path("output/creator_api_debug")
            debug_dir.mkdir(parents=True, exist_ok=True)
            (debug_dir / "visible_texts.json").write_text(
                json.dumps(candidates, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
        except Exception:
            pass
    return pick_visible_display_name(candidates)


def _close_edit_form(page: Any) -> None:
    _click_by_texts(page, ("取消", "Cancel", "关闭", "Close"))
    if "creator-center" not in str(getattr(page, "url", "")):
        try:
            page.goto(CREATOR_CENTER_URL, wait_until="domcontentloaded", timeout=60000)
            _settle(page)
        except Exception:
            pass


def _read_input_value(locator: Any) -> str:
    try:
        return str(locator.input_value() or "").strip()
    except Exception:
        try:
            return str(locator.first.input_value() or "").strip()
        except Exception:
            return str(locator.get_attribute("value") or locator.first.get_attribute("value") or "").strip()


def _read_input_after_label(page: Any, label_texts: tuple[str, ...]) -> str:
    """读取「编辑个人资料」里某个标签后面的输入框。"""
    for text in label_texts:
        try:
            label = page.get_by_text(text, exact=False)
        except Exception:
            continue
        if not _visible(label):
            continue
        try:
            field = label.first.locator("xpath=following::input[1]")
        except Exception:
            continue
        if not _visible(field):
            continue
        value = _read_input_value(field.first)
        if value:
            return value
    return ""


def _wait_for_edit_profile_form(page: Any) -> bool:
    for title in ("编辑个人资料", "Edit Profile"):
        try:
            page.get_by_text(title, exact=False).first.wait_for(state="visible", timeout=8000)
            return True
        except Exception:
            continue
    return False


def _read_edit_profile(page: Any) -> tuple[str, str]:
    """点击编辑后读取昵称和用户名，然后取消关闭，不改资料。"""
    if not _click_by_texts(page, ("编辑", "Edit")):
        print("[creator-api] 未找到编辑按钮")
        return "", ""
    page.wait_for_timeout(800)
    if not _wait_for_edit_profile_form(page):
        print("[creator-api] 编辑个人资料弹窗未出现")
        _debug_page(page, "edit_profile_not_found")
        _close_edit_form(page)
        return "", ""
    try:
        nickname = _read_input_after_label(page, ("昵称", "Nickname"))
        username = _read_input_after_label(page, ("用户名", "Username")).lstrip("@")
        if nickname:
            print(f"[creator-api] 已读取昵称: {nickname}")
        else:
            print("[creator-api] 编辑资料中未找到昵称")
        if username:
            print(f"[creator-api] 已读取用户名: {username}")
        else:
            print("[creator-api] 编辑资料中未找到用户名")
        if not nickname and not username:
            _debug_page(page, "edit_profile_fields_not_found")
        return nickname, username
    finally:
        _close_edit_form(page)


def _return_to_creator_home(page: Any) -> Any:
    url = str(getattr(page, "url", ""))
    if "creator-center" in url:
        return page
    try:
        page.goto(CREATOR_CENTER_URL, wait_until="domcontentloaded", timeout=60000)
        _settle(page)
    except Exception:
        pass
    return page


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
    """进入创作者中心：读取 API 密钥，再打开编辑资料读取昵称和用户名。"""

    def __init__(self, page: Any, *, page_timeout: int = 60000) -> None:
        self.page = page
        self.page_timeout = page_timeout

    def extract(self) -> CreatorCenterProfile:
        page = self.page
        print("[creator-api] 打开创作者中心")
        page.goto(CREATOR_CENTER_URL, wait_until="domcontentloaded", timeout=self.page_timeout)
        _settle(page)
        self._wait_for_home()
        print("[creator-api] 关闭 Cookie / 新手引导")
        self._dismiss_overlays()
        self._wait_for_profile_card()

        value = _read_api_value(self.page)
        if not value:
            print("[creator-api] 尝试点击 API 管理入口")
            page = self._click_and_settle(ENTRY_TEXTS)
            value = _read_api_value(page)
        if not value:
            print("[creator-api] 尝试创建 API 密钥")
            page = self._click_and_settle(CREATE_TEXTS)
            page.wait_for_timeout(1500)
            value = _read_api_value(page)
        if not value:
            _debug_page(self.page, "api_key_not_found")
            raise RuntimeError(f"创作者中心未找到 API 密钥 ({_describe_page(self.page)})")
        print(f"[creator-api] 已读取 API 密钥 ({_mask_key(value)})")

        print("[creator-api] 关闭 API 弹窗并打开编辑资料")
        _click_by_texts(self.page, ("好的", "OK", "Got it"))
        self.page.wait_for_timeout(500)
        self.page = _return_to_creator_home(self.page)
        nickname, username = _read_edit_profile(self.page)
        return CreatorCenterProfile(api_key=value, display_name=nickname, username=username)

    def _wait_for_profile_card(self) -> None:
        try:
            self.page.get_by_text(re.compile(r"@Square-Creator-")).first.wait_for(state="visible", timeout=15000)
        except Exception:
            pass

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


def extract_creator_api(page: Any, config: dict, *, page_timeout: int = 60000) -> CreatorCenterProfile:
    """进入创作者中心并读取 API 与展示名称；缺失 API 时尝试创建。"""
    _ = config
    return CreatorCenterApiExtractor(page, page_timeout=page_timeout).extract()


def api_metadata(profile: CreatorCenterProfile) -> dict:
    data = {"api_key": profile.api_key, "api_extracted_at": datetime.now(timezone.utc).isoformat()}
    if profile.display_name:
        data["display_name"] = profile.display_name
    if profile.username:
        data["username"] = profile.username
    return data
