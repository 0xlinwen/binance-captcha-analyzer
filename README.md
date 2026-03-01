# Binance Captcha Analyzer

Binance 登录/注册自动化工具，基于 Playwright 浏览器自动化 + OpenRouter AI 验证码识别 + IMAP 邮箱验证码提取。

## 核心功能

- 自动登录/注册 Binance 账号
- AI 识别滑块验证码和点击验证码（通过 OpenRouter API）
- IMAP 自动提取邮箱 MFA 验证码
- 多进程并发处理多个账号
- 本地静态资源缓存（减少网络流量）
- 流量监控统计
- 自动提取 Cookie 和 CSRF Token

## 整体架构

```
┌─────────────────────────────────────────────────────────┐
│                    cli.py (入口)                         │
│         命令行解析 / 并发调度 / 信号处理                   │
│              ProcessPoolExecutor                        │
└──────────────────────┬──────────────────────────────────┘
                       │ 每个账号一个进程
                       ▼
┌─────────────────────────────────────────────────────────┐
│               orchestrator.py (编排器)                    │
│     浏览器启动 / 缓存管理 / Cookie提取 / 流量监控          │
│                                                         │
│  登录模式:  简化浏览器配置                                │
│  注册模式:  完整反检测配置 + WebGL 伪造                   │
└──────────────────────┬──────────────────────────────────┘
                       │
                       ▼
┌─────────────────────────────────────────────────────────┐
│                  flows.py (状态机)                        │
│            login_with_url_state (登录)                   │
│            register_with_url_state (注册)                │
│                                                         │
│  根据 URL 路径判断当前阶段：                               │
│  /login → 输入邮箱 → /login/password → 输入密码           │
│  → /login/mfa → 邮件验证码 → /login/stay-signed-in       │
│  → /my/dashboard → 登录成功                              │
└───────┬──────────────────┬──────────────────┬───────────┘
        │                  │                  │
        ▼                  ▼                  ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────────┐
│ web_actions  │  │captcha_solver│  │   email_imap     │
│ 页面交互动作  │  │ 验证码执行    │  │ IMAP邮件验证码    │
│ 输入/点击/   │  │ 滑块/点击    │  │ 提取/填充/提交    │
│ 跳转/弹窗    │  │ 重试/冷却    │  │                  │
└──────────────┘  └──────┬───────┘  └──────────────────┘
                         │
                         ▼
                  ┌──────────────┐
                  │  captcha_ai  │
                  │ OpenRouter   │
                  │ AI 视觉识别   │
                  └──────────────┘
```

## 项目结构

```
captcha_analyzer.py                    # 兼容入口
src/binance_analyzer/
  __init__.py
  cli.py                               # 主入口、并发调度、信号处理、缓存预热
  config.py                            # 配置加载与默认值
  orchestrator.py                      # 单账号编排（浏览器启动、缓存、Cookie提取）
  flows.py                             # 登录/注册状态机（URL驱动）
  web_actions.py                       # 页面交互（输入邮箱/密码、点击按钮、弹窗处理）
  captcha_solver.py                    # 验证码执行（滑块拖动、点击图片、重试策略）
  captcha_ai.py                        # OpenRouter AI 调用与 JSON 解析
  email_imap.py                        # IMAP 邮件验证码提取与 MFA 提交
  storage.py                           # 账号文件读取、结果写入（文件锁）
  local_cache.py                       # 应用层静态资源缓存
  traffic_monitor.py                   # 流量统计（按类型/域名/请求）
```

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

依赖：
- `playwright` - 浏览器自动化
- `requests` - OpenRouter API 调用
- `opencv-python` + `numpy` - 验证码截图调试标注
- `scikit-image` - 图像处理

## 配置

复制 `config.example.json` 为 `config.json`，按需修改：

```bash
cp config.example.json config.json
```

### 配置项说明

```jsonc
{
  // === 必填 ===
  "openrouter_api_key": "sk-or-v1-xxx",   // OpenRouter API Key（也可通过环境变量 OPENROUTER_API_KEY 设置）
  "models": ["google/gemini-3-flash-preview"],  // AI 模型列表，取第一个
  "imap_host": "imap.example.com",         // 邮箱 IMAP 服务器
  "imap_port": 993,                        // IMAP 端口
  "accounts_file": "accounts.txt",         // 账号文件路径
  "output_file": "output/registered_accounts.json",  // 输出文件路径

  // === 浏览器 ===
  "headless": false,                       // 是否无头模式
  "max_workers": 2,                        // 并发进程数

  // === 本地缓存 ===
  "cache": {
    "enabled": true                        // 是否启用本地静态资源缓存
  },
  // enabled=true:  使用 persistent_context + route拦截 + 本地缓存
  // enabled=false: 使用普通 launch 模式，无拦截，干净浏览器环境

  // === 代理 ===
  "proxy": {
    "enabled": false,
    "server": "host:port",                 // 支持 http/https/socks5
    "username": "",
    "password": ""
  },

  // === 登录 ===
  "login": {
    "start_url": "https://accounts.binance.com/zh-CN/login"
  },

  // === 验证码 ===
  "captcha": {
    "retry_mode": "fast",                  // fast: 快速重试
    "max_attempts_per_round": 3,           // 每轮最大尝试次数
    "max_rounds": 2,                       // 最大轮次（每轮重新加载页面）
    "cooldown_on_risk_min_sec": 20,        // 风控冷却最小秒数
    "cooldown_on_risk_max_sec": 60,        // 风控冷却最大秒数
    "click_retry_per_cell": 3              // 点击验证码单格重试次数
  },

  // === MFA ===
  "mfa": {
    "submit_retry": 2,                     // MFA 提交重试次数
    "not_registered_keywords": [           // 未注册关键词
      "未注册", "账号不存在", "account does not exist", "not registered", "没有账号"
    ]
  }
}
```

### 账号文件格式

`accounts.txt`，每行一个账号：

```
email1@example.com:password1
email2@example.com:password2
```

## 运行

```bash
# 正常运行
python captcha_analyzer.py

# 刷新缓存（删除旧缓存并重新预热）
python captcha_analyzer.py --refresh-cache
```

---

## 浏览器环境配置详解

### 登录模式 vs 注册模式

登录和注册使用不同的浏览器配置策略：

| 特性 | 登录模式 | 注册模式 |
|------|----------|----------|
| 浏览器启动 | `chromium.launch()` 简化配置 | `chromium.launch()` 完整反检测 |
| 用户数据目录 | 无（每次全新浏览器） | 无（每次全新浏览器） |
| 反检测脚本 | 无 | 完整 WebGL 伪造 |
| User-Agent | 默认 | 默认 |
| 视口大小 | 默认 | 默认 |
| 时区/语言 | 默认 | Asia/Shanghai, zh-CN |

### 登录模式浏览器配置

```python
browser = p.chromium.launch(
    headless=headless,
    args=[
        '--disable-blink-features=AutomationControlled',  # 隐藏自动化标识
        '--no-sandbox',                                    # 禁用沙箱（提高兼容性）
    ],
    proxy=proxy_settings,  # 可选代理
)
page = browser.new_page()
```

**特点：**
- 简化配置，启动速度快
- 每次启动都是全新浏览器，无历史数据
- 无 Cookie、无缓存、无登录状态
- 适合已注册账号的登录验证

### 注册模式浏览器配置

```python
browser = p.chromium.launch(
    headless=headless,
    args=[
        '--disable-blink-features=AutomationControlled',  # 隐藏自动化标识
        '--no-sandbox',
        '--disable-dev-shm-usage',                        # 避免共享内存问题
        '--disable-infobars',                             # 禁用信息栏
        '--window-size=1920,1080',
        '--start-maximized',
    ]
)

context = browser.new_context(
    locale='zh-CN',
    timezone_id='Asia/Shanghai',
    proxy=proxy_settings,
)
page = context.new_page()
```

**反检测脚本注入：**

```javascript
// 1. 隐藏 webdriver 属性
Object.defineProperty(navigator, 'webdriver', {
    get: () => undefined
});

// 2. 伪造 Chrome 对象
window.chrome = {
    runtime: {},
    loadTimes: function() {},
    csi: function() {},
    app: {}
};

// 3. 修复 Permissions API
const originalQuery = window.navigator.permissions.query;
window.navigator.permissions.query = (parameters) => (
    parameters.name === 'notifications' ?
        Promise.resolve({ state: Notification.permission }) :
        originalQuery(parameters)
);

// 4. 伪造 WebGL 信息（关键！）
const getParameterProxyHandler = {
    apply: function(target, thisArg, args) {
        const param = args[0];
        if (param === 37445) {  // UNMASKED_VENDOR_WEBGL
            return 'Google Inc. (Apple)';
        }
        if (param === 37446) {  // UNMASKED_RENDERER_WEBGL
            return 'ANGLE (Apple, Apple M1, OpenGL 4.1)';
        }
        return Reflect.apply(target, thisArg, args);
    }
};
```

**特点：**
- 完整的反检测配置，降低被识别为自动化的风险
- 伪造 WebGL 渲染器信息（Binance 会检测）
- 固定 User-Agent 和视口大小
- 适合新账号注册

### 缓存预热模式（warmup_cache）

首次运行时自动执行，用于预热静态资源缓存：

```python
context = p.chromium.launch_persistent_context(
    user_data_dir=str(MASTER_CACHE_DIR),  # 持久化用户数据目录
    headless=headless,
    args=[
        '--disable-blink-features=AutomationControlled',
        '--no-sandbox',
        '--disable-dev-shm-usage',
        '--disable-gpu',
        '--disable-software-rasterizer',
        '--disk-cache-size=104857600',  # 100MB 磁盘缓存
    ],
    ignore_https_errors=True,
    proxy=proxy_settings,
)
```

**预热流程：**
1. 访问登录页 `https://accounts.binance.com/zh-CN/login`
2. 等待页面加载，下载静态资源
3. 访问注册页 `https://www.binance.com/zh-CN/register`
4. 等待页面加载
5. 清除 Cookie（保留缓存）
6. 关闭浏览器

**缓存存储位置：**
```
.browser_cache/
  master/                    # 主缓存模板（预热生成）
    Default/
      Cache/Cache_Data/      # Chromium 磁盘缓存
  local_cache/               # 应用层缓存（JS/CSS 文件）
    index.json               # 缓存索引
    <md5_hash>               # 缓存文件
```

### 浏览器指纹检测点

Binance 会检测以下浏览器指纹：

| 检测项 | 说明 | 应对措施 |
|--------|------|----------|
| `navigator.webdriver` | 自动化标识 | 设为 undefined |
| `window.chrome` | Chrome 特有对象 | 伪造完整对象 |
| WebGL Vendor/Renderer | 显卡信息 | 伪造为真实显卡 |
| User-Agent | 浏览器标识 | 使用真实 Chrome UA |
| 视口大小 | 窗口尺寸 | 固定 1920x1080 |
| 时区/语言 | 地区信息 | 设为 Asia/Shanghai |
| Permissions API | 权限查询 | 修复异常行为 |

---

## 登录/注册流程

### 状态机驱动

`flows.py` 使用 URL 驱动的状态机，每次迭代检查当前 URL 决定执行什么操作：

| URL 路径 | 阶段 | 操作 |
|----------|------|------|
| `/login` | 登录首页 | 输入邮箱 → 点击继续 → 处理验证码 |
| `/login/password` | 密码页 | 输入密码 → 点击继续 → 处理验证码 |
| `/login/mfa` | MFA 验证 | IMAP 获取邮件验证码 → 填充 → 提交 |
| `/login/stay-signed-in` | 保持登录 | 点击"是" |
| `/my/*` | Dashboard | 登录成功，提取 Cookie |
| `/register` | 注册首页 | 输入邮箱 → 勾选协议 → 点击继续 |
| `/register/register-set-password` | 设置密码 | 输入密码 → 点击继续 |
| `/register/verification` | 邮件验证 | IMAP 获取验证码 → 填充 → 提交 |

### 状态机参数

```python
# flows.py 顶部常量
MAX_TOTAL_ITERATIONS = 50   # URL 状态机最大总迭代次数
MAX_URL_RETRIES = 10        # 单个 URL 状态最大重试次数
MAX_CAPTCHA_FAILS = 3       # 验证码最大连续失败次数
MAX_MFA_RETRIES = 3         # MFA 最大重试次数
```

每个阶段都会自动检测并处理：
- 验证码弹窗（滑块/点击）
- 风控错误页面
- 白屏检测与刷新
- 弹窗关闭（Cookie 弹窗、"已知晓"按钮等）
- 未注册检测（返回 `"need_register"` 状态）

### 返回值说明

| 返回值 | 含义 | CLI 处理 |
|--------|------|----------|
| `True` | 成功 | 计入成功数 |
| `False` | 失败 | 重试（最多 3 次） |
| `"rate_limited"` | IP 被风控 | 不重试 |
| `"need_register"` | 账号未注册 | 不重试，计入未注册数 |
| `"already_registered"` | 账号已注册 | 不重试，计入已注册数 |

### 风控检测

以下关键词触发风控处理：
- `网络连接失败`、`操作失败`、`PRECHECK`
- `cap_too_many_attempts`、`208075`、`208061`
- `认证失败，请刷新页面后重试`

---

## AI 验证码识别

通过 OpenRouter API 调用视觉 AI 模型识别验证码，支持两种类型：

### 1. 点击验证码（3x3 图片网格）

**识别流程：**
1. 截图验证码容器（`.bcap-modal`）
2. 提取提示文字（如"请点击包含猫的图片"）
3. 发送截图 + 提示词给 AI
4. AI 返回需要点击的位置坐标
5. 按坐标点击对应图片
6. 点击"验证"按钮确认

**AI 返回格式：**
```json
{"positions": [[1,2], [2,3], [3,1]]}
```

### 2. 滑块验证码

**识别流程：**
1. 截图滑块背景图（`.bs-main-image`）
2. 获取图片宽度
3. 发送截图给 AI，识别缺口位置
4. 计算滑动距离 = `gap_x - puzzle_x`
5. 模拟人类滑动（缓动函数 + Y轴抖动 + 随机步数）
6. 等待服务器验证

**AI 返回格式：**
```json
{"gap_x": 185}
```

### 滑块拖动模拟

```python
# 缓动函数：先快后慢
eased = progress * (2 - progress)

# 20-30 个随机步数
steps = random.randint(20, 30)

# Y 轴随机抖动 ±0.5px
jitter_y = random.uniform(-0.5, 0.5)

# 每步间隔 10-30ms
time.sleep(random.uniform(0.01, 0.03))
```

### 验证码重试策略

- 每轮最多 `max_attempts_per_round` 次尝试
- 最多 `max_rounds` 轮（每轮重新加载页面）
- AI 调用失败自动重试 3 次，间隔 1 秒
- 检测到风控签名时冷却 20-60 秒
- 验证码消失需连续 5 次检测确认（间隔 500ms）

---

## 本地缓存系统

### 工作原理

通过 Playwright 的 `page.route()` 拦截请求，对静态资源使用 `route.fulfill()` 直接从本地返回，零网络开销。

**缓存范围：**
- `bin.bnbstatic.com/static` 的 JS/CSS
- `public.bnbstatic.com/unpkg` 的 JS/CSS

### Master/Worker 架构

```
.browser_cache/
  master/          # 主缓存模板（预热生成）
  local_cache/     # 应用层缓存（JS/CSS 文件）
  worker_0/        # Worker 0 的浏览器 profile（运行时从 master 复制）
  worker_1/        # Worker 1 的浏览器 profile
```

### 缓存 vs 普通模式

| 特性 | `cache.enabled: true` | `cache.enabled: false` |
|------|----------------------|------------------------|
| 浏览器启动 | `launch_persistent_context` | `launch` (普通模式) |
| 请求拦截 | `page.route("**/*")` | 无 |
| 静态资源缓存 | `route.fulfill()` 本地返回 | 无 |
| 流量消耗 | 首次 ~35MB，后续 ~8MB | 每次 ~69MB |

---

## 流量监控

每次运行结束后自动输出流量统计：

```
============================================================
流量统计摘要
============================================================
实际网络流量: 8.23MB
本地缓存命中: 30.85MB
请求数: 345 (网络: 129, 缓存: 216)
```

---

## 输出结果

成功账号写入 `output/registered_accounts.json`：

```json
{
  "accounts": [
    {
      "name": "账号_user1",
      "email": "user1@example.com",
      "cookie": "cr00=xxx; csrftoken=xxx; ...",
      "csrftoken": "md5_hash_of_cr00",
      "enabled": true
    }
  ]
}
```

---

## 日志系统

```
logs/
  2024-01-15.log                    # 每日全局摘要日志
  failures/
    user1_at_example_com_20240115.log  # 失败账号详细日志
```

---

## 常见问题

### `208075` / `PRECHECK` / `认证失败`

IP 被风控，解决方案：
- 降低并发数（`max_workers: 1-2`）
- 使用高质量独立代理
- 关闭缓存模式（`cache.enabled: false`）排除缓存干扰

### 验证码识别失败

- 检查 `debug/` 目录的截图确认 AI 识别结果
- 尝试更换 AI 模型
- 增加 `max_attempts_per_round` 和 `max_rounds`

### 邮件验证码获取超时

- 确认 IMAP 配置正确
- 检查邮箱是否开启了 IMAP 访问
- 超时时间默认 90 秒

---

## 推荐配置

| 场景 | max_workers | cache | proxy | headless |
|------|-------------|-------|-------|----------|
| 调试 | 1 | false | 按需 | false |
| 少量账号 | 1-2 | true | 按需 | false |
| 批量处理 | 2-3 | true | 建议开启 | true |

不建议 `max_workers >= 5`，风控触发概率明显增加。
