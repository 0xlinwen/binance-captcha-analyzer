# Binance Captcha Analyzer

Binance 登录/注册自动化工具，基于 Playwright 浏览器自动化 + OpenRouter AI 验证码识别 + IMAP 邮箱验证码提取。

## 核心功能

- 自动登录/注册 Binance 账号
- AI 识别滑块验证码和点击验证码（通过 OpenRouter API）
- IMAP 自动提取邮箱 MFA 验证码（支持 Outlook API 拉码）
- 多进程并发处理多个账号
- 本地静态资源缓存（减少网络流量）
- 浏览器指纹随机化（Mac Apple Silicon 配置池）
- 完整反检测脚本注入（WebGL/屏幕/硬件/媒体设备伪造）
- 自动提取 Cookie 和 CSRF Token
- 成功/失败账号分类日志

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
│     浏览器启动 / 缓存管理 / Cookie提取 / 反检测注入        │
│                                                         │
│  登录/注册模式:  完整反检测配置 + WebGL 伪造              │
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
│ 输入/点击/   │  │ 滑块/点击    │  │ Outlook API拉码  │
│ 跳转/弹窗    │  │ 重试/冷却    │  │ 提取/填充/提交    │
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
main.py                                # 兼容入口
src/binance_analyzer/
  __init__.py
  cli.py                               # 主入口、并发调度、信号处理、缓存预热
  config.py                            # 配置加载与默认值
  orchestrator.py                      # 单账号编排（浏览器启动、缓存、Cookie提取）
  flows.py                             # 登录/注册状态机（URL驱动）
  web_actions.py                       # 页面交互（输入邮箱/密码、点击按钮、弹窗处理）
  captcha_solver.py                    # 验证码执行（滑块拖动、点击图片、重试策略）
  captcha_ai.py                        # OpenRouter AI 调用与 JSON 解析
  prompts.py                            # AI 验证码识别提示词模板
  email_imap.py                        # IMAP 邮件验证码提取 + Outlook API 拉码
  storage.py                           # 账号文件读取、结果写入（文件锁）
  local_cache.py                       # 应用层静态资源缓存
  traffic_monitor.py                   # 流量统计（按类型/域名/请求）
  fingerprint.py                       # 浏览器指纹随机化（UA/时区/WebGL）
  logger.py                            # 统一日志管理（每日日志、成功/失败分类）
  constants.py                         # 全局常量（超时、重试、日志格式等）
  utils.py                             # 工具函数（重试策略、弹窗处理、文件名清理）
  exceptions.py                        # 自定义异常层级（可重试/不可重试分类）
output/
  success_accounts.txt                 # 成功的账号
  failed_accounts.txt                  # 失败的账号
  registered_accounts.json             # 成功账号的 Cookie 和 CSRF Token
  logs/
    binance_YYYY-MM-DD.log             # 每日主日志
    success/YYYY-MM-DD.log             # 当日成功账号列表
    failure/YYYY-MM-DD.log             # 当日失败账号列表
```

## 安装

```bash
pip install -r requirements.txt
playwright install chromium
```

依赖：
- `playwright` - 浏览器自动化
- `requests` - OpenRouter API / Outlook 邮件 API 调用
- `opencv-python` + `numpy` - 验证码截图调试标注
- `psutil` - 进程管理（信号处理时终止子进程）

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

  // === 模式 ===
  "mode": "login",                         // 运行模式：login / register（别名：signup, sign_up）
  "max_login_retries": 3,                  // 单账号最大重试次数
  "debug_mode": false,                     // 调试模式

  // === 浏览器 ===
  "headless": false,                       // 是否无头模式
  "max_workers": 2,                        // 并发进程数

  // === 本地缓存 ===
  "cache": {
    "enabled": true                        // 是否启用本地静态资源缓存
  },

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
    "max_attempts_per_round": 5,           // 每轮最大尝试次数（默认 5）
    "max_rounds": 3,                       // 最大轮次（默认 3，每轮重新加载页面）
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
python main.py

# 刷新缓存（删除旧缓存并重新预热）
python main.py --refresh-cache
```

---

## 浏览器环境配置详解

### 登录模式 vs 注册模式

| 特性 | 登录模式 | 注册模式 |
|------|----------|----------|
| 浏览器启动 | `chromium.launch()` + `new_context()` | `chromium.launch()` + `new_context()` |
| 反检测脚本 | 完整 11 项伪造 | 完整 11 项伪造 |
| User-Agent | 随机 Chrome 138-145 | 随机 Chrome 138-145 |
| 视口大小 | 随机（基于指纹配置） | 随机（基于指纹配置） |
| 时区/语言 | 随机 | 随机 |
| WebGL 伪造 | 随机 Mac Apple Silicon 显卡 | 随机 Mac Apple Silicon 显卡 |
| 屏幕/硬件伪造 | 完整伪造 | 完整伪造 |

### 指纹随机化

每个 worker 启动时生成随机指纹，避免多窗口被关联检测：

```python
# fingerprint.py
CHROME_VERSIONS = ['138.0.0.0', '140.0.0.0', '141.0.0.0', '142.0.0.0',
                   '143.0.0.0', '144.0.0.0', '145.0.0.0']

TIMEZONES = ['Asia/Shanghai', 'Asia/Hong_Kong', 'Asia/Singapore']

LOCALES = ['zh-CN', 'en-US', 'zh-TW']

# 4 种 Mac Apple Silicon 配置
FINGERPRINT_PROFILES = [
    'mac_m4_real'   # M4, 10核, 1470x956, DPR=2
    'mac_m1_8core'  # M1, 8核,  1440x900, DPR=2
    'mac_m2_8core'  # M2, 8核,  1512x982, DPR=2
    'mac_m3_pro'    # M3 Pro, 12核, 1512x982, DPR=2
]
```

启动时会打印指纹信息：
```
[Worker-0] 指纹: UA=...Chrome/142.0.0.0 Safari/537.36 | TZ=Asia/Hong_Kong | Screen=1512x982 | DPR=2
```

### 反检测脚本（11 项）

登录和注册模式均注入完整的反检测初始化脚本：

| # | 伪造项 | 说明 |
|---|--------|------|
| 1 | `navigator.webdriver` | 设为 undefined，隐藏自动化标识 |
| 2 | `navigator.platform` | 伪造为 MacIntel |
| 3 | `navigator.language/languages` | 随机多语言数组 |
| 4 | 硬件信息 | `hardwareConcurrency` + `deviceMemory` |
| 5 | 屏幕信息 | width/height/availWidth/availHeight/colorDepth/pixelDepth/DPR |
| 6 | `window.chrome` | 完整伪造 runtime/loadTimes/csi/app |
| 7 | Permissions API | 修复 notifications 状态查询 |
| 8 | WebGL | 完整伪造 vendor/renderer（含 OffscreenCanvas + Worker） |
| 9 | Canvas 噪声 | frame-aware seed 扰动（toDataURL/toBlob/getImageData） |
| 10 | 媒体设备 | 伪造摄像头/麦克风/扬声器 |
| 11 | Automation 属性 | 删除 cdc_ 前缀变量 |

### 缓存预热模式

首次运行时自动执行，用于预热静态资源缓存：

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
  worker_N/                  # Worker N 的浏览器 profile（运行时从 master 复制）
```

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

### 返回值说明

| 返回值 | 含义 | CLI 处理 |
|--------|------|----------|
| `True` | 成功 | 计入成功数 |
| `False` | 失败 | 重试（最多 max_login_retries 次） |
| `"rate_limited"` | IP 被风控 | 不重试 |
| `"need_register"` | 账号未注册 | 不重试，计入未注册数 |
| `"already_registered"` | 账号已注册 | 不重试，计入已注册数 |
| `"imap_auth_failed"` | IMAP 认证失败 | 不重试，计入 IMAP 失败数 |

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
5. 模拟人类滑动（多种缓动函数 + Y轴抖动 + 随机步数）
6. 等待服务器验证

**AI 返回格式：**
```json
{"gap_x": 185}
```

### 滑块拖动模拟

支持多种缓动函数随机选择：
- `ease_out` - 先快后慢
- `ease_in_out` - 慢-快-慢
- `linear_with_pause` - 匀速带随机暂停

```python
# 20-30 个随机步数
steps = random.randint(20, 30)
# Y 轴随机抖动 ±0.5px
jitter_y = random.uniform(-0.5, 0.5)
# 每步间隔 10-30ms
time.sleep(random.uniform(0.01, 0.03))
```

### 验证码重试策略

- 每轮最多 `max_attempts_per_round` 次尝试（默认 5）
- 最多 `max_rounds` 轮（默认 3，每轮重新加载页面）
- AI 调用失败自动重试 3 次，带指数退避
- 检测到风控签名时冷却 20-60 秒
- 验证码消失需连续检测确认

---

## 邮箱验证码

### IMAP 模式

标准 IMAP 连接获取验证码，支持：
- 自动检测 Binance 发件人
- 6 位验证码提取（支持中文/繁体/英文关键词）
- HTML 邮件解析（含 `<strong>` 标签内验证码）
- 过滤时间戳误匹配
- 认证失败自动重试（最多 5 次）

### Outlook API 模式

`@outlook.com` 邮箱自动使用外部 API 拉码：
- 轮询间隔 5 秒，超时 60 秒
- 永久性错误（密码错误等）连续 3 次后停止
- 从 subject 和 content 中提取验证码

---

## 本地缓存系统

通过 Playwright 的 `page.route()` 拦截请求，对静态资源使用 `route.fulfill()` 直接从本地返回。

**缓存范围：**
- `bin.bnbstatic.com/static` 的 JS/CSS
- `public.bnbstatic.com/unpkg` 的 JS/CSS

### 缓存 vs 普通模式

| 特性 | `cache.enabled: true` | `cache.enabled: false` |
|------|----------------------|------------------------|
| 浏览器启动 | `launch` + `new_context` | `launch` + `new_context` |
| 请求拦截 | `page.route("**/*")` | 无 |
| 静态资源缓存 | `route.fulfill()` 本地返回 | 无 |

---

## 日志系统

```
output/logs/
  binance_YYYY-MM-DD.log               # 每日主日志（完整执行过程）
  success/
    YYYY-MM-DD.log                      # 当日成功账号列表（含 already_registered）
  failure/
    YYYY-MM-DD.log                      # 当日失败账号列表
```

运行结束后自动输出当日汇总统计（总数、成功、失败、IP风控、成功率）。

---

## 异常处理

自定义异常层级，支持可重试/不可重试分类：

- `CaptchaError` - 验证码相关错误
- `IMAPError` → `IMAPAuthFailed` / `IMAPConnectionError` / `IMAPTimeout`
- `BrowserError` - 浏览器相关错误
- `ConfigError` - 配置错误

`utils.retry_with_backoff()` 提供指数退避重试，带 jitter 随机化。

---

## 常见问题

### `208075` / `PRECHECK` / `认证失败`

IP 被风控，解决方案：
- 降低并发数（`max_workers: 1-2`）
- 使用高质量独立代理
- 关闭缓存模式（`cache.enabled: false`）排除缓存干扰

### CloudFront 403 / IP 地区限制

以下情况会被识别为 IP 级别拦截，直接失败关闭窗口，不重试：
- CloudFront 403 ERROR（CDN 层拦截）
- "无法为该地区的用户提供服务"（IP 被识别为美国等受限地区，错误码 200004431）

解决方案：更换代理 IP 到非受限地区。

### 验证码识别失败

- 检查 `debug/` 目录的截图确认 AI 识别结果
- 尝试更换 AI 模型
- 增加 `max_attempts_per_round` 和 `max_rounds`

### 邮件验证码获取超时

- 确认 IMAP 配置正确
- 检查邮箱是否开启了 IMAP 访问
- Outlook 邮箱会自动使用 API 模式
- 超时时间默认 90 秒

### IMAP 认证失败

- 检查邮箱密码是否正确
- 确认邮箱已开启 IMAP 服务
- 返回 `"imap_auth_failed"` 后不会重试

---

## 推荐配置

| 场景 | max_workers | cache | proxy | headless |
|------|-------------|-------|-------|----------|
| 调试 | 1 | false | 按需 | false |
| 少量账号 | 1-2 | true | 按需 | false |
| 批量处理 | 2-3 | true | 建议开启 | true |

不建议 `max_workers >= 5`，风控触发概率明显增加。
