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


def _build_init_script(fingerprint: dict) -> str:
    """
    构建完整的反检测初始化脚本
    针对服务器无头环境（无真实 GPU）做全面伪造

    修复记录：
    1. WebGL 伪造：增加 Worker 线程拦截，解决 OffscreenCanvas 跨线程失效
    2. Permissions：移除 permissions=[] 改为 JS 拦截，避免 state=denied 暴露
    3. chrome.webstore：补全定义，避免 constructor 访问报错
    4. languages：两个模式都注入脚本，确保多语言生效
    5. Canvas 噪声：每次随机微扰，避免固定 hash 被关联追踪
    """
    webgl_vendor = fingerprint['webgl_vendor']
    webgl_renderer = fingerprint['webgl_renderer']
    platform = fingerprint['platform']
    hardware_concurrency = fingerprint['hardware_concurrency']
    device_memory = fingerprint['device_memory']
    languages = fingerprint['languages']
    screen_width = fingerprint['screen_width']
    screen_height = fingerprint['screen_height']
    avail_width = fingerprint['avail_width']
    avail_height = fingerprint['avail_height']
    color_depth = fingerprint['color_depth']
    pixel_depth = fingerprint['pixel_depth']
    device_pixel_ratio = fingerprint['device_pixel_ratio']
    languages_json = str(languages).replace("'", '"')

    # 每次生成随机 canvas 噪声种子，避免固定 hash 被追踪
    canvas_noise = random.uniform(0.0001, 0.0009)

    return f'''
(function() {{

// ── 1. 隐藏 webdriver ────────────────────────────────────────────
Object.defineProperty(navigator, 'webdriver', {{ get: () => undefined }});

// ── 2. 平台 ──────────────────────────────────────────────────────
Object.defineProperty(navigator, 'platform', {{ get: () => '{platform}' }});

// ── 3. 语言（多语言，避免单一语言被识别）────────────────────────
Object.defineProperty(navigator, 'language',  {{ get: () => '{languages[0]}' }});
Object.defineProperty(navigator, 'languages', {{ get: () => {languages_json} }});

// ── 4. 硬件信息 ──────────────────────────────────────────────────
Object.defineProperty(navigator, 'hardwareConcurrency', {{ get: () => {hardware_concurrency} }});
Object.defineProperty(navigator, 'deviceMemory',        {{ get: () => {device_memory} }});

// ── 5. 屏幕信息（修复服务器默认 1280×720 + DPR=1）──────────────
Object.defineProperty(screen, 'width',       {{ get: () => {screen_width} }});
Object.defineProperty(screen, 'height',      {{ get: () => {screen_height} }});
Object.defineProperty(screen, 'availWidth',  {{ get: () => {avail_width} }});
Object.defineProperty(screen, 'availHeight', {{ get: () => {avail_height} }});
Object.defineProperty(screen, 'colorDepth',  {{ get: () => {color_depth} }});
Object.defineProperty(screen, 'pixelDepth',  {{ get: () => {pixel_depth} }});
Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {device_pixel_ratio} }});

// ── 6. chrome 对象（修复 runtime/webstore undefined 报错）────────
// 修复：补全 webstore，避免 chrome.webstore.constructor 访问报错
window.chrome = {{
    app: {{
        isInstalled: false,
        InstallState: {{
            DISABLED: 'disabled',
            INSTALLED: 'installed',
            NOT_INSTALLED: 'not_installed'
        }},
        RunningState: {{
            CANNOT_RUN: 'cannot_run',
            READY_TO_RUN: 'ready_to_run',
            RUNNING: 'running'
        }},
        getDetails: function() {{ return null; }},
        getIsInstalled: function() {{ return false; }},
        runningState: function() {{ return 'cannot_run'; }},
    }},
    // 修复：webstore 必须存在且有 constructor，否则检测报错
    webstore: {{
        onInstallStageChanged: {{}},
        onDownloadProgress: {{}},
        install: function() {{ return Promise.resolve(); }},
    }},
    runtime: {{
        PlatformOs: {{
            MAC: 'mac', WIN: 'win', ANDROID: 'android',
            CROS: 'cros', LINUX: 'linux', OPENBSD: 'openbsd'
        }},
        PlatformArch: {{
            ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64'
        }},
        PlatformNaclArch: {{
            ARM: 'arm', X86_32: 'x86-32', X86_64: 'x86-64'
        }},
        RequestUpdateCheckStatus: {{
            THROTTLED: 'throttled', NO_UPDATE: 'no_update', UPDATE_AVAILABLE: 'update_available'
        }},
        OnInstalledReason: {{
            INSTALL: 'install', UPDATE: 'update',
            CHROME_UPDATE: 'chrome_update', SHARED_MODULE_UPDATE: 'shared_module_update'
        }},
        OnRestartRequiredReason: {{
            APP_UPDATE: 'app_update', OS_UPDATE: 'os_update', PERIODIC: 'periodic'
        }},
        connect: function() {{
            return {{
                postMessage: function() {{}},
                disconnect: function() {{}},
                onMessage: {{ addListener: function() {{}}, removeListener: function() {{}} }},
                onDisconnect: {{ addListener: function() {{}}, removeListener: function() {{}} }}
            }};
        }},
        sendMessage: function() {{}},
        id: undefined,
        lastError: undefined,
    }},
    loadTimes: function() {{
        return {{
            requestTime: Date.now() / 1000 - Math.random() * 2,
            startLoadTime: Date.now() / 1000 - Math.random() * 1.5,
            commitLoadTime: Date.now() / 1000 - Math.random(),
            finishDocumentLoadTime: Date.now() / 1000 - Math.random() * 0.5,
            finishLoadTime: Date.now() / 1000,
            firstPaintTime: Date.now() / 1000 - Math.random() * 0.3,
            firstPaintAfterLoadTime: 0,
            navigationType: 'Other',
            wasFetchedViaSpdy: true,
            wasNpnNegotiated: true,
            npnNegotiatedProtocol: 'h2',
            wasAlternateProtocolAvailable: false,
            connectionInfo: 'h2'
        }};
    }},
    csi: function() {{
        return {{
            startE: Date.now(),
            onloadT: Date.now() + Math.floor(Math.random() * 500 + 200),
            pageT: Math.random() * 5000 + 1000,
            tran: 15
        }};
    }},
}};

// ── 7. Permissions API ───────────────────────────────────────────
// 修复：不再依赖 permissions=[] 参数（会导致 state=denied 暴露）
// 改为 JS 拦截，强制返回 'prompt'（真实浏览器首次访问的状态）
const _originalPermissionsQuery = window.navigator.permissions.query.bind(navigator.permissions);
window.navigator.permissions.query = (parameters) => {{
    if (parameters.name === 'notifications') {{
        return Promise.resolve({{ state: 'prompt', onchange: null }});
    }}
    if (parameters.name === 'clipboard-read' || parameters.name === 'clipboard-write') {{
        return Promise.resolve({{ state: 'prompt', onchange: null }});
    }}
    return _originalPermissionsQuery(parameters);
}};

// ── 8. WebGL 完整伪造 ────────────────────────────────────────────
// 修复：增加 Worker 线程拦截脚本注入，解决 OffscreenCanvas 跨线程失效问题
// 同时修复 getExtension proxy 返回的常量值问题
(function() {{
    const VENDOR   = '{webgl_vendor}';
    const RENDERER = '{webgl_renderer}';

    // UNMASKED_VENDOR_WEBGL = 37445, UNMASKED_RENDERER_WEBGL = 37446
    const WEBGL_PARAMS = {{
        37445: VENDOR,
        37446: RENDERER,
    }};

    function patchGetParameter(original) {{
        return new Proxy(original, {{
            apply: function(target, thisArg, args) {{
                const param = args[0];
                if (param === 37445) return VENDOR;
                if (param === 37446) return RENDERER;
                return Reflect.apply(target, thisArg, args);
            }}
        }});
    }}

    function patchGetExtension(original) {{
        return function(name) {{
            const ext = original.call(this, name);
            if (name === 'WEBGL_debug_renderer_info' && ext) {{
                // 修复：直接覆盖常量属性，而非用 Proxy（更可靠）
                try {{
                    Object.defineProperty(ext, 'UNMASKED_VENDOR_WEBGL',   {{ get: () => 37445 }});
                    Object.defineProperty(ext, 'UNMASKED_RENDERER_WEBGL', {{ get: () => 37446 }});
                }} catch(e) {{}}
            }}
            return ext;
        }};
    }}

    function patchContext(ctx) {{
        if (!ctx || ctx.__bnPatch) return;
        ctx.__bnPatch = true;
        ctx.getParameter  = patchGetParameter(ctx.getParameter.bind(ctx));
        ctx.getExtension  = patchGetExtension(ctx.getExtension.bind(ctx));
    }}

    // 主线程：HTMLCanvasElement
    const _origGetContext = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {{
        const ctx = _origGetContext.call(this, type, attrs);
        if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {{
            patchContext(ctx);
        }}
        return ctx;
    }};

    // 主线程：OffscreenCanvas
    if (typeof OffscreenCanvas !== 'undefined') {{
        const _origOSC = OffscreenCanvas.prototype.getContext;
        OffscreenCanvas.prototype.getContext = function(type, attrs) {{
            const ctx = _origOSC.call(this, type, attrs);
            if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {{
                patchContext(ctx);
            }}
            return ctx;
        }};
    }}

    // 修复：拦截 Worker 创建，向 Worker 注入相同的 WebGL 伪造脚本
    // 检测工具有时在 Worker 里用 OffscreenCanvas 检测真实 GPU
    const WORKER_PATCH_SCRIPT = `
        const _VENDOR = '${{VENDOR}}';
        const _RENDERER = '${{RENDERER}}';
        if (typeof OffscreenCanvas !== 'undefined') {{
            const _orig = OffscreenCanvas.prototype.getContext;
            OffscreenCanvas.prototype.getContext = function(type, attrs) {{
                const ctx = _orig.call(this, type, attrs);
                if (ctx && (type === 'webgl' || type === 'webgl2')) {{
                    if (!ctx.__bnPatch) {{
                        ctx.__bnPatch = true;
                        const _gp = ctx.getParameter.bind(ctx);
                        ctx.getParameter = function(p) {{
                            if (p === 37445) return _VENDOR;
                            if (p === 37446) return _RENDERER;
                            return _gp(p);
                        }};
                    }}
                }}
                return ctx;
            }};
        }}
    `;

    const _origWorker = window.Worker;
    window.Worker = function(scriptURL, options) {{
        // 只对 blob: URL 注入（检测脚本通常用 blob worker）
        if (typeof scriptURL === 'string' && scriptURL.startsWith('blob:')) {{
            try {{
                const blob = new Blob([WORKER_PATCH_SCRIPT], {{ type: 'application/javascript' }});
                const patchURL = URL.createObjectURL(blob);
                const combinedBlob = new Blob(
                    [`importScripts('${{patchURL}}');`],
                    {{ type: 'application/javascript' }}
                );
                scriptURL = URL.createObjectURL(combinedBlob);
            }} catch(e) {{}}
        }}
        return new _origWorker(scriptURL, options);
    }};
    window.Worker.prototype = _origWorker.prototype;

}})();

// ── 9. Canvas 噪声（随机微扰，避免固定 hash 被关联追踪）────────
// 修复：每次启动使用不同噪声种子，防止同一指纹被跨 IP 追踪
(function() {{
    const _noise = {canvas_noise};
    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        const ctx2d = this.getContext('2d');
        if (ctx2d) {{
            const imageData = ctx2d.getImageData(0, 0, this.width || 1, this.height || 1);
            if (imageData.data.length > 0) {{
                imageData.data[0] = (imageData.data[0] + _noise * 255) & 0xFF;
                ctx2d.putImageData(imageData, 0, 0);
            }}
        }}
        return _origToDataURL.call(this, type, quality);
    }};

    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
        const imageData = _origGetImageData.call(this, x, y, w, h);
        if (imageData.data.length > 0) {{
            imageData.data[0] = (imageData.data[0] + Math.floor(_noise * 10)) & 0xFF;
        }}
        return imageData;
    }};
}})();

// ── 10. 媒体设备（保证设备列表不为空）──────────────────────────
const _origEnumDevices = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
navigator.mediaDevices.enumerateDevices = async function() {{
    const devices = await _origEnumDevices();
    if (devices.length === 0) {{
        return [
            {{ deviceId: '', kind: 'audioinput',  label: '', groupId: '' }},
            {{ deviceId: '', kind: 'videoinput',  label: '', groupId: '' }},
            {{ deviceId: '', kind: 'audiooutput', label: '', groupId: '' }},
        ];
    }}
    return devices;
}};

// ── 11. 隐藏 Automation 属性 ─────────────────────────────────────
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

}})();
'''


def _handle_route(route, request):
    """路由处理函数：本地缓存"""
    url = request.url
    resource_type = request.resource_type

    cache_manager = get_cache_manager()
    if cache_manager:
        cached = cache_manager.get_cached(url, resource_type)
        if cached:
            mark_cached_url(url)
            route.fulfill(
                status=200,
                headers=cached["headers"],
                body=cached["body"],
            )
            return

    route.continue_()


def _on_response(response):
    """响应回调：保存到本地缓存"""
    try:
        request = response.request
        url = request.url
        resource_type = request.resource_type

        if response.status != 200:
            return

        url_lower = url.lower()
        if not any(domain in url_lower for domain in CACHEABLE_DOMAINS):
            return

        if resource_type not in ("script", "stylesheet", "fetch"):
            return

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


def _get_launch_args(screen_width: int, screen_height: int) -> list:
    """构建 Chromium 启动参数（服务器无头环境优化）"""
    return [
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-infobars',
        f'--window-size={screen_width},{screen_height}',
        '--use-gl=angle',
        '--use-angle=swiftshader-webgl',
        '--disable-gpu-sandbox',
        '--disable-gpu-process-crash-limit',
        '--disable-setuid-sandbox',
        '--disable-accelerated-2d-canvas',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-ipc-flooding-protection',
        '--force-color-profile=srgb',
        '--disk-cache-size=104857600',
    ]


def _build_context(p, fingerprint: dict, proxy_settings, headless: bool, mode: str):
    """
    统一创建浏览器 + context + 注入脚本
    修复：两个模式都注入完整 init_script，不再区分简化/完整
    修复：移除 permissions=[] 参数，避免 Notification.permission='denied'
    """
    launch_args = _get_launch_args(fingerprint['screen_width'], fingerprint['screen_height'])

    browser = p.chromium.launch(
        headless=headless,
        args=launch_args,
    )

    context = browser.new_context(
        user_agent=fingerprint['user_agent'],
        locale=fingerprint['locale'],
        timezone_id=fingerprint['timezone_id'],
        proxy=proxy_settings,
        viewport={
            "width": fingerprint['screen_width'],
            "height": fingerprint['screen_height'] - 80,
        },
        screen={
            "width": fingerprint['screen_width'],
            "height": fingerprint['screen_height'],
        },
        device_scale_factor=fingerprint['device_pixel_ratio'],
        # 修复：不传 permissions 参数，保持浏览器默认 prompt 状态
        # permissions=[] 会导致所有权限 denied，被检测工具识别
    )

    page = context.new_page()

    # 修复：两个模式统一注入完整反检测脚本
    page.add_init_script(_build_init_script(fingerprint))

    return browser, context, page


def warmup_cache(proxy_config=None, headless=True):
    """预热缓存：访问 Binance 页面，下载静态资源"""
    print("预热浏览器缓存...")
    MASTER_CACHE_DIR.mkdir(parents=True, exist_ok=True)

    init_cache_manager(CACHE_DIR)

    fingerprint = generate_fingerprint(use_real_profile=True)

    with sync_playwright() as p:
        launch_args = _get_launch_args(fingerprint['screen_width'], fingerprint['screen_height'])

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
            user_agent=fingerprint['user_agent'],
            locale=fingerprint['locale'],
            timezone_id=fingerprint['timezone_id'],
            viewport={
                "width": fingerprint['screen_width'],
                "height": fingerprint['screen_height'] - 80,
            },
            screen={
                "width": fingerprint['screen_width'],
                "height": fingerprint['screen_height'],
            },
            device_scale_factor=fingerprint['device_pixel_ratio'],
        )

        try:
            page = context.new_page()
            page.add_init_script(_build_init_script(fingerprint))
            page.route("**/*", _handle_route)
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

    shutil.rmtree(worker_dir, ignore_errors=True)


def extract_cookies_and_csrf(page):
    """从 page 的 context 提取 cookies"""
    context = page.context
    cookies = context.cookies()
    cookie_string = "; ".join([
        f"{c['name']}={c['value']}"
        for c in cookies if "binance" in c.get("domain", "")
    ])

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
    raw_mode = str(config.get("mode", "login"))
    mode = raw_mode.strip().lower()

    print(f"\n{'='*60}")
    print(f"[Worker-{worker_id}] 开始处理: {email_addr}")
    if proxy_enabled:
        print(f"[Worker-{worker_id}] 代理: {proxy_config.get('server', 'N/A')}")
    print(f"[Worker-{worker_id}] 模式: {mode}")

    if mode in ("signup", "sign_up"):
        mode = "register"
    elif mode not in ("register", "login"):
        print(f"[Worker-{worker_id}] 未知 mode={raw_mode!r}，已回退为 login")
        mode = "login"
    print(f"{'='*60}")

    browser = None

    with sync_playwright() as p:
        # 生成指纹
        fingerprint = generate_fingerprint(use_real_profile=False)
        print(
            f"[Worker-{worker_id}] 指纹: UA={fingerprint['user_agent'][-40:]} | "
            f"TZ={fingerprint['timezone_id']} | "
            f"Screen={fingerprint['screen_width']}x{fingerprint['screen_height']} | "
            f"DPR={fingerprint['device_pixel_ratio']} | "
            f"Lang={fingerprint['languages'][0]}"
        )

        # 代理配置
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

        # 修复：两个模式统一用完整反检测配置，不再区分"简化"/"完整"
        print(f"[Worker-{worker_id}] 浏览器配置: {mode}模式（完整反检测）")
        browser, context, page = _build_context(p, fingerprint, proxy_settings, headless, mode)

        try:
            if mode == "register":
                print(f"\n[Worker-{worker_id}] 模式: 注册")
                reg_result = register_with_url_state(
                    page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT
                )
                if reg_result == "rate_limited":
                    print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                    return False
                if reg_result == "imap_auth_failed":
                    print(f"[Worker-{worker_id}] IMAP 认证失败，停止后续流程")
                    return "imap_auth_failed"
                if reg_result == "already_registered":
                    print(f"[Worker-{worker_id}] 账号已注册，请使用 login 模式")
                    return "already_registered"
                if not reg_result:
                    print(f"[Worker-{worker_id}] 注册失败")
                    return False
            else:
                print(f"\n[Worker-{worker_id}] 模式: 登录")
                result = login_with_url_state(
                    page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT
                )
                if result == "rate_limited":
                    print(f"\n[Worker-{worker_id}] [ERROR] IP 被风控")
                    return False
                if result == "imap_auth_failed":
                    print(f"[Worker-{worker_id}] IMAP 认证失败，停止后续流程")
                    return "imap_auth_failed"
                if result == "need_register":
                    print(f"[Worker-{worker_id}] 账号未注册，请使用 register 模式")
                    return "need_register"
                if not result:
                    print(f"[Worker-{worker_id}] 登录失败")
                    return False

            # 访问 dashboard
            print(f"\n[Worker-{worker_id}] 访问 dashboard...")
            dashboard_url = "https://www.binance.com/zh-CN/my/dashboard"
            dashboard_loaded = False
            for attempt in range(3):
                try:
                    page.goto(dashboard_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                    page.wait_for_timeout(random.randint(2000, 3000))
                    current_url = page.url
                    if "/my/dashboard" in current_url or "/my/" in current_url:
                        dashboard_loaded = True
                        break
                    print(f"[Worker-{worker_id}] dashboard 未完全加载，重试 ({attempt + 1}/3)")
                except Exception as e:
                    print(f"[Worker-{worker_id}] 访问 dashboard 失败 ({attempt + 1}/3): {e}")
                    page.wait_for_timeout(1000)

            if not dashboard_loaded:
                try:
                    page.wait_for_load_state("networkidle", timeout=10000)
                except Exception:
                    pass
                page.wait_for_timeout(2000)

            # 提取 cookie
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