"""浏览器上下文：启动本机 Google Chrome，并通过 CDP 连接。"""

from __future__ import annotations

import os
import platform
import shutil
import socket
import subprocess
import time
import urllib.parse
from pathlib import Path

# 主流程使用本机 Google Chrome；缺失时 fail-fast，禁止静默回退到 Playwright Chromium
_LOCAL_CHROME_PATH: str | None = None

# 常见本机 Chrome 路径（按平台）
_LOCAL_CHROME_CANDIDATES: tuple[str, ...] = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
    str(Path.home() / "Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/google-chrome",
    "/usr/bin/chromium-browser",
    "/usr/bin/chromium",
)


def cleanup_subprocess_browser(browser=None, chrome_process=None, user_data_dir=None) -> None:
    if browser:
        chrome_process = chrome_process or getattr(browser, '_chrome_process', None)
        user_data_dir = user_data_dir or getattr(browser, '_user_data_dir', None)
        try:
            browser.close()
        except Exception:
            pass
    if chrome_process:
        try:
            if chrome_process.poll() is None:
                chrome_process.terminate()
                try:
                    chrome_process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    chrome_process.kill()
        except Exception:
            try:
                chrome_process.kill()
            except Exception:
                pass
    if user_data_dir:
        shutil.rmtree(user_data_dir, ignore_errors=True)


def get_local_chrome_path() -> str:
    """获取本机 Google Chrome 可执行文件路径（懒加载，缺失则报错）。"""
    global _LOCAL_CHROME_PATH
    if _LOCAL_CHROME_PATH is not None:
        return _LOCAL_CHROME_PATH

    env_path = str(os.environ.get("CHROME_PATH") or os.environ.get("GOOGLE_CHROME_BIN") or "").strip()
    candidates: list[str] = []
    if env_path:
        candidates.append(env_path)
    candidates.extend(_LOCAL_CHROME_CANDIDATES)

    # macOS/Linux 额外尝试 which
    if platform.system() != "Windows":
        for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
            found = shutil.which(name)
            if found:
                candidates.append(found)

    seen: set[str] = set()
    for path in candidates:
        if not path or path in seen:
            continue
        seen.add(path)
        if Path(path).is_file() and os.access(path, os.X_OK):
            _LOCAL_CHROME_PATH = path
            return _LOCAL_CHROME_PATH

    raise RuntimeError(
        "未找到本机 Google Chrome。请安装 Chrome，或设置环境变量 CHROME_PATH "
        "指向可执行文件（例如 /Applications/Google Chrome.app/Contents/MacOS/Google Chrome）"
    )


def get_chromium_path() -> str:
    """兼容旧调用名：返回本机 Chrome 路径。"""
    return get_local_chrome_path()


def find_free_port():
    """找一个可用的端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def setup_proxy_auth(cdp, proxy_settings) -> bool:
    if not proxy_settings:
        return False

    username = str(proxy_settings.get("username") or "").strip()
    password = str(proxy_settings.get("password") or "")
    if not username or not password:
        return False

    def on_auth_required(params):
        challenge = params.get("authChallenge") or {}
        source = str(challenge.get("source") or "").strip().lower()
        if source in {"proxy", ""}:
            response = {
                "response": "ProvideCredentials",
                "username": username,
                "password": password,
            }
        else:
            response = {"response": "Default"}
        try:
            cdp.send(
                "Fetch.continueWithAuth",
                {
                    "requestId": params["requestId"],
                    "authChallengeResponse": response,
                },
            )
        except Exception:
            pass

    def on_request_paused(params):
        try:
            cdp.send("Fetch.continueRequest", {"requestId": params["requestId"]})
        except Exception:
            pass

    cdp.on("Fetch.authRequired", on_auth_required)
    cdp.on("Fetch.requestPaused", on_request_paused)
    cdp.send("Fetch.enable", {"handleAuthRequests": True, "patterns": [{"urlPattern": "*"}]})
    return True


def _normalize_proxy_server(proxy_settings) -> str | None:
    if not proxy_settings:
        return None
    proxy_server = str(proxy_settings.get("server") or "").strip()
    if not proxy_server:
        return None
    parsed_proxy = urllib.parse.urlsplit(proxy_server)
    if not parsed_proxy.hostname or not parsed_proxy.port:
        return proxy_server
    return urllib.parse.urlunsplit(
        (
            parsed_proxy.scheme or "http",
            f"{parsed_proxy.hostname}:{parsed_proxy.port}",
            parsed_proxy.path,
            parsed_proxy.query,
            parsed_proxy.fragment,
        )
    )


def build_chrome_launch_command(
    chrome_path: str,
    port: int,
    user_data_dir: str,
    *,
    headless: bool,
    proxy_server: str | None = None,
) -> list[str]:
    """构建本机 Chrome 启动参数。不伪造窗口尺寸、UA 或色域。"""
    cmd = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={user_data_dir}",
        "--disable-blink-features=AutomationControlled",
        "--no-first-run",
        "--no-default-browser-check",
    ]
    if headless:
        cmd.append("--headless=new")
    if proxy_server:
        cmd.append(f"--proxy-server={proxy_server}")
    cmd.append("about:blank")
    return cmd


def build_browser_context(p, proxy_settings, headless: bool):
    """
    创建 browser + context。
    用 subprocess 启动本机 Google Chrome（不带 --enable-automation），再用 CDP 连接。
    使用本机真实 UA/时区/WebGL/屏幕，不注入伪造脚本。
    """
    return _launch_local_chrome(p, proxy_settings, headless)


def _launch_local_chrome(p, proxy_settings, headless: bool):
    """subprocess 启动本机 Google Chrome，不带 --enable-automation。"""
    import tempfile
    port = find_free_port()
    chrome_path = get_local_chrome_path()
    user_data_dir = tempfile.mkdtemp(prefix='pw_chrome_')
    chrome_process = None
    browser = None
    proxy_server = _normalize_proxy_server(proxy_settings)
    cmd = build_chrome_launch_command(
        chrome_path,
        port,
        user_data_dir,
        headless=headless,
        proxy_server=proxy_server,
    )

    try:
        chrome_process = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

        connected = False
        for _ in range(50):
            if chrome_process.poll() is not None:
                raise RuntimeError(f"Chrome 进程启动失败，退出码: {chrome_process.returncode}")
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
                    s.settimeout(1)
                    s.connect(('127.0.0.1', port))
                    connected = True
                    break
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.3)

        if not connected:
            raise RuntimeError("CDP 端口未就绪，启动超时")

        time.sleep(1)

        browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
        context = browser.contexts[0]
        page = context.pages[0] if context.pages else context.new_page()
        need_proxy_auth = bool(
            str((proxy_settings or {}).get("username") or "").strip()
            and str((proxy_settings or {}).get("password") or "")
        )
        if need_proxy_auth:
            cdp = context.new_cdp_session(page)
            proxy_auth_enabled = setup_proxy_auth(cdp, proxy_settings)
            if not proxy_auth_enabled:
                cdp.detach()
            else:
                browser._proxy_auth_cdp = cdp

        browser._chrome_process = chrome_process
        browser._user_data_dir = user_data_dir

        return browser, context, page
    except Exception:
        cleanup_subprocess_browser(browser, chrome_process, user_data_dir)
        raise
