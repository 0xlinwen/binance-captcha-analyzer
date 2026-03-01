import hashlib
import random
import shutil
from pathlib import Path

from playwright.sync_api import sync_playwright

from .flows import login_with_url_state, register_with_url_state
from .storage import save_registered_account
from .traffic_monitor import mark_cached_url
from .local_cache import init_cache_manager, get_cache_manager
from .fingerprint import generate_fingerprint

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
            '--disable-dev-shm-usage',
            '--disable-infobars',
            '--window-size=1920,1080',
            '--start-maximized',
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
            viewport={'width': 1920, 'height': 1080},
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            locale='zh-CN',
            timezone_id='Asia/Shanghai',
        )

        try:
            page = context.new_page()

            # 隐藏自动化特征
            page.add_init_script('''
                Object.defineProperty(navigator, 'webdriver', {
                    get: () => undefined
                });

                window.chrome = {
                    runtime: {},
                    loadTimes: function() {},
                    csi: function() {},
                    app: {}
                };

                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({ state: Notification.permission }) :
                        originalQuery(parameters)
                );
            ''')

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
    mode = config.get("mode", "login")  # register / login (不再支持 auto)

    print(f"\n{'='*60}")
    print(f"[Worker-{worker_id}] 开始处理: {email_addr}")
    if proxy_enabled:
        print(f"[Worker-{worker_id}] 代理: {proxy_config.get('server', 'N/A')}")
    print(f"[Worker-{worker_id}] 模式: {mode}")
    print(f"{'='*60}")

    with sync_playwright() as p:
        # 根据模式选择不同的浏览器配置
        is_register_mode = mode == "register"

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

        if is_register_mode:
            # 注册模式：使用完整的反检测配置 + 随机指纹
            fingerprint = generate_fingerprint()
            print(f"[Worker-{worker_id}] 指纹: {fingerprint['user_agent'][-30:]} | {fingerprint['timezone_id']} | {fingerprint['webgl_renderer'][-20:]}")

            browser = p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                    '--disable-dev-shm-usage',
                    '--disable-infobars',
                    f'--window-size={fingerprint["viewport"]["width"]},{fingerprint["viewport"]["height"]}',
                    '--start-maximized',
                ]
            )
            context = browser.new_context(
                viewport=fingerprint['viewport'],
                user_agent=fingerprint['user_agent'],
                locale=fingerprint['locale'],
                timezone_id=fingerprint['timezone_id'],
                proxy=proxy_settings,
            )
            page = context.new_page()

            # 完整的反检测脚本（包含随机 WebGL 伪造）
            webgl_vendor = fingerprint['webgl_vendor']
            webgl_renderer = fingerprint['webgl_renderer']
            page.add_init_script(f'''
                Object.defineProperty(navigator, 'webdriver', {{
                    get: () => undefined
                }});

                window.chrome = {{
                    runtime: {{}},
                    loadTimes: function() {{}},
                    csi: function() {{}},
                    app: {{}}
                }};

                const originalQuery = window.navigator.permissions.query;
                window.navigator.permissions.query = (parameters) => (
                    parameters.name === 'notifications' ?
                        Promise.resolve({{ state: Notification.permission }}) :
                        originalQuery(parameters)
                );

                // 伪造 WebGL 信息
                const getParameterProxyHandler = {{
                    apply: function(target, thisArg, args) {{
                        const param = args[0];
                        // UNMASKED_VENDOR_WEBGL
                        if (param === 37445) {{
                            return '{webgl_vendor}';
                        }}
                        // UNMASKED_RENDERER_WEBGL
                        if (param === 37446) {{
                            return '{webgl_renderer}';
                        }}
                        return Reflect.apply(target, thisArg, args);
                    }}
                }};

                const originalGetContext = HTMLCanvasElement.prototype.getContext;
                HTMLCanvasElement.prototype.getContext = function(type, attrs) {{
                    const context = originalGetContext.call(this, type, attrs);
                    if (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl') {{
                        if (context && context.getParameter) {{
                            context.getParameter = new Proxy(context.getParameter, getParameterProxyHandler);
                        }}
                    }}
                    return context;
                }};
            ''')
        else:
            # 登录模式：简化配置 + 随机指纹
            fingerprint = generate_fingerprint()
            print(f"[Worker-{worker_id}] 指纹: {fingerprint['user_agent'][-30:]} | {fingerprint['timezone_id']}")

            browser = p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--no-sandbox',
                ],
                proxy=proxy_settings,
            )
            context = browser.new_context(
                viewport=fingerprint['viewport'],
                user_agent=fingerprint['user_agent'],
                locale=fingerprint['locale'],
                timezone_id=fingerprint['timezone_id'],
            )
            page = context.new_page()

        try:
            if mode == "register":
                # 注册模式
                print(f"\n[Worker-{worker_id}] 模式: 注册")
                reg_result = register_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
                if reg_result == "rate_limited":
                    print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                    return False
                if reg_result == "already_registered":
                    print(f"[Worker-{worker_id}] 账号已注册，请使用 login 模式")
                    return "already_registered"
                if not reg_result:
                    print(f"[Worker-{worker_id}] 注册失败")
                    return False
            else:
                # 登录模式
                result = login_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
                if result == "rate_limited":
                    print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                    return False
                if result == "need_register":
                    print(f"[Worker-{worker_id}] 账号未注册，请使用 register 模式")
                    return "need_register"
                if not result:
                    print(f"[Worker-{worker_id}] 登录失败")
                    return False

            print(f"\n[Worker-{worker_id}] 访问 dashboard...")
            dashboard_url = "https://www.binance.com/zh-CN/my/dashboard"
            dashboard_loaded = False
            for attempt in range(3):
                try:
                    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    page.wait_for_timeout(random.randint(2000, 3000))
                    # 检查是否真正加载了 dashboard 页面
                    current_url = page.url
                    if "/my/dashboard" in current_url or "/my/" in current_url:
                        dashboard_loaded = True
                        break
                    print(f"[Worker-{worker_id}] dashboard 页面未完全加载，重试 ({attempt + 1}/3)")
                except Exception as e:
                    print(f"[Worker-{worker_id}] 访问 dashboard 失败 ({attempt + 1}/3): {e}")
                    page.wait_for_timeout(1000)

            if not dashboard_loaded:
                # 最后尝试等待页面稳定
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)

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
            if browser:
                browser.close()
