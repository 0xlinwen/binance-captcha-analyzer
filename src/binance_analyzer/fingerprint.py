import random

# ============================================================
# 浏览器指纹
# native：默认。使用本机 Chrome 真实身份，不伪造 UA/时区/WebGL/Canvas
# spoofed：随机 Mac 配置，仅在明确配置 fingerprint.mode=spoofed 时启用
# Chrome 107+ 使用 UA Reduction：minor version 固定为 0.0.0
# ============================================================

# Chrome 版本池
CHROME_VERSIONS = [
    '138.0.0.0',
    '140.0.0.0',
    '141.0.0.0',
    '142.0.0.0',
    '143.0.0.0',
    '144.0.0.0',
    '145.0.0.0',
]

# 时区池
TIMEZONES = [
    'Asia/Shanghai',
    'Asia/Hong_Kong',
    'Asia/Singapore',
]

# 语言池（locale key -> navigator.languages 数组）
LOCALES_MAP = {
    'zh-CN': ['zh-CN', 'zh', 'en-US', 'en'],
    'en-US': ['en-US', 'zh-CN', 'zh', 'en'],
    'zh-TW': ['zh-TW', 'zh', 'en-US', 'en'],
}

# 指纹配置池（全部 Mac Apple Silicon，服务器通过 JS 伪造 WebGL）
FINGERPRINT_PROFILES = [
    {
        'name': 'mac_m4_real',
        'platform': 'MacIntel',
        'os': 'Macintosh; Intel Mac OS X 10_15_7',
        'hardware_concurrency': 10,
        'device_memory': 8,
        'screen_width': 1470,
        'screen_height': 956,
        'avail_width': 1470,
        'avail_height': 840,
        'color_depth': 30,
        'pixel_depth': 30,
        'device_pixel_ratio': 2,
        'webgl_vendor': 'Google Inc. (Apple)',
        'webgl_renderer': 'ANGLE (Apple, ANGLE Metal Renderer: Apple M4, Unspecified Version)',
    },
    {
        'name': 'mac_m1_8core',
        'platform': 'MacIntel',
        'os': 'Macintosh; Intel Mac OS X 10_15_7',
        'hardware_concurrency': 8,
        'device_memory': 8,
        'screen_width': 1440,
        'screen_height': 900,
        'avail_width': 1440,
        'avail_height': 784,
        'color_depth': 30,
        'pixel_depth': 30,
        'device_pixel_ratio': 2,
        'webgl_vendor': 'Google Inc. (Apple)',
        'webgl_renderer': 'ANGLE (Apple, ANGLE Metal Renderer: Apple M1, Unspecified Version)',
    },
    {
        'name': 'mac_m2_8core',
        'platform': 'MacIntel',
        'os': 'Macintosh; Intel Mac OS X 10_15_7',
        'hardware_concurrency': 8,
        'device_memory': 8,
        'screen_width': 1512,
        'screen_height': 982,
        'avail_width': 1512,
        'avail_height': 866,
        'color_depth': 30,
        'pixel_depth': 30,
        'device_pixel_ratio': 2,
        'webgl_vendor': 'Google Inc. (Apple)',
        'webgl_renderer': 'ANGLE (Apple, ANGLE Metal Renderer: Apple M2, Unspecified Version)',
    },
    {
        'name': 'mac_m3_pro',
        'platform': 'MacIntel',
        'os': 'Macintosh; Intel Mac OS X 10_15_7',
        'hardware_concurrency': 12,
        'device_memory': 16,
        'screen_width': 1512,
        'screen_height': 982,
        'avail_width': 1512,
        'avail_height': 866,
        'color_depth': 30,
        'pixel_depth': 30,
        'device_pixel_ratio': 2,
        'webgl_vendor': 'Google Inc. (Apple)',
        'webgl_renderer': 'ANGLE (Apple, ANGLE Metal Renderer: Apple M3 Pro, Unspecified Version)',
    },
]


FINGERPRINT_MODE_NATIVE = "native"
FINGERPRINT_MODE_SPOOFED = "spoofed"
FINGERPRINT_MODES = {FINGERPRINT_MODE_NATIVE, FINGERPRINT_MODE_SPOOFED}


def is_native_fingerprint(fingerprint: dict) -> bool:
    """是否使用本机 Chrome 真实身份，不注入 UA/屏幕/WebGL 等伪造。"""
    return str((fingerprint or {}).get("mode") or "").strip().lower() == FINGERPRINT_MODE_NATIVE


def _native_fingerprint() -> dict:
    return {
        "mode": FINGERPRINT_MODE_NATIVE,
        "user_agent": "",
        "platform": "",
        "timezone_id": "",
        "locale": "",
        "languages": [],
        "chrome_version": "",
        "hardware_concurrency": 0,
        "device_memory": 0,
        "screen_width": 0,
        "screen_height": 0,
        "avail_width": 0,
        "avail_height": 0,
        "color_depth": 0,
        "pixel_depth": 0,
        "device_pixel_ratio": 0,
        "webgl_vendor": "",
        "webgl_renderer": "",
    }


def describe_fingerprint(fingerprint: dict) -> str:
    """生成日志用的指纹摘要。"""
    if is_native_fingerprint(fingerprint):
        return "native（使用本机 Chrome 真实身份，不伪造 UA/时区/WebGL/Canvas）"
    user_agent = str(fingerprint.get("user_agent") or "")
    return (
        f"UA={user_agent[-40:]} | "
        f"TZ={fingerprint.get('timezone_id')} | "
        f"Screen={fingerprint.get('screen_width')}x{fingerprint.get('screen_height')} | "
        f"DPR={fingerprint.get('device_pixel_ratio')} | "
        f"Lang={fingerprint.get('languages')}"
    )


def generate_fingerprint(use_real_profile: bool = False, mode: str = FINGERPRINT_MODE_NATIVE) -> dict:
    """
    生成浏览器指纹。

    Args:
        use_real_profile: spoofed 模式下 True = 固定使用 mac_m4 配置，False = 随机选择
        mode: native = 不伪造，使用本机 Chrome 真实身份；spoofed = 随机/固定伪装配置
    """
    normalized_mode = str(mode or "").strip().lower()
    if normalized_mode not in FINGERPRINT_MODES:
        raise ValueError("fingerprint.mode 只支持 native/spoofed")
    if normalized_mode == FINGERPRINT_MODE_NATIVE:
        return _native_fingerprint()

    if use_real_profile:
        profile = FINGERPRINT_PROFILES[0]
    else:
        profile = random.choice(FINGERPRINT_PROFILES)

    chrome_version = random.choice(CHROME_VERSIONS)
    locale_key = random.choice(list(LOCALES_MAP.keys()))
    languages = LOCALES_MAP[locale_key]

    user_agent = (
        f"Mozilla/5.0 ({profile['os']}) "
        f"AppleWebKit/537.36 (KHTML, like Gecko) "
        f"Chrome/{chrome_version} Safari/537.36"
    )

    return {
        "mode": FINGERPRINT_MODE_SPOOFED,
        "user_agent": user_agent,
        "platform": profile["platform"],
        "timezone_id": random.choice(TIMEZONES),
        "locale": locale_key,
        "languages": languages,
        "chrome_version": chrome_version,
        "hardware_concurrency": profile["hardware_concurrency"],
        "device_memory": profile["device_memory"],
        "screen_width": profile["screen_width"],
        "screen_height": profile["screen_height"],
        "avail_width": profile["avail_width"],
        "avail_height": profile["avail_height"],
        "color_depth": profile["color_depth"],
        "pixel_depth": profile["pixel_depth"],
        "device_pixel_ratio": profile["device_pixel_ratio"],
        "webgl_vendor": profile["webgl_vendor"],
        "webgl_renderer": profile["webgl_renderer"],
    }
