import hashlib
import random
import shutil
import subprocess
import socket
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

from .flows import login_with_url_state, register_with_url_state
from .storage import save_registered_account
from .traffic_monitor import mark_cached_url
from .local_cache import init_cache_manager, get_cache_manager
from .fingerprint import generate_fingerprint

PAGE_TIMEOUT = 60000

# Playwright Chromium 路径
_CHROMIUM_PATH = None

def _get_chromium_path():
    """获取 Playwright Chromium 可执行文件路径（懒加载）"""
    global _CHROMIUM_PATH
    if _CHROMIUM_PATH is None:
        import subprocess as _sp
        result = _sp.run(
            ['python3', '-c', 'from playwright.sync_api import sync_playwright; p = sync_playwright().start(); print(p.chromium.executable_path); p.stop()'],
            capture_output=True, text=True
        )
        _CHROMIUM_PATH = result.stdout.strip()
        if not _CHROMIUM_PATH:
            raise RuntimeError(f"无法获取 Chromium 路径: {result.stderr}")
    return _CHROMIUM_PATH

def _find_free_port():
    """找一个可用的端口"""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]

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
    构建完整的反检测初始化脚本。

    修复记录 v5：
    - [修复1b] Canvas iframe hash 一致问题根本修复：
               改用 frame-aware seed：在 JS 层用 location.href 哈希混入 base seed，
               让每个 frame（主页面/iframe/sandbox iframe）产生不同的 _noiseShift，
               从而 getImageData 扰动结果不同，canvas hash 不同。
               之前 v4 的"固定 per-session seed"思路错误——5个 frame 画相同内容，
               固定 seed 扰动结果也相同，hash 依然一致。
    - [修复2] wInnerHeight > wOuterHeight ✅ 已在 v4 修复
    - [修复3] chrome.webstore/runtime constructor 报错 ✅ 已在 v4 修复
    """
    webgl_vendor          = fingerprint['webgl_vendor']
    webgl_renderer        = fingerprint['webgl_renderer']
    platform              = fingerprint['platform']
    hardware_concurrency  = fingerprint['hardware_concurrency']
    device_memory         = fingerprint['device_memory']
    languages             = fingerprint['languages']
    screen_width          = fingerprint['screen_width']
    screen_height         = fingerprint['screen_height']
    avail_width           = fingerprint['avail_width']
    avail_height          = fingerprint['avail_height']
    color_depth           = fingerprint['color_depth']
    pixel_depth           = fingerprint['pixel_depth']
    device_pixel_ratio    = fingerprint['device_pixel_ratio']
    languages_json        = str(languages).replace("'", '"')

    # ── 修复1: Canvas noise ──────────────────────────────────────────
    # 使用固定 per-session seed（在 Python 层生成一次），保证同一 session 内
    # 主页面和 iframe 使用相同的 seed 产生不同但一致的扰动
    # 关键：seed 固定后，主页面 canvas hash ≠ iframe canvas hash（因为注入时序不同）
    # 真实浏览器里 sandbox iframe canvas hash 本来就和主页面不同，所以"不同"才是正确的
    canvas_noise_seed = random.randint(100000, 999999)  # 固定 seed，整个 session 唯一
    canvas_noise = (canvas_noise_seed % 9000) / 10000000.0 + 0.0001  # 0.0001 ~ 0.0009
    canvas_noise_int = (canvas_noise_seed % 9) + 1  # 1~9

    # ── Worker 补丁脚本（值在 Python 层直接嵌入）──────────────────
    worker_patch_js = f"""
if (typeof OffscreenCanvas !== 'undefined') {{
    const _orig = OffscreenCanvas.prototype.getContext;
    OffscreenCanvas.prototype.getContext = function(type, attrs) {{
        const ctx = _orig.call(this, type, attrs);
        if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {{
            if (!ctx.__bnPatch) {{
                ctx.__bnPatch = true;
                const _gp = ctx.getParameter.bind(ctx);
                ctx.getParameter = function(p) {{
                    if (p === 37445) return '{webgl_vendor}';
                    if (p === 37446) return '{webgl_renderer}';
                    return _gp(p);
                }};
                const _ge = ctx.getExtension.bind(ctx);
                ctx.getExtension = function(name) {{
                    const ext = _ge(name);
                    if (name === 'WEBGL_debug_renderer_info' && ext) {{
                        try {{
                            Object.defineProperty(ext, 'UNMASKED_VENDOR_WEBGL',   {{ get: () => 37445 }});
                            Object.defineProperty(ext, 'UNMASKED_RENDERER_WEBGL', {{ get: () => 37446 }});
                        }} catch(e) {{}}
                    }}
                    return ext;
                }};
            }}
        }}
        return ctx;
    }};
}}
"""

    return f"""
(function() {{

// ── 1. Navigator.prototype 属性伪造 ──────────────────────────────
(function() {{
    const proto = Object.getPrototypeOf(navigator);

    function makeNativeGetter(value, propName) {{
        const fn = function() {{ return value; }};
        const nativeStr = 'function get ' + propName + '() {{ [native code] }}';
        try {{
            Object.defineProperty(fn, 'toString', {{
                value: function() {{ return nativeStr; }},
                configurable: true,
                writable: true,
            }});
            Object.defineProperty(fn, 'name', {{
                value: 'get ' + propName,
                configurable: true,
            }});
        }} catch(e) {{}}
        return fn;
    }}

    Object.defineProperty(proto, 'languages', {{
        get: makeNativeGetter({languages_json}, 'languages'),
        configurable: true,
        enumerable: true,
    }});

    Object.defineProperty(proto, 'hardwareConcurrency', {{
        get: makeNativeGetter({hardware_concurrency}, 'hardwareConcurrency'),
        configurable: true,
        enumerable: true,
    }});

    Object.defineProperty(proto, 'deviceMemory', {{
        get: makeNativeGetter({device_memory}, 'deviceMemory'),
        configurable: true,
        enumerable: true,
    }});
}})();

// ── 4. 屏幕信息 + 窗口尺寸修复 ──────────────────────────────────
// 修复: wInnerHeight > wOuterHeight 问题
// 无头模式下 Playwright 的 outerHeight 有时小于 innerHeight
// 真实 Chrome macOS 里 outerHeight = innerHeight + 工具栏高度（约 85px）
// 直接在 JS 层伪造 outerHeight/outerWidth
Object.defineProperty(screen, 'width',       {{ get: () => {screen_width} }});
Object.defineProperty(screen, 'height',      {{ get: () => {screen_height} }});
Object.defineProperty(screen, 'availWidth',  {{ get: () => {avail_width} }});
Object.defineProperty(screen, 'availHeight', {{ get: () => {avail_height} }});
Object.defineProperty(screen, 'colorDepth',  {{ get: () => {color_depth} }});
Object.defineProperty(screen, 'pixelDepth',  {{ get: () => {pixel_depth} }});
Object.defineProperty(window, 'devicePixelRatio', {{ get: () => {device_pixel_ratio} }});

(function() {{
    // 实测：真实Chrome outerWidth === innerWidth（无差值），只有outerHeight因工具栏大于innerHeight
    // outerWidth 不伪造，让Playwright自然值即可
    const _TOOLBAR_H = 85;
    Object.defineProperty(window, 'outerHeight', {{
        get: function() {{ return window.innerHeight + _TOOLBAR_H; }},
        configurable: true,
    }});
}})();

// ── 5. chrome 对象 ──────────────────────────────────────────────
// 实测真实Chrome：webstore/runtime 已被移除，访问会 TypeError，这才是真实表现
// 不伪造 webstore/runtime，只保留 loadTimes/csi/app 这些真实存在的属性
(function() {{
    function makeNativeFunction(name, fn) {{
        try {{
            Object.defineProperty(fn, 'name', {{ value: name, configurable: true }});
            Object.defineProperty(fn, 'toString', {{
                value: function() {{ return 'function ' + name + '() {{ [native code] }}'; }},
                configurable: true,
                writable: true,
            }});
        }} catch(e) {{}}
        return fn;
    }}

    window.chrome = {{
        app: {{
            isInstalled: false,
            InstallState: {{
                DISABLED: 'disabled', INSTALLED: 'installed', NOT_INSTALLED: 'not_installed'
            }},
            RunningState: {{
                CANNOT_RUN: 'cannot_run', READY_TO_RUN: 'ready_to_run', RUNNING: 'running'
            }},
            getDetails:      makeNativeFunction('getDetails', function() {{ return null; }}),
            getIsInstalled:  makeNativeFunction('getIsInstalled', function() {{ return false; }}),
            runningState:    makeNativeFunction('runningState', function() {{ return 'cannot_run'; }}),
        }},
        loadTimes: makeNativeFunction('loadTimes', function() {{
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
                connectionInfo: 'h2',
            }};
        }}),
        csi: makeNativeFunction('csi', function() {{
            return {{
                startE:  Date.now(),
                onloadT: Date.now() + Math.floor(Math.random() * 500 + 200),
                pageT:   Math.random() * 5000 + 1000,
                tran:    15,
            }};
        }}),
    }};
}})();

// ── 6. Permissions API ───────────────────────────────────────────
(function() {{
    const _orig = window.navigator.permissions.query.bind(navigator.permissions);
    window.navigator.permissions.query = function(parameters) {{
        const name = parameters && parameters.name;
        if (name === 'notifications' || name === 'clipboard-read' || name === 'clipboard-write') {{
            return Promise.resolve({{ state: 'prompt', onchange: null }});
        }}
        return _orig(parameters);
    }};
}})();

// ── 7. WebGL 伪造（主线程）───────────────────────────────────────
(function() {{
    const VENDOR   = '{webgl_vendor}';
    const RENDERER = '{webgl_renderer}';

    function patchContext(ctx) {{
        if (!ctx || ctx.__bnPatch) return;
        ctx.__bnPatch = true;

        const _gp = ctx.getParameter.bind(ctx);
        ctx.getParameter = function(p) {{
            if (p === 37445) return VENDOR;
            if (p === 37446) return RENDERER;
            return _gp(p);
        }};

        const _ge = ctx.getExtension.bind(ctx);
        ctx.getExtension = function(name) {{
            const ext = _ge(name);
            if (name === 'WEBGL_debug_renderer_info' && ext) {{
                try {{
                    Object.defineProperty(ext, 'UNMASKED_VENDOR_WEBGL',   {{ get: () => 37445 }});
                    Object.defineProperty(ext, 'UNMASKED_RENDERER_WEBGL', {{ get: () => 37446 }});
                }} catch(e) {{}}
            }}
            return ext;
        }};
    }}

    const _origGetCtx = HTMLCanvasElement.prototype.getContext;
    HTMLCanvasElement.prototype.getContext = function(type, attrs) {{
        const ctx = _origGetCtx.call(this, type, attrs);
        if (ctx && (type === 'webgl' || type === 'webgl2' || type === 'experimental-webgl')) {{
            patchContext(ctx);
        }}
        return ctx;
    }};

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

    const _workerPatch = {repr(worker_patch_js)};
    const _origWorker = window.Worker;
    window.Worker = function(url, opts) {{
        if (typeof url === 'string' && url.startsWith('blob:')) {{
            try {{
                const patchBlob = new Blob([_workerPatch], {{ type: 'application/javascript' }});
                const patchURL  = URL.createObjectURL(patchBlob);
                const wrapBlob  = new Blob(
                    ['importScripts(' + JSON.stringify(patchURL) + ');\\n'],
                    {{ type: 'application/javascript' }}
                );
                url = URL.createObjectURL(wrapBlob);
            }} catch(e) {{}}
        }}
        return new _origWorker(url, opts);
    }};
    window.Worker.prototype = _origWorker.prototype;
}})();

// ── 8. Canvas 噪声（统一 seed，所有 frame hash 一致）────────────
// 实测真实Chrome：5个canvas（主页面+iframe）hash 全部一致，这才是正常表现
// 之前的 frame-aware 让主页面≠iframe，反而是异常特征
// 现在改回统一 _noiseShift，所有 frame 扰动相同，hash 一致
(function() {{
    const _noiseShift = {canvas_noise_int};  // 固定 per-session，所有 frame 相同
    const _shift = (_noiseShift % 10) + 1;

    // ── toDataURL 扰动 ──────────────────────────────────────────
    function perturbDataURL(dataURL) {{
        if (!dataURL || dataURL === 'data:,') return dataURL;
        const commaIdx = dataURL.indexOf(',');
        if (commaIdx < 0) return dataURL;
        const header = dataURL.substring(0, commaIdx + 1);
        const b64 = dataURL.substring(commaIdx + 1);
        if (b64.length < 20) return dataURL;
        const B64 = 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/';
        const pos = Math.max(0, b64.length - 20 - _shift);
        const ch = b64[pos];
        const idx = B64.indexOf(ch);
        if (idx < 0) return dataURL;
        const newCh = B64[(idx + _shift + 1) % B64.length];
        const newB64 = b64.substring(0, pos) + newCh + b64.substring(pos + 1);
        return header + newB64;
    }}

    const _origToDataURL = HTMLCanvasElement.prototype.toDataURL;
    HTMLCanvasElement.prototype.toDataURL = function(type, quality) {{
        return perturbDataURL(_origToDataURL.call(this, type, quality));
    }};

    const _origToBlob = HTMLCanvasElement.prototype.toBlob;
    if (_origToBlob) {{
        HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {{
            return _origToBlob.call(this, function(blob) {{
                if (!blob || !callback) return;
                const fr = new FileReader();
                fr.onload = function() {{
                    const perturbed = perturbDataURL(fr.result);
                    try {{
                        const arr = perturbed.split(',');
                        const mime = arr[0].match(/:(.*?);/)[1];
                        const bstr = atob(arr[1]);
                        let n = bstr.length;
                        const u8 = new Uint8Array(n);
                        while (n--) u8[n] = bstr.charCodeAt(n);
                        callback(new Blob([u8], {{type: mime}}));
                    }} catch(e) {{
                        callback(blob);
                    }}
                }};
                fr.readAsDataURL(blob);
            }}, type, quality);
        }};
    }}

    // ── getImageData 扰动（统一，所有 frame 相同）────────────────
    const _origGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    CanvasRenderingContext2D.prototype.getImageData = function(x, y, w, h) {{
        const imageData = _origGetImageData.call(this, x, y, w, h);
        if (imageData && imageData.data && imageData.data.length > 4) {{
            const data = imageData.data;
            let found = false;
            for (let i = data.length - 4; i >= 0; i -= 4) {{
                if (data[i] !== 0 || data[i+1] !== 0 || data[i+2] !== 0 || data[i+3] !== 0) {{
                    data[i] = (data[i] + _noiseShift) & 0xFF;
                    found = true;
                    break;
                }}
            }}
            if (!found) {{
                data[0] = _noiseShift & 0xFF;
            }}
        }}
        return imageData;
    }};
}})();

// ── 9. 媒体设备（保证不为空）────────────────────────────────────
(function() {{
    const _orig = navigator.mediaDevices.enumerateDevices.bind(navigator.mediaDevices);
    navigator.mediaDevices.enumerateDevices = async function() {{
        const devices = await _orig();
        if (devices.length === 0) {{
            return [
                {{ deviceId: '', kind: 'audioinput',  label: '', groupId: '' }},
                {{ deviceId: '', kind: 'videoinput',  label: '', groupId: '' }},
                {{ deviceId: '', kind: 'audiooutput', label: '', groupId: '' }},
            ];
        }}
        return devices;
    }};
}})();

// ── 10. navigator.connection（Network Information API）───────────
// 真实Chrome有此属性，effectiveType通常为'4g'或'3g'
// Playwright默认不注入，navigator.connection为undefined是自动化特征
(function() {{
    const _types = ['4g', '3g'];
    const _type = _types[{canvas_noise_int} % 2];  // 用session seed随机选，保持一致性
    const _conn = {{
        effectiveType: _type,
        rtt: _type === '4g' ? 50 : 100,
        downlink: _type === '4g' ? 10 : 1.5,
        saveData: false,
        type: 'wifi',
        onchange: null,
        addEventListener: function() {{}},
        removeEventListener: function() {{}},
        dispatchEvent: function() {{ return true; }},
    }};
    try {{
        Object.defineProperty(navigator, 'connection', {{
            get: () => _conn,
            configurable: true,
        }});
    }} catch(e) {{}}
}})();

// ── 10. 清除自动化特征 ───────────────────────────────────────────
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Array;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Promise;
delete window.cdc_adoQpoasnfa76pfcZLmcfl_Symbol;

}})();
"""


def _handle_route(route, request):
    url = request.url
    resource_type = request.resource_type
    cache_manager = get_cache_manager()
    if cache_manager:
        cached = cache_manager.get_cached(url, resource_type)
        if cached:
            mark_cached_url(url)
            route.fulfill(status=200, headers=cached["headers"], body=cached["body"])
            return
    route.continue_()


def _on_response(response):
    try:
        url = response.request.url
        resource_type = response.request.resource_type
        if response.status != 200:
            return
        if not any(d in url.lower() for d in CACHEABLE_DOMAINS):
            return
        if resource_type not in ("script", "stylesheet", "fetch"):
            return
        cache_manager = get_cache_manager()
        if cache_manager:
            try:
                cache_manager.save_to_cache(url, resource_type, response.body(), dict(response.headers))
            except Exception:
                pass
    except Exception:
        pass


def _get_launch_args(screen_width: int, screen_height: int) -> list:
    """
    修复2: wInnerHeight > wOuterHeight 问题
    原因：之前 --window-size 使用 screen_height（如 982），
          但 viewport 设置为 screen_height - 80（902），
          Playwright 在无头模式下 wOuterHeight 从 --window-size 读取（822 = 902 toolbar？）
          实际上 --window-size 设置的是整个浏览器窗口大小，包含工具栏
          无头模式没有工具栏，所以 outerHeight = innerHeight = viewport height
    修复：--window-size 使用 viewport 尺寸（screen_height - 80），
          让 outerHeight = innerHeight，避免 outerHeight < innerHeight 的异常
    """
    viewport_height = screen_height - 80
    return [
        "--incognito",
        '--enable-features=WebAssembly',
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-infobars',
        # 修复: 使用 viewport_height 而非 screen_height，避免 wInnerHeight > wOuterHeight
        f'--window-size={screen_width},{viewport_height}',
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


def _build_context(p, fingerprint: dict, proxy_settings, headless: bool):
    """
    创建 browser + context + 注入脚本。
    - 无代理 / 无认证代理：subprocess 启动（不带 --enable-automation）
    - 需认证代理：Playwright launch + new_context(proxy=...) 方式
    """
    viewport_height = fingerprint['screen_height'] - 80

    # 需要认证的代理走 Playwright launch 方式
    needs_auth = proxy_settings and proxy_settings.get("username")
    if needs_auth:
        browser, context, page = _build_context_playwright(
            p, fingerprint, proxy_settings, headless, viewport_height
        )
    else:
        browser, context, page = _build_context_subprocess(
            p, fingerprint, proxy_settings, headless, viewport_height
        )

    # 注入反检测脚本
    init_script = _build_init_script(fingerprint)
    context.add_init_script(init_script)

    return browser, context, page


def _build_context_playwright(p, fingerprint, proxy_settings, headless, viewport_height):
    """Playwright launch 方式，支持代理认证。"""
    launch_args = _get_launch_args(fingerprint['screen_width'], fingerprint['screen_height'])
    browser = p.chromium.launch(
        headless=headless,
        args=launch_args,
    )
    context = browser.new_context(
        proxy=proxy_settings,
        user_agent=fingerprint['user_agent'],
        locale=fingerprint['locale'],
        timezone_id=fingerprint['timezone_id'],
        viewport={'width': fingerprint['screen_width'], 'height': viewport_height},
        screen={'width': fingerprint['screen_width'], 'height': fingerprint['screen_height']},
        device_scale_factor=fingerprint['device_pixel_ratio'],
    )
    page = context.new_page()
    return browser, context, page


def _build_context_subprocess(p, fingerprint, proxy_settings, headless, viewport_height):
    """subprocess 启动 Chromium，不带 --enable-automation。"""
    import tempfile
    port = _find_free_port()
    chromium_path = p.chromium.executable_path
    user_data_dir = tempfile.mkdtemp(prefix='pw_chrome_')

    # 最小化启动参数，不加 --enable-automation
    cmd = [
        chromium_path,
        f'--remote-debugging-port={port}',
        f'--user-data-dir={user_data_dir}',
        '--disable-blink-features=AutomationControlled',
        '--no-first-run',
        '--no-default-browser-check',
        f'--window-size={fingerprint["screen_width"]},{viewport_height}',
        '--force-color-profile=srgb',
        '--disable-background-timer-throttling',
        '--disable-backgrounding-occluded-windows',
        '--disable-renderer-backgrounding',
        '--disable-ipc-flooding-protection',
        'about:blank',
    ]
    if headless:
        cmd.append('--headless=new')
    if proxy_settings:
        cmd.append(f'--proxy-server={proxy_settings["server"]}')

    # 启动 Chromium 进程
    chrome_process = subprocess.Popen(
        cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )

    # 等待 CDP 端口就绪
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
        chrome_process.terminate()
        raise RuntimeError("CDP 端口未就绪，启动超时")

    # 再等一下确保 HTTP 服务完全就绪
    time.sleep(1)

    # 连接
    browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
    context = browser.contexts[0]

    # 通过 CDP 设置 user-agent / 时区 / 设备指标
    page = context.pages[0] if context.pages else context.new_page()
    cdp = context.new_cdp_session(page)
    cdp.send('Emulation.setUserAgentOverride', {
        'userAgent': fingerprint['user_agent'],
        'platform': fingerprint['platform'],
        'acceptLanguage': ','.join(fingerprint['languages']),
    })
    cdp.send('Emulation.setTimezoneOverride', {
        'timezoneId': fingerprint['timezone_id'],
    })
    cdp.send('Emulation.setDeviceMetricsOverride', {
        'width': fingerprint['screen_width'],
        'height': viewport_height,
        'deviceScaleFactor': fingerprint['device_pixel_ratio'],
        'mobile': False,
        'screenWidth': fingerprint['screen_width'],
        'screenHeight': fingerprint['screen_height'],
    })
    cdp.detach()

    # 保存进程引用和临时目录以便后续清理
    browser._chrome_process = chrome_process
    browser._user_data_dir = user_data_dir

    return browser, context, page


def warmup_cache(proxy_config=None, headless=True):
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
            channel="chrome",
            args=launch_args,
            proxy=proxy_settings,
            user_agent=fingerprint['user_agent'],
            locale=fingerprint['locale'],
            timezone_id=fingerprint['timezone_id'],
            viewport={
                "width":  fingerprint['screen_width'],
                "height": fingerprint['screen_height'] - 80,
            },
            screen={
                "width":  fingerprint['screen_width'],
                "height": fingerprint['screen_height'],
            },
            device_scale_factor=fingerprint['device_pixel_ratio'],
        )
        try:
            context.add_init_script(_build_init_script(fingerprint))
            page = context.new_page()
            page.route("**/*", _handle_route)
            page.on("response", _on_response)
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


def extract_cookies_and_csrf(page):
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
    output_file  = config["output_file"]
    headless     = config.get("headless", False)
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
        fingerprint = generate_fingerprint(use_real_profile=False)
        print(
            f"[Worker-{worker_id}] 指纹: "
            f"UA={fingerprint['user_agent'][-40:]} | "
            f"TZ={fingerprint['timezone_id']} | "
            f"Screen={fingerprint['screen_width']}x{fingerprint['screen_height']} | "
            f"DPR={fingerprint['device_pixel_ratio']} | "
            f"Lang={fingerprint['languages']}"
        )

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

        print(f"[Worker-{worker_id}] 浏览器配置: {mode}模式（完整反检测）")
        browser, context, page = _build_context(p, fingerprint, proxy_settings, headless)

        # ── 修复: 初始化缓存（之前只在 warmup_cache 里初始化，register_account 里从未调用）
        if config.get("cache", {}).get("enabled", False):
            init_cache_manager(CACHE_DIR)
            _init_worker_cache(worker_id)  # 从 master 缓存复制到 worker 目录
            # 只在缓存启用时注册路由拦截（page.route 会启用 CDP Fetch.enable，是自动化特征）
            page.route("**/*", _handle_route)
            page.on("response", _on_response)
            print(f"[Worker-{worker_id}] 缓存已启用")

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
                    if "/my/dashboard" in page.url or "/my/" in page.url:
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
            cookie_string, csrftoken = extract_cookies_and_csrf(page)
            if cookie_string and csrftoken:
                account_data = {
                    "name":             f"账号_{email_addr.split('@')[0]}",
                    "email":            f"{email_addr}----{email_password}",
                    "cookie":           cookie_string,
                    "csrftoken":        csrftoken,
                    "enabled":          True,
                    "avatar_changed":   False,
                    "nickname_changed": False,
                    "display_name":     "",
                    "mail_api_url":     "https://wrpifa-com.netlify.app/",
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
            if config.get("cache", {}).get("enabled", False):
                _sync_new_cache_to_master(worker_id)  # 把新缓存文件同步回 master
            if browser:
                chrome_proc = getattr(browser, '_chrome_process', None)
                user_data_dir = getattr(browser, '_user_data_dir', None)
                browser.close()
                if chrome_proc:
                    chrome_proc.terminate()
                    try:
                        chrome_proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        chrome_proc.kill()
                if user_data_dir:
                    shutil.rmtree(user_data_dir, ignore_errors=True)