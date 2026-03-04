import random

# ============================================================
# 基于真实 Mac 浏览器指纹生成（服务器无头环境优化版）
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


def generate_fingerprint(use_real_profile: bool = False):
    """
    生成浏览器指纹

    Args:
        use_real_profile: True = 固定使用 mac_m4 配置，False = 随机选择
    """
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
        'user_agent': user_agent,
        'platform': profile['platform'],
        'timezone_id': random.choice(TIMEZONES),
        'locale': locale_key,
        'languages': languages,
        'chrome_version': chrome_version,
        'hardware_concurrency': profile['hardware_concurrency'],
        'device_memory': profile['device_memory'],
        'screen_width': profile['screen_width'],
        'screen_height': profile['screen_height'],
        'avail_width': profile['avail_width'],
        'avail_height': profile['avail_height'],
        'color_depth': profile['color_depth'],
        'pixel_depth': profile['pixel_depth'],
        'device_pixel_ratio': profile['device_pixel_ratio'],
        'webgl_vendor': profile['webgl_vendor'],
        'webgl_renderer': profile['webgl_renderer'],
    }