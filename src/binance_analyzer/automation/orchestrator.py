import random
import shutil
from dataclasses import replace
from pathlib import Path

from playwright.sync_api import sync_playwright

from .automation_driver import build_driver
from ..storage.registered_account_storage import save_registered_account
from ..runtime.local_cache import init_cache_manager
from ..fingerprint import describe_fingerprint, generate_fingerprint, is_native_fingerprint
from .cache_routes import handle_cache_route, track_cache_response
from .browser_context import (
    build_stealth_context,
    build_stealth_init_script,
    cleanup_subprocess_browser,
    get_launch_args,
)
from ..integrations.local_proxy_pool import bind_local_rotating_proxy
from ..integrations.proxy_integration import (
    build_proxy_launch_config,
    create_proxy_runtime,
    describe_proxy_runtime,
    make_proxy_logger,
    stop_managed_proxy_runtime,
)
from ..results import AccountStatus, AutomationResult
from ..flows.page_signals import is_dashboard_url
from ..integrations.creator_api import api_metadata, extract_creator_api
from ..integrations.creator_api_quota import acquire_creator_api_slot, release_creator_api_slot
from ..storage.credential_export import export_credentials
from ..runtime.logger import get_logger_manager

PAGE_TIMEOUT = 60000
# orchestrator.py 位于 src/binance_analyzer/automation/，项目根是 parents[3]
PROJECT_ROOT = Path(__file__).resolve().parents[3]
CACHE_DIR = PROJECT_ROOT / ".browser_cache"
MASTER_CACHE_DIR = CACHE_DIR / "master"

def _safe_int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _build_account_proxy_config(proxy_config: dict) -> dict:
    runtime_proxy_config = dict(proxy_config or {})
    gost = runtime_proxy_config.get("gost")
    if isinstance(gost, dict):
        runtime_proxy_config["gost"] = {**gost, "listen_port": 0}
    return runtime_proxy_config


def warmup_cache(proxy_config=None, headless=True):
    print("预热浏览器缓存...")
    MASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    init_cache_manager(CACHE_DIR)
    fingerprint = generate_fingerprint(use_real_profile=True, mode="native")
    proxy_runtime = None
    proxy_settings = None
    base_dir = PROJECT_ROOT
    pool_manager = None
    lease_id = None
    lease_result = "proxy_failed"
    try:
        if proxy_config and proxy_config.get("enabled"):
            runtime_proxy, pool_manager, lease_id = bind_local_rotating_proxy(base_dir, proxy_config)
            if str(proxy_config.get("mode") or "").strip().lower() == "rotating_single_ip" and not lease_id:
                raise RuntimeError("缓存预热代理初始化失败：固定池没有可用条目")
            proxy_runtime = create_proxy_runtime(
                {
                    "mode": "login",
                    "login": {"start_url": "https://accounts.binance.com/zh-CN/login"},
                    "proxy": runtime_proxy,
                },
                max_attempts=_safe_int(proxy_config.get("max_attempts", 3), 3),
                skip_check=True,
                logger=make_proxy_logger("[cache]"),
            )
            proxy_settings = build_proxy_launch_config(proxy_runtime)
            if proxy_settings:
                print(f"[cache] 代理就绪: {describe_proxy_runtime(proxy_runtime)}")
            else:
                stop_managed_proxy_runtime(proxy_runtime)
                proxy_runtime = None
                raise RuntimeError("缓存预热代理初始化失败：proxy.enabled=true 时禁止回退为直连")

        with sync_playwright() as p:
            launch_kwargs = {
                "user_data_dir": str(MASTER_CACHE_DIR),
                "headless": headless,
                "channel": "chrome",
                "args": [
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                "proxy": proxy_settings,
            }
            if not is_native_fingerprint(fingerprint):
                launch_kwargs["args"] = get_launch_args(
                    fingerprint["screen_width"], fingerprint["screen_height"]
                )
                launch_kwargs.update(
                    user_agent=fingerprint["user_agent"],
                    locale=fingerprint["locale"],
                    timezone_id=fingerprint["timezone_id"],
                    viewport={
                        "width": fingerprint["screen_width"],
                        "height": fingerprint["screen_height"] - 80,
                    },
                    screen={
                        "width": fingerprint["screen_width"],
                        "height": fingerprint["screen_height"],
                    },
                    device_scale_factor=fingerprint["device_pixel_ratio"],
                )
            context = p.chromium.launch_persistent_context(**launch_kwargs)
            try:
                if not is_native_fingerprint(fingerprint):
                    context.add_init_script(build_stealth_init_script(fingerprint))
                page = context.new_page()
                page.route("**/*", handle_cache_route)
                page.on("response", track_cache_response)
                for url in [
                    "https://accounts.binance.com/zh-CN/login",
                    "https://accounts.binance.com/zh-CN/register",
                ]:
                    print(f"访问: {url}")
                    try:
                        page.goto(url, wait_until="domcontentloaded", timeout=30000)
                        page.wait_for_timeout(5000)
                    except Exception as e:
                        print(f"  加载异常: {e}，继续...")
                context.clear_cookies()
                print("缓存预热完成")
                lease_result = "success"
            finally:
                context.close()
    finally:
        stop_managed_proxy_runtime(proxy_runtime)
        if pool_manager and lease_id:
            pool_manager.release(lease_id, result_status=lease_result)


def _get_worker_cache_dir(worker_id: int) -> Path:
    return CACHE_DIR / f"worker_{worker_id}"


def _init_worker_cache(worker_id: int) -> Path:
    worker_dir = _get_worker_cache_dir(worker_id)
    if worker_dir.exists():
        shutil.rmtree(worker_dir, ignore_errors=True)
    if MASTER_CACHE_DIR.exists():
        print(f"[Worker-{worker_id}] 从 master 缓存复制...")
        shutil.copytree(MASTER_CACHE_DIR, worker_dir, dirs_exist_ok=True)
    else:
        worker_dir.mkdir(parents=True, exist_ok=True)
    return worker_dir


def _sync_new_cache_to_master(worker_id: int):
    worker_dir = _get_worker_cache_dir(worker_id)
    if not worker_dir.exists() or not MASTER_CACHE_DIR.exists():
        return

    worker_cache = worker_dir / "Default" / "Cache" / "Cache_Data"
    master_cache = MASTER_CACHE_DIR / "Default" / "Cache" / "Cache_Data"
    if not worker_cache.exists():
        worker_cache = worker_dir / "Default" / "Cache"
    if not master_cache.exists():
        master_cache = MASTER_CACHE_DIR / "Default" / "Cache"

    skip_keywords = ["captcha", "puzzle", "slider", "bncaptcha", "geetest", "bnc-cap", "s3.amazonaws"]

    if worker_cache.exists() and master_cache.exists():
        master_files = {f.relative_to(master_cache) for f in master_cache.rglob("*") if f.is_file()}
        new_files = []
        for f in worker_cache.rglob("*"):
            if not f.is_file():
                continue
            rel = f.relative_to(worker_cache)
            if any(kw in str(rel).lower() for kw in skip_keywords):
                continue
            if rel not in master_files:
                dest = master_cache / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    shutil.copy2(f, dest)
                    new_files.append(f"{rel} ({f.stat().st_size/1024:.1f}KB)")
                except Exception:
                    pass
        if new_files:
            print(f"[Worker-{worker_id}] 同步了 {len(new_files)} 个新缓存文件到 master:")
            for nf in new_files:
                print(f"  - {nf}")

    shutil.rmtree(worker_dir, ignore_errors=True)


def _finish_flow_status(status: AccountStatus, worker_id: int, *, mode: str):
    """统一处理登录/注册流程返回状态，成功时返回 None 继续后续 cookie 提取。"""
    if status is AccountStatus.SUCCESS:
        return None

    status_messages = {
        AccountStatus.RATE_LIMITED: "[ERROR] IP 被风控",
        AccountStatus.AUTH_FAILED: "[ERROR] 平台认证失败，停止当前账号",
        AccountStatus.PROXY_FAILED: "代理连接失败，准备重试新代理",
        AccountStatus.IMAP_AUTH_FAILED: "IMAP 认证失败，停止后续流程",
        AccountStatus.EMAIL_VERIFICATION_REQUIRED: "已到邮箱验证码页，按配置不读取邮件",
        AccountStatus.ALREADY_REGISTERED: "账号已注册，请使用 login 模式",
        AccountStatus.NEED_REGISTER: "账号未注册，请使用 register 模式",
        AccountStatus.FAILED: f"{'注册' if mode == 'register' else '登录'}失败",
    }
    message = status_messages.get(status, status_messages[AccountStatus.FAILED])
    print(f"[Worker-{worker_id}] {message}")
    return status


def register_account(base_dir: Path, email_addr: str, email_password: str, config: dict, worker_id: int = 0) -> AutomationResult:
    output_file  = config["output_file"]
    headless     = config.get("headless", False)
    proxy_config = config.get("proxy", {})
    proxy_enabled = proxy_config.get("enabled", False)
    raw_mode = str(config["mode"])
    mode = raw_mode.strip().lower()

    if mode not in ("register", "login"):
        raise ValueError("配置 mode 只支持 login/register")

    print(f"\n{'='*60}")
    print(f"[Worker-{worker_id}] 开始处理: {email_addr}")
    if proxy_enabled:
        proxy_type = str(proxy_config.get("mode") or proxy_config.get("profile") or "unknown").strip().lower()
        proxy_labels = {
            "static": "固定(static)",
            "fixed": "固定(fixed)",
            "rotating_single_ip": "固定池(rotating_single_ip)",
            "dynamic": "动态(dynamic)",
        }
        print(f"[Worker-{worker_id}] 代理: 已启用 | 类型: {proxy_labels.get(proxy_type, proxy_type)}")
    else:
        print(f"[Worker-{worker_id}] 代理: 未启用 | 类型: 直连(direct)")
    print(f"[Worker-{worker_id}] 模式: {mode}")
    print(f"{'='*60}")

    browser = None
    proxy_runtime = None
    pool_manager = None
    lease_id = None
    automation_result = AutomationResult.from_status(AccountStatus.FAILED)

    get_logger_manager(base_dir=base_dir / "logs")

    try:
        with sync_playwright() as p:
            fingerprint_mode = str((config.get("fingerprint") or {}).get("mode") or "native").strip().lower()
            fingerprint = generate_fingerprint(use_real_profile=False, mode=fingerprint_mode)
            print(f"[Worker-{worker_id}] 指纹: {describe_fingerprint(fingerprint)}")

            proxy_settings = None
            if proxy_enabled:
                runtime_proxy, pool_manager, lease_id = bind_local_rotating_proxy(base_dir, proxy_config)
                if str(proxy_config.get("mode") or "").strip().lower() == "rotating_single_ip" and not lease_id:
                    print(f"[Worker-{worker_id}] 固定池没有可用条目，停止当前账号并重试")
                    automation_result = AutomationResult.from_status(AccountStatus.PROXY_FAILED)
                    return automation_result
                if lease_id:
                    static = runtime_proxy.get("static") or {}
                    print(f"[Worker-{worker_id}] 固定池条目: {static.get('host')}:{static.get('port')}")
                runtime_config = {**config, "proxy": runtime_proxy}
                proxy_runtime = create_proxy_runtime(
                    runtime_config,
                    max_attempts=_safe_int(proxy_config.get("max_attempts", 5), 5),
                    require_exit_ip=True,
                    logger=make_proxy_logger(f"[Worker-{worker_id}]"),
                )
                proxy_settings = build_proxy_launch_config(proxy_runtime)
                if proxy_settings:
                    print(f"[Worker-{worker_id}] 使用代理运行时: {describe_proxy_runtime(proxy_runtime)}")
                else:
                    print(f"[Worker-{worker_id}] 代理初始化失败，停止当前账号并重试")
                    stop_managed_proxy_runtime(proxy_runtime)
                    proxy_runtime = None
                    automation_result = AutomationResult.from_status(AccountStatus.PROXY_FAILED)
                    return automation_result

            browser_mode_label = "native 真实身份" if is_native_fingerprint(fingerprint) else "spoofed 伪装指纹"
            print(f"[Worker-{worker_id}] 浏览器配置: {mode}模式（{browser_mode_label}）")
            browser, context, page = build_stealth_context(p, fingerprint, proxy_settings, headless)

            # ── 修复: 初始化缓存（之前只在 warmup_cache 里初始化，register_account 里从未调用）
            if config.get("cache", {}).get("enabled", False):
                init_cache_manager(CACHE_DIR)
                # 主流程 Chrome 使用临时 user-data-dir，复制 master 磁盘缓存不会生效。
                # 实际生效的是下面的应用层路由缓存；page.route 会打开 CDP Fetch。
                page.route("**/*", handle_cache_route)
                page.on("response", track_cache_response)
                print(f"[Worker-{worker_id}] 缓存已启用")

            try:
                print(f"\n[Worker-{worker_id}] 模式: {'注册' if mode == 'register' else '登录'}")
                automation_result = build_driver(mode).run(
                    page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT
                )
                flow_result = _finish_flow_status(automation_result.status, worker_id, mode=mode)
                if flow_result is not None:
                    automation_result = replace(
                        automation_result,
                        status=flow_result,
                        error_code=flow_result.value,
                    )
                    return automation_result

                # 访问 dashboard
                print(f"\n[Worker-{worker_id}] 访问 dashboard...")
                dashboard_url = "https://www.binance.com/zh-CN/my/dashboard"
                dashboard_loaded = False
                for attempt in range(3):
                    try:
                        page.goto(dashboard_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                        page.wait_for_timeout(random.randint(2000, 3000))
                        if is_dashboard_url(page.url):
                            dashboard_loaded = True
                            break
                        print(f"[Worker-{worker_id}] dashboard 未完全加载，重试 ({attempt+1}/3)")
                    except Exception as e:
                        print(f"[Worker-{worker_id}] 访问 dashboard 失败 ({attempt+1}/3): {e}")
                        page.wait_for_timeout(1000)

                if not dashboard_loaded:
                    try:
                        page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    page.wait_for_timeout(2000)

                # 提取 cookie
                print(f"\n[Worker-{worker_id}] 提取 cookie 和 csrftoken...")
                credentials = export_credentials(page)
                automation_result = replace(automation_result, credentials=credentials)
                cookie_string, csrftoken = credentials.cookie, credentials.csrftoken
                if cookie_string and csrftoken:
                    account_data = {
                        "name":             f"账号_{email_addr.split('@')[0]}",
                        "email":            f"{email_addr}----{email_password}",
                        "password":         email_password,
                        "cookie":           cookie_string,
                        "csrftoken":        csrftoken,
                        "credential_exported_at": credentials.credential_exported_at,
                        "enabled":          True,
                        "avatar_changed":   False,
                        "nickname_changed": False,
                        "display_name":     "",
                        "username":         "",
                        "mail_api_url":     "https://wrpifa-com.netlify.app/",
                    }
                    save_registered_account(base_dir, output_file, account_data)
                    creator_cfg = config.get("creator_api", {})
                    if creator_cfg.get("enabled"):
                        slot_token = acquire_creator_api_slot(base_dir, config)
                        if slot_token is None:
                            print(f"[Worker-{worker_id}] 创作者 API 已达到本次提取配额，跳过")
                        else:
                            print(f"\n[Worker-{worker_id}] 提取创作者中心 API...")
                            try:
                                creator_profile = extract_creator_api(page, base_dir, page_timeout=PAGE_TIMEOUT)
                                account_data.update(api_metadata(creator_profile))
                                save_registered_account(base_dir, output_file, account_data)
                            except Exception as exc:
                                release_creator_api_slot(base_dir, config, slot_token, completed=False)
                                print(f"[Worker-{worker_id}] 创作者 API 提取失败，Cookie 已保存: {exc}")
                            else:
                                release_creator_api_slot(base_dir, config, slot_token, completed=True)
                                print(f"[Worker-{worker_id}] API 提取成功（已隐藏密钥）")
                    print(f"\n[Worker-{worker_id}] 处理成功: {email_addr}")
                    return automation_result

                print(f"[Worker-{worker_id}] 未能获取有效的 cookie 或 csrftoken")
                automation_result = AutomationResult.from_status(AccountStatus.FAILED, message="未能获取有效的 cookie 或 csrftoken")
                return automation_result

            except Exception as e:
                print(f"[Worker-{worker_id}] 处理过程出错: {e}")
                automation_result = AutomationResult.from_status(AccountStatus.FAILED, message=str(e))
                return automation_result
            finally:
                cleanup_subprocess_browser(browser)
                stop_managed_proxy_runtime(proxy_runtime)
                proxy_runtime = None
    finally:
        if pool_manager and lease_id:
            pool_manager.release(lease_id, result_status=automation_result.status.value)
