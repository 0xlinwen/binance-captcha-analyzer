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
│  cache=true:  launch_persistent_context + route拦截      │
│  cache=false: launch 普通模式（无拦截，干净环境）           │
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

每个阶段都会自动检测并处理：
- 验证码弹窗（滑块/点击）
- 风控错误页面
- 白屏检测与刷新
- 弹窗关闭（Cookie 弹窗、"已知晓"按钮等）
- 未注册检测（自动切换到注册流程）

### 风控检测

以下关键词触发风控处理：
- `网络连接失败`、`操作失败`、`PRECHECK`
- `cap_too_many_attempts`、`208075`
- `认证失败，请刷新页面后重试`

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

**AI 提示词：**

```
这是一个验证码图片，是一个 3x3 的图片网格。
提示文字是："{prompt_text}"

请分析这个验证码，告诉我应该点击哪些图片。
图片位置用行列表示，从左上角开始：
- 第1行第1列 = (1,1), 第1行第2列 = (1,2), 第1行第3列 = (1,3)
- 第2行第1列 = (2,1), 第2行第2列 = (2,2), 第2行第3列 = (2,3)
- 第3行第1列 = (3,1), 第3行第2列 = (3,2), 第3行第3列 = (3,3)

请只返回 JSON 格式，例如：
{"positions": [[1,2], [2,3], [3,1]]}

不要返回其他内容，只返回 JSON。
```

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

**AI 提示词：**

```
分析这个滑块验证码图片。

图片信息：
- 总宽度：{image_width}px
- 左侧 0-60px：拼图块（puzzle piece），宽度固定 60px
- 背景中有一个缺口（gap），形状与拼图块相同

任务：找到缺口左边缘的 x 坐标（像素值）

提示：
- 缺口通常比周围区域略暗或有明显边缘
- 缺口宽度约 60px
- 缺口位置通常在 100-250px 范围内

返回 JSON 格式：
{"gap_x": 缺口左边缘x坐标}
```

**AI 返回格式：**
```json
{"gap_x": 185}
```

### 滑块拖动模拟

`simulate_human_drag` 模拟真实人类滑动行为：

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
- 点击验证码支持第二轮验证（等待新图片加载后继续）

### 调试输出

滑块验证码会在 `debug/` 目录生成调试图片：
- `slider_xxx_bg.png` - 原始背景图
- `slider_xxx_ruler.png` - 带刻度尺的背景图
- `slider_xxx_ai_result.png` - AI 识别结果标注图

## 本地缓存系统

### 工作原理

通过 Playwright 的 `page.route()` 拦截请求，对静态资源使用 `route.fulfill()` 直接从本地返回，零网络开销。

**缓存范围：**
- `bin.bnbstatic.com/static` 的 JS/CSS
- `public.bnbstatic.com/unpkg` 的 JS/CSS

**缓存存储：** `.browser_cache/local_cache/`
- `index.json` - 缓存索引（URL → 文件映射）
- MD5 hash 文件 - 响应体

### Master/Worker 架构

```
.browser_cache/
  master/          # 主缓存模板（预热生成）
  local_cache/     # 应用层缓存（JS/CSS 文件）
  worker_0/        # Worker 0 的浏览器 profile（运行时从 master 复制）
  worker_1/        # Worker 1 的浏览器 profile
```

- 首次运行自动预热：访问登录页和注册页，下载静态资源
- 每个 Worker 启动时从 master 复制浏览器 profile
- 运行结束后，新增的缓存文件同步回 master（排除验证码相关文件）
- Worker 目录用完即删

### 缓存 vs 普通模式

| 特性 | `cache.enabled: true` | `cache.enabled: false` |
|------|----------------------|------------------------|
| 浏览器启动 | `launch_persistent_context` | `launch` (普通模式) |
| 请求拦截 | `page.route("**/*")` | 无 |
| 静态资源缓存 | `route.fulfill()` 本地返回 | 无 |
| 浏览器 profile | `.browser_cache/worker_N/` | 临时目录（自动管理） |
| 流量消耗 | 首次 ~35MB，后续 ~8MB | 每次 ~69MB |
| 适用场景 | 批量处理，节省流量 | 调试，避免缓存干扰 |

## 流量监控

每次运行结束后自动输出流量统计：

```
============================================================
流量统计摘要
============================================================
实际网络流量: 8.23MB
本地缓存命中: 30.85MB
请求数: 345 (网络: 129, 缓存: 216)

按资源类型 (仅网络流量):
  fetch              5.12MB (62.2%)
  script             2.01MB (24.4%)
  ...

按域名 (仅网络流量, Top 10):
  www.binance.com                            3.21MB (39.0%)
  accounts.binance.com                       2.15MB (26.1%)
  ...
```

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

CSRF Token 生成逻辑：`md5(cr00 cookie value)`

## 日志系统

```
logs/
  2024-01-15.log                    # 每日全局摘要日志
  failures/
    user1_at_example_com_20240115.log  # 失败账号详细日志（含完整操作记录）
```

- 全局日志：记录每个账号的成功/失败、耗时、阶段
- 失败日志：仅在失败时写入，包含完整的操作步骤和页面状态

## 常见问题

### `208075` / `认证失败，请刷新页面后重试`

IP 被风控，解决方案：
- 降低并发数（`max_workers: 1-2`）
- 账号之间加随机延迟
- 使用高质量独立代理
- 关闭缓存模式（`cache.enabled: false`）排除缓存干扰

### 验证码识别失败

- 检查 `debug/` 目录的截图确认 AI 识别结果
- 尝试更换 AI 模型（修改 `models` 配置）
- 增加 `max_attempts_per_round` 和 `max_rounds`

### 邮件验证码获取超时

- 确认 IMAP 配置正确（host/port/密码）
- 检查邮箱是否开启了 IMAP 访问
- 超时时间默认 90 秒，Binance 邮件通常 10-30 秒内到达

### 页面白屏

自动检测并刷新。如果频繁出现：
- 检查网络/代理连接
- 关闭缓存模式测试

## 推荐配置

| 场景 | max_workers | cache | proxy | headless |
|------|-------------|-------|-------|----------|
| 调试 | 1 | false | 按需 | false |
| 少量账号 | 1-2 | true | 按需 | false |
| 批量处理 | 2-3 | true | 建议开启 | true |

不建议 `max_workers >= 5`，风控触发概率明显增加。
