import hashlib
import os
import random
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from .flows import login_with_url_state, register_with_url_state
from .storage import save_registered_account
from .traffic_monitor import enable_traffic_monitor, print_traffic_summary, reset_traffic_monitor, mark_cached_url
from .local_cache import init_cache_manager, get_cache_manager

PAGE_TIMEOUT = 60000

# 缓存目录
CACHE_DIR = Path(__file__).resolve().parents[2] / ".browser_cache"
# 主缓存模板
MASTER_CACHE_DIR = CACHE_DIR / "master"

# 可缓存的静态资源域名
CACHEABLE_DOMAINS = [
    "bin.bnbstatic.com/static",
    "public.bnbstatic.com/unpkg",
]


def _handle_route(route, request):
    """路由处理函数：本地缓存"""
    url = request.url
    resource_type = request.resource_type

    # 检查本地缓存
    cache_manager = get_cache_manager()
    if cache_manager:
        cached = cache_manager.get_cached(url, resource_type)
        if cached:
            # 标记为缓存命中
            mark_cached_url(url)
            # 从本地缓存返回
            route.fulfill(
                status=200,
                headers=cached["headers"],
                body=cached["body"],
            )
            return

    # 继续请求
    route.continue_()


def _on_response(response):
    """响应回调：保存到本地缓存"""
    try:
        request = response.request
        url = request.url
        resource_type = request.resource_type

        # 只缓存成功的响应
        if response.status != 200:
            return

        # 检查是否可缓存
        url_lower = url.lower()
        if not any(domain in url_lower for domain in CACHEABLE_DOMAINS):
            return

        # 缓存 script、stylesheet、fetch 类型
        if resource_type not in ("script", "stylesheet", "fetch"):
            return

        # 获取响应体并缓存
        cache_manager = get_cache_manager()
        if cache_manager:
            try:
                body = response.body()
                headers = dict(response.headers)
                cache_manager.save_to_cache(url, resource_type, body, headers)
            except Exception:
                pass
    except Exception:
        pass


def warmup_cache(proxy_config=None, headless=True):
    """预热缓存：访问 Binance 页面，下载静态资源"""
    print("预热浏览器缓存...")
    MASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    # 初始化本地缓存管理器
    init_cache_manager(CACHE_DIR)

    with sync_playwright() as p:
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
            '--disk-cache-size=104857600',
        ]

        proxy_settings = None
        if proxy_config and proxy_config.get("enabled") and proxy_config.get("server"):
            server = proxy_config["server"]
            if not server.startswith(("http://", "https://", "socks")):
                server = f"http://{server}"
            proxy_settings = {"server": server}
            if proxy_config.get("username"):
                proxy_settings["username"] = proxy_config["username"]
            if proxy_config.get("password"):
                proxy_settings["password"] = proxy_config["password"]

        context = p.chromium.launch_persistent_context(
            user_data_dir=str(MASTER_CACHE_DIR),
            headless=headless,
            args=launch_args,
            proxy=proxy_settings,
            viewport={"width": 1280, "height": 800},
        )

        try:
            page = context.new_page()

            # 启用资源拦截
            page.route("**/*", _handle_route)
            # 启用响应缓存
            page.on("response", _on_response)

            warmup_urls = [
                "https://accounts.binance.com/zh-CN/login",
                "https://www.binance.com/zh-CN/register",
            ]

            for url in warmup_urls:
                print(f"访问: {url}")
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    page.wait_for_timeout(5000)
                except Exception as e:
                    print(f"  加载异常: {e}，继续...")

            context.clear_cookies()
            print("缓存预热完成")
        finally:
            context.close()


def _get_worker_cache_dir(worker_id: int) -> Path:
    """获取 worker 缓存目录路径"""
    return CACHE_DIR / f"worker_{worker_id}"


def _init_worker_cache(worker_id: int) -> Path:
    """初始化 worker 缓存：从 master 复制"""
    worker_dir = _get_worker_cache_dir(worker_id)

    # 删除旧的 worker 目录
    if worker_dir.exists():
        shutil.rmtree(worker_dir, ignore_errors=True)

    # 从 master 复制
    if MASTER_CACHE_DIR.exists():
        print(f"[Worker-{worker_id}] 从 master 缓存复制...")
        shutil.copytree(MASTER_CACHE_DIR, worker_dir, dirs_exist_ok=True)
    else:
        worker_dir.mkdir(parents=True, exist_ok=True)

    return worker_dir


def _sync_new_cache_to_master(worker_id: int):
    """同步 worker 中新增的缓存文件到 master，然后删除 worker 目录"""
    worker_dir = _get_worker_cache_dir(worker_id)

    if not worker_dir.exists() or not MASTER_CACHE_DIR.exists():
        return

    # Chromium 缓存目录
    worker_cache = worker_dir / "Default" / "Cache" / "Cache_Data"
    master_cache = MASTER_CACHE_DIR / "Default" / "Cache" / "Cache_Data"

    if not worker_cache.exists():
        worker_cache = worker_dir / "Default" / "Cache"
    if not master_cache.exists():
        master_cache = MASTER_CACHE_DIR / "Default" / "Cache"

    # 不同步验证码相关文件
    skip_keywords = ["captcha", "puzzle", "slider", "bncaptcha", "geetest", "bnc-cap", "s3.amazonaws"]

    if worker_cache.exists() and master_cache.exists():
        master_files = set()
        for f in master_cache.rglob("*"):
            if f.is_file():
                master_files.add(f.relative_to(master_cache))

        new_count = 0
        new_files = []
        for f in worker_cache.rglob("*"):
            if f.is_file():
                rel_path = f.relative_to(worker_cache)
                filename_lower = str(rel_path).lower()
                if any(kw in filename_lower for kw in skip_keywords):
                    continue
                if rel_path not in master_files:
                    dest = master_cache / rel_path
                    dest.parent.mkdir(parents=True, exist_ok=True)
                    try:
                        shutil.copy2(f, dest)
                        new_count += 1
                        size_kb = f.stat().st_size / 1024
                        new_files.append(f"{rel_path} ({size_kb:.1f}KB)")
                    except Exception:
                        pass

        if new_count > 0:
            print(f"[Worker-{worker_id}] 同步了 {new_count} 个新缓存文件到 master:")
            for nf in new_files:
                print(f"  - {nf}")

    # 删除 worker 目录
    shutil.rmtree(worker_dir, ignore_errors=True)


def extract_cookies_and_csrf(page):
    """从 page 的 context 提取 cookies"""
    context = page.context
    cookies = context.cookies()
    cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies if "binance" in c.get("domain", "")])

    cookie_map = {c["name"]: c["value"] for c in cookies if "binance" in c.get("domain", "")}
    csrftoken = None
    if "cr00" in cookie_map:
        csrftoken = hashlib.md5(cookie_map["cr00"].encode()).hexdigest()
        print(f"cr00: {cookie_map['cr00'][:20]}...")
        print(f"csrftoken (md5): {csrftoken}")
    else:
        csrftoken = cookie_map.get("csrftoken")
        if csrftoken:
            print(f"csrftoken (cookie): {csrftoken}")
        else:
            print("警告: 未找到 cr00，无法计算 csrftoken")

    return cookie_string, csrftoken


def register_account(base_dir: Path, email_addr: str, email_password: str, config: dict, worker_id: int = 0):
    """注册/登录账号"""
    output_file = config["output_file"]
    headless = config.get("headless", False)
    proxy_config = config.get("proxy", {})
    proxy_enabled = proxy_config.get("enabled", False)
    cache_enabled = config.get("cache", {}).get("enabled", True)

    print(f"\n{'='*60}")
    print(f"[Worker-{worker_id}] 开始处理: {email_addr}")
    if proxy_enabled:
        print(f"[Worker-{worker_id}] 代理: {proxy_config.get('server', 'N/A')}")
    if not cache_enabled:
        print(f"[Worker-{worker_id}] 本地缓存: 已禁用")
    print(f"{'='*60}")

    # 初始化本地缓存管理器
    if cache_enabled:
        init_cache_manager(CACHE_DIR)

    # 初始化 worker 缓存（从 master 复制）
    if cache_enabled:
        cache_dir = _init_worker_cache(worker_id)

    with sync_playwright() as p:
        launch_args = [
            '--disable-blink-features=AutomationControlled',
            '--no-sandbox',
        ]

        proxy_settings = None
        if proxy_enabled and proxy_config.get("server"):
            server = proxy_config["server"]
            if not server.startswith(("http://", "https://", "socks")):
                server = f"http://{server}"
            proxy_settings = {"server": server}
            if proxy_config.get("username"):
                proxy_settings["username"] = proxy_config["username"]
            if proxy_config.get("password"):
                proxy_settings["password"] = proxy_config["password"]
            print(f"[Worker-{worker_id}] 使用代理: {server}")

        if cache_enabled:
            launch_args.append('--disk-cache-size=104857600')
            context = p.chromium.launch_persistent_context(
                user_data_dir=str(cache_dir),
                headless=headless,
                args=launch_args,
                proxy=proxy_settings,
                viewport={"width": 1280, "height": 800},
            )
            browser = None
        else:
            browser = p.chromium.launch(
                headless=headless,
                args=launch_args,
                proxy=proxy_settings,
            )
            context = browser.new_context(viewport={"width": 1280, "height": 800})

        try:
            context.clear_cookies()
            page = context.new_page()

            if cache_enabled:
                # 启用资源拦截（屏蔽 + 本地缓存）
                page.route("**/*", _handle_route)
                # 启用响应缓存
                page.on("response", _on_response)

            # 启用流量监控
            reset_traffic_monitor()
            enable_traffic_monitor(page)

            result = login_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
            if result == "rate_limited":
                print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                return False

            if result == "need_register":
                print(f"\n[Worker-{worker_id}] 检测到账号未注册，启动注册流程...")
                reg_result = register_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
                if reg_result == "rate_limited":
                    print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                    return False
                if not reg_result:
                    print(f"[Worker-{worker_id}] 注册失败")
                    return False
            elif not result:
                print(f"[Worker-{worker_id}] 登录失败")
                return False

            print(f"\n[Worker-{worker_id}] 访问 dashboard...")
            try:
                page.goto("https://www.binance.com/zh-CN/my/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(random.randint(2500, 3500))
            except Exception as e:
                print(f"[Worker-{worker_id}] 访问 dashboard 失败: {e}")

            print(f"\n[Worker-{worker_id}] 提取 cookie 和 csrftoken...")
            cookie_string, csrftoken = extract_cookies_and_csrf(page)
            if cookie_string and csrftoken:
                account_data = {
                    "name": f"账号_{email_addr.split('@')[0]}",
                    "email": email_addr,
                    "cookie": cookie_string,
                    "csrftoken": csrftoken,
                    "enabled": True,
                }
                save_registered_account(base_dir, output_file, account_data)
                print(f"\n[Worker-{worker_id}] 处理成功: {email_addr}")
                return True

            print(f"[Worker-{worker_id}] 未能获取有效的 cookie 或 csrftoken")
            return False
        except Exception as e:
            print(f"[Worker-{worker_id}] 处理过程出错: {e}")
            return False
        finally:
            # 打印流量统计
            print_traffic_summary()
            # 打印本地缓存统计
            if cache_enabled:
                cache_manager = get_cache_manager()
                if cache_manager:
                    cache_manager.print_stats()

            context.close()
            if browser:
                browser.close()
            # 同步新增缓存到 master
            if cache_enabled:
                _sync_new_cache_to_master(worker_id)
