import random

# Chrome 版本池 (保持较新版本)
CHROME_VERSIONS = [
    '120.0.0.0', '121.0.0.0', '122.0.0.0', '123.0.0.0', '124.0.0.0'
]

# 常见分辨率 (只用 1920x1080，其他分辨率会触发风控)
VIEWPORTS = [
    {'width': 1920, 'height': 1080},
]

# 时区池
TIMEZONES = [
    'Asia/Shanghai',
    'Asia/Hong_Kong',
    'Asia/Singapore',
    'Asia/Tokyo',
    'America/New_York',
    'Europe/London',
]

# 语言池
LOCALES = [
    'zh-CN', 'zh-TW', 'en-US', 'en-GB', 'ja-JP'
]

# WebGL 渲染器池 (常见显卡)
WEBGL_RENDERERS = [
    {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M1, OpenGL 4.1)'},
    {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M2, OpenGL 4.1)'},
    {'vendor': 'Google Inc. (Apple)', 'renderer': 'ANGLE (Apple, Apple M3, OpenGL 4.1)'},
    {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3060, OpenGL 4.5)'},
    {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 3070, OpenGL 4.5)'},
    {'vendor': 'Google Inc. (NVIDIA)', 'renderer': 'ANGLE (NVIDIA, NVIDIA GeForce RTX 4060, OpenGL 4.5)'},
    {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) UHD Graphics 630, OpenGL 4.1)'},
    {'vendor': 'Google Inc. (Intel)', 'renderer': 'ANGLE (Intel, Intel(R) Iris(R) Xe Graphics, OpenGL 4.1)'},
]


def generate_fingerprint():
    """生成随机浏览器指纹"""
    chrome_version = random.choice(CHROME_VERSIONS)
    viewport = random.choice(VIEWPORTS)
    webgl = random.choice(WEBGL_RENDERERS)

    # 生成 User-Agent
    user_agent = f'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/{chrome_version} Safari/537.36'

    return {
        'user_agent': user_agent,
        'viewport': viewport,
        'timezone_id': random.choice(TIMEZONES),
        'locale': random.choice(LOCALES),
        'chrome_version': chrome_version,
        'webgl_vendor': webgl['vendor'],
        'webgl_renderer': webgl['renderer'],
    }
