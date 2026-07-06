"""浏览器上下文与反检测脚本构建。"""

from __future__ import annotations

import random
import shutil
import socket
import subprocess
import time
import urllib.parse

_CHROMIUM_PATH = None


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


def get_chromium_path():
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

def build_stealth_init_script(fingerprint: dict) -> str:
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

def get_launch_args(screen_width: int, screen_height: int) -> list:
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


def build_stealth_context(p, fingerprint: dict, proxy_settings, headless: bool):
    """
    创建 browser + context + 注入脚本。
    统一使用 subprocess 启动（不带 --enable-automation），更隐蔽。
    """
    viewport_height = fingerprint['screen_height'] - 80
    browser, context, page = build_stealth_context_subprocess(
        p, fingerprint, proxy_settings, headless, viewport_height
    )

    # 注入反检测脚本
    init_script = build_stealth_init_script(fingerprint)
    context.add_init_script(init_script)

    return browser, context, page


def build_stealth_context_subprocess(p, fingerprint, proxy_settings, headless, viewport_height):
    """subprocess 启动 Chromium，不带 --enable-automation。"""
    import tempfile
    port = find_free_port()
    chromium_path = p.chromium.executable_path
    user_data_dir = tempfile.mkdtemp(prefix='pw_chrome_')
    chrome_process = None
    browser = None
    proxy_server = None
    if proxy_settings:
        proxy_server = str(proxy_settings.get("server") or "").strip()
        if proxy_server:
            parsed_proxy = urllib.parse.urlsplit(proxy_server)
            if parsed_proxy.hostname and parsed_proxy.port:
                proxy_server = urllib.parse.urlunsplit(
                    (
                        parsed_proxy.scheme or "http",
                        f"{parsed_proxy.hostname}:{parsed_proxy.port}",
                        parsed_proxy.path,
                        parsed_proxy.query,
                        parsed_proxy.fragment,
                    )
                )

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
    if proxy_server:
        cmd.append(f'--proxy-server={proxy_server}')

    try:
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
        proxy_auth_enabled = setup_proxy_auth(cdp, proxy_settings)
        if not proxy_auth_enabled:
            cdp.detach()
        else:
            browser._proxy_auth_cdp = cdp

        # 保存进程引用和临时目录以便后续清理
        browser._chrome_process = chrome_process
        browser._user_data_dir = user_data_dir

        return browser, context, page
    except Exception:
        cleanup_subprocess_browser(browser, chrome_process, user_data_dir)
        raise

