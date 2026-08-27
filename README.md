# Binance Captcha Analyzer

Binance 登录/注册自动化工具，基于 Playwright 浏览器自动化 + OpenRouter AI 验证码识别 + IMAP 邮箱验证码提取。

## 核心功能

- 自动登录/注册 Binance 账号
- AI 识别复选框、点击图片和滑块验证码（通过 OpenRouter API）
- IMAP 自动提取邮箱 MFA 验证码（支持 Outlook API 拉码）
- 多进程并发处理多个账号
- 本地静态资源缓存（减少网络流量）
- 浏览器指纹随机化（Mac Apple Silicon 配置池）
- 完整反检测脚本注入（WebGL/屏幕/硬件/媒体设备伪造）
- 自动提取 Cookie 和 CSRF Token
- 成功/失败账号分类日志

## 整体架构

```
提交端（CLI / 前端 / 其他系统）
        │ POST /api/login-jobs，mode=login 或 register
        ▼
Linux Cloud API（src/binance_cloud/api.py）
  读取 config/cloud.json
  创建任务、状态和账号记录 ────────────────► SQLite：data/binance.db
        │ GET /health 后校验 protocol_version
        │ POST /worker/execute-login
        ▼
Windows Worker（src/binance_cloud/worker.py）
  读取 config/worker.json + config/automation.json
  worker_id 代表这一台 Windows 节点
        │ worker_max_workers 控制整台节点的账号并发
        ▼
浏览器自动化（register_account）
  登录/注册 -> 邮箱 MFA -> 导出 Cookie / CSRF / 过期时间
        │ POST /api/worker/callback
        │ 失败时持久化到 data/runtime/callback_outbox.json 后重试
        ▼
Linux Cloud API ────────────────────────────────► SQLite 凭证和任务结果
        │                                            credentials / execution_logs
        └── 任务组连续 5 次失败、全部完成 ───────► Lark Webhook（可选）
```

凭证只由 Linux SQLite 对外提供；Windows 保存的是自动化运行文件、回调重试队列和本地调试结果。每台机器的相对路径都以各自项目根目录为基准。

### 本地自动化流程

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
│          login_flow.py / register_flow.py (状态机)        │
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
│ web_actions  │  │   captcha/   │  │   email_imap     │
│ 页面交互动作  │  │ 可扩展验证码库 │  │ IMAP邮件验证码    │
│ 输入/点击/   │  │ 检测/提示词/  │  │ Outlook API拉码  │
│ 跳转/弹窗    │  │ solver/AI客户端│  │ 提取/填充/提交    │
└──────────────┘  └──────────────┘  └──────────────────┘
```

## 项目结构

```
main.py                                # 命令行启动入口
src/binance_analyzer/
  __init__.py
  cli.py                               # 主入口、并发调度、信号处理、缓存预热
  config.py                            # 配置加载与默认值
  orchestrator.py                      # 单账号编排（代理、浏览器、流程、Cookie）
  automation_driver.py                 # 登录/注册驱动抽象与统一结果封装
  credential_export.py                 # Cookie、CSRF、过期时间导出
  browser_context.py                   # 浏览器上下文、反检测脚本、subprocess 启动
  flows.py                             # 登录/注册共享状态机工具
  login_flow.py                        # 登录流程状态机
  register_flow.py                     # 注册流程状态机
  results.py                           # 账号处理状态模型
  page_signals.py                      # URL、风控、代理失败等页面信号检测
  web_actions.py                       # 页面交互（输入邮箱/密码、点击按钮、弹窗处理）
  captcha/                              # 可扩展验证码库
    types.py                            # 验证码类型与上下文
    detector.py                         # 页面验证码类型检测
    prompts.py                          # AI 验证码识别提示词模板
    ai_client.py                        # OpenRouter AI 调用与 JSON 解析
    solvers.py                          # checkbox/click/slider solver 与注册表
    service.py                          # 验证码求解主循环
  email_imap.py                        # IMAP 邮件验证码提取 + Outlook API 拉码
  account_storage.py                   # 账号队列与成功/失败结果文件
  proxy_ip_storage.py                  # 代理出口 IP 使用记录
  registered_account_storage.py        # registered_accounts.json 持久化
  screenshot_storage.py                # 截图清理
  proxy_integration.py                 # 业务层代理运行时适配
  local_cache.py                       # 应用层静态资源缓存
  traffic_monitor.py                   # 流量统计（按类型/域名/请求）
  fingerprint.py                       # 浏览器指纹随机化（UA/时区/WebGL）
  logger.py                            # 失败账号详细日志与运行统计
  constants.py                         # 全局常量（超时、重试、日志格式等）
  utils.py                             # 工具函数（重试策略、弹窗处理、文件名清理）
  exceptions.py                        # 自定义异常层级（可重试/不可重试分类）
src/proxy_forwarder/
  config.py                            # 代理配置解析与质量检查配置
  proxy_utils.py                       # 代理文本解析、URL 构建、客户端配置
  runtime.py                           # 代理探测、动态代理获取、gost 生命周期
  logging.py                           # 代理运行时日志

src/binance_cloud/
  api.py                               # Linux 云端任务 API 与回调
  database.py                          # SQLite 任务、凭证、日志持久化
  worker.py                            # Windows 执行 Worker
  protocols.py                         # 云端/Worker 请求协议
data/
  accounts/pending.txt                 # 待处理账号队列
  results/
    success_accounts.txt               # 成功账号简表
    failed_accounts.txt                # 失败账号简表
    registered_accounts.json           # 完整凭据、Cookie 和 CSRF Token
  runtime/
    used_proxy_ips.txt                 # 已使用动态代理出口 IP
    creator_api_quota.json             # Creator API 提取配额状态
    callback_outbox.json               # Windows 回调重试队列
artifacts/
  debug/creator_api/                   # Creator API 提取失败证据
logs/
  failures/                            # 失败账号详细日志
```

## 安装

### 云端 API 与 Windows Worker（实验性服务入口）

两端都应在项目根目录创建虚拟环境。Linux 云端额外安装服务依赖：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements-server.txt
```

Linux 云端启动 API：

```bash
PYTHONPATH=src uvicorn binance_cloud.api:app --host 0.0.0.0 --port 8000
```

Linux 的业务配置统一写在 `config/cloud.json`（数据库路径、Windows Worker 地址、回调地址、协议版本、租约时长和 Lark Webhook）。数据库父目录需由运行用户具备写权限。鉴权仍可按需通过 `BINANCE_WORKER_TOKEN`、`BINANCE_CALLBACK_TOKEN` 启用。

直接对公网暴露 API 时使用上面的 `--host 0.0.0.0`。`deploy/linux/binance-cloud.service` 为反向代理部署准备，默认只监听 `127.0.0.1:8000`；使用该模板时必须由 Nginx/Caddy 等反向代理把公网域名转发到该地址。不要在只监听 `127.0.0.1` 的情况下直接使用 `http://Linux公网IP:8000`。

Windows 启动执行服务（需在 Worker 目录准备 `config/automation.json` 和 `config/worker.json`）：

```powershell
$env:BINANCE_WORKER_BASE_DIR = "C:\\binance-worker"
python -m uvicorn binance_cloud.worker:app --host 0.0.0.0 --port 8100
```

本地直接调试 Worker、暂时没有 Linux 任务状态接口时，可在 Windows 的
`config/worker.json` 中设置 `"debug_mode": true`。调试模式会跳过任务状态查询和心跳，
但仍会执行登录/注册；如果 `config/worker.json` 配置了 `callback_url`，执行结果仍会回调。
生产环境请保持 `debug_mode` 为 `false`，以启用取消检查和租约心跳。

Windows Worker 需安装项目完整依赖（`requirements.txt`）并执行
`playwright install chromium`；`BINANCE_WORKER_BASE_DIR` 必须指向包含
`config/automation.json`、账号/输出目录的 Worker 工作目录。若 Linux 启用了
`BINANCE_WORKER_TOKEN`，Windows 端也必须设置同名变量；回调鉴权可另设
`BINANCE_CALLBACK_TOKEN`。

`deploy/windows/start_worker.ps1` 中的 `C:\binance-captcha-analyzer` 是示例路径；复制该模板前，必须将 `PYTHONPATH`、`BINANCE_WORKER_BASE_DIR` 和 `Set-Location` 同步替换为 Windows 实际部署目录。README 中的 `C:\binance-worker` 仅是等价示例目录。

Windows `config/worker.json` 可设置 `worker_max_workers` 控制同一任务内的账号并发数，
例如 `2` 表示该 Windows Worker 全部任务最多同时执行两个账号，其余账号排队；默认值为 `1`（串行）。
Linux 每次派发前会检查 Windows `/health` 的 `protocol_version`，并在任务请求中携带相同版本；版本不一致时任务不执行并记录为派发失败。Linux 与 Windows 必须部署同一版本代码。
版本不兼容时，如果 Linux `config/cloud.json` 配置了 `lark.webhook_url`，Linux 会发送一次系统告警；未配置时不发送外部通知，但错误会写入任务状态和执行日志，任务仍按重试策略处理。

接口闭环为 `POST /api/login-jobs` -> Windows `POST /worker/execute-login` -> Linux `POST /api/worker/callback`。请求体的 `mode` 可选 `login` 或 `register`，同一 Worker 可并行处理两种独立流程；当前服务入口使用 SQLite，长字段（Cookie、密码、Token）使用 `TEXT`；Windows Worker 复用现有 `register_account` 流程。

当前默认不启用 API/Worker 鉴权；设置 `BINANCE_WORKER_TOKEN` 或 `BINANCE_CALLBACK_TOKEN` 后分别启用 Worker 请求和回调校验。Cookie 仅在登录成功时保存，不执行自动在线检查或状态更新。创建任务前必须在 `config/cloud.json` 配置 `windows_worker_url` 与 `callback_url`；Worker 心跳会续租当前账号，取消任务后停止后续账号执行。

Windows 在回调前会把结果写入 `data/runtime/callback_outbox.json`。Linux 短暂不可达时，该文件会按退避间隔持续重试，Worker 重启后仍会恢复投递；回调成功才删除对应条目。每个凭证附带 `credential_updated_at`，Linux 只接受较新的凭证，避免 Cookie 过期重登后旧回调迟到并覆盖新 Cookie。

附带部署模板：`deploy/linux/binance-cloud.service` 和 `deploy/windows/start_worker.ps1`。Linux 后台会自动回收过期任务租约、标记离线 Worker、重新派发可重试任务。数据库支持 WAL、备份接口 `/api/database/backup`、任务取消接口 `/api/login-jobs/{id}/cancel` 和日志清理接口 `/api/logs?days=30`。

### Windows PowerShell

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
playwright install chromium
```

### macOS / Linux

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
playwright install chromium
```

依赖：
- `playwright` - 浏览器自动化
- `requests` - OpenRouter API / Outlook 邮件 API 调用
- `opencv-python` + `numpy` - 历史验证码图像处理依赖，当前主流程不直接依赖其完成坐标点击
- `psutil` - 进程管理（信号处理时终止子进程）
- `portalocker` - Windows、macOS 和 Linux 通用的多进程文件锁

项目不依赖 Unix 专属的 `fcntl`。Windows 环境无需、也不能执行 `pip install fcntl`。

## 配置

复制角色化示例为对应的 JSON 配置，按需修改。根目录 `config.json` 可暂时保留作迁移核对，但本地 CLI 和 Windows Worker 都不会读取它：

```bash
cp config/automation.example.json config/automation.json
# Windows Worker 额外需要
cp config/worker.example.json config/worker.json
# Linux Cloud 额外需要
cp config/cloud.example.json config/cloud.json
```

### 配置项说明

三份配置文件职责严格分离：

| 配置文件 | 部署位置 | 读取者 | 负责内容 | 不应包含 |
|---|---|---|---|---|
| `config/automation.json` | 本地 CLI、Windows Worker | `cli.py`、Worker | 浏览器、登录/注册、邮箱、验证码、Creator API、代理、本地账号文件和结果文件 | Cloud URL、Worker ID、Lark、SQLite |
| `config/worker.json` | Windows Worker | `worker.py` | Windows 节点 ID、节点总并发、调试模式、独立回调地址、协议版本 | 浏览器和代理具体参数、SQLite、Lark |
| `config/cloud.json` | Linux Cloud | `api.py` | SQLite、Windows Worker 地址、云端回调公网地址、租约、Lark、协议版本 | 浏览器、邮箱、验证码、账号文件 |

根目录 `config.json` 仅供迁移核对，运行时不会读取，也没有回退逻辑。

#### `worker.json`（Windows 节点配置）

```jsonc
{
  "protocol_version": "1", // 必须与 Linux cloud.json 完全相同
  "worker_id": "windows-01", // Windows 节点稳定标识，不是账号、线程或任务 ID
  "worker_max_workers": 1, // 整台 Windows Worker 同时运行的账号上限
  "debug_mode": false, // true 时跳过 Linux 的任务状态查询和心跳
  "callback_url": "https://linux.example.com/api/worker/callback" // 独立调试和 Worker 注册时使用
}
```

Linux 派发的任务会在请求中携带 `cloud.json.callback_url`，因此生产任务的回调目标以 Linux 配置为准；`worker.json.callback_url` 用于独立调用 Worker、`/worker/register` 和没有 Linux 派发的调试场景。`worker_id` 用于 Linux 记录这台节点的心跳和当前任务；每个账号的浏览器隔离目录由任务和明细 ID 自动生成。

#### `cloud.json`（Linux 服务配置）

```jsonc
{
  "protocol_version": "1", // 必须与每台 Windows Worker 的 worker.json 相同
  "database_path": "data/binance.db", // 相对 Linux 项目根目录解析
  "windows_worker_url": "http://Windows公网IP:8100", // Linux 能访问的 Worker 地址
  "callback_url": "https://Linux域名/api/worker/callback", // Windows 能访问的 Linux 回调地址
  "task_lease_seconds": 1800, // Worker 心跳租约秒数
  "lark": {"webhook_url": ""} // 可选：全局任务告警和完成通知
}
```

`windows_worker_url` 的网络方向是 Linux -> Windows；`callback_url` 的方向是 Windows -> Linux。两台机器都有公网 IP 时，Windows 应放行 Worker 监听端口（默认 `8100`），Linux 应放行反向代理或 Uvicorn 实际监听的回调端口。

#### `automation.json`（本地 CLI 与 Windows 自动化配置）

```jsonc
{
  // === 必填 ===
  "openrouter_api_key": "sk-or-v1-xxx",   // OpenRouter API Key（也可通过环境变量 OPENROUTER_API_KEY 设置）
  "models": ["google/gemini-3-flash-preview"],  // AI 模型列表，取第一个
  "imap_host": "imap.example.com",         // 邮箱 IMAP 服务器
  "imap_port": 993,                        // IMAP 端口
  "accounts_file": "data/accounts/pending.txt",      // 账号文件路径
  "output_file": "data/results/registered_accounts.json", // 输出文件路径

  // === 模式 ===
  "mode": "login",                         // 运行模式：login / register
  "mode_options": ["login", "register"],   // 仅供手动复制，不参与程序读取
  "max_login_retries": 3,                  // 单账号最大重试次数

  // === 浏览器 ===
  "headless": false,                       // 是否无头模式
  "max_workers": 2,                        // 并发进程数

  // === 运行时 ===
  "runtime": {
    "max_workers_default": 2,              // 未配置 max_workers 时的默认并发
    "retry_delay_min_sec": 20,             // 普通重试最小等待秒数
    "retry_delay_max_sec": 60,             // 普通重试最大等待秒数
    "proxy_retry_delay_min_sec": 10,       // 代理重试最小等待秒数
    "proxy_retry_delay_max_sec": 30        // 代理重试最大等待秒数
  },

  // === 本地缓存 ===
  "cache": {
    "enabled": true                        // 是否启用本地静态资源缓存
  },

  // === 代理 ===
  "proxy": {
    "enabled": false,
    "used_ips_file": "data/runtime/used_proxy_ips.txt",
    "mode": "dynamic",                     // dynamic / static
    "api_url": "https://proxy-api.example.com/gen?region=JP&count=1&proto=http",  // 动态 IP API
    "timeout_seconds": 15,
    "check_timeout_seconds": 15,
    "proxy_quality_check_enabled": true,      // 检查目标站点延迟，慢 IP 自动换新
    "proxy_quality_check_timeout_seconds": 10,
    "proxy_quality_check_max_latency_ms": 2500,
    "proxy_quality_check_url": "https://accounts.binance.com/zh-CN/login",
    "bootstrap": {                         // 用于请求动态 API 的认证代理
      "host": "host",
      "port": 10000,
      "username": "",
      "password": ""
    },
    "gost": {                              // 可选，本地转发层
      "binary": "gost",
      "listen_host": "127.0.0.1",
      "listen_port": 0                      // 0 = 每个账号自动分配空闲端口
    }
  },

  // === 登录 ===
  "login": {
    "start_url": "https://accounts.binance.com/zh-CN/login"
  },

  // === 注册 ===
  "register": {
    "submit_error_ack_max_attempts": 3     // 注册提交错误弹窗最多确认并重试次数
  },

  // === 验证码 ===
  "captcha": {
    "retry_mode": "fast",                  // fast: 快速重试
    "max_attempts_per_round": 1,           // 每轮最大尝试次数
    "max_rounds": 3,                       // 最大轮次
    "cooldown_on_risk_min_sec": 30,        // 风控冷却最小秒数
    "cooldown_on_risk_max_sec": 90,        // 风控冷却最大秒数
    "click_retry_per_cell": 3              // 点击验证码单格重试次数
  },

  // === MFA ===
  "mfa": {
    "submit_retry": 2,                     // MFA 提交重试次数
    "not_registered_keywords": [           // 未注册关键词
      "未注册", "账号不存在", "account does not exist", "not registered", "没有账号"
    ],
    "email_verification_enabled": true      // false 时到邮箱验证码页即停止，账号保留在队列
  },

  // === OpenRouter AI 请求代理 ===
  "ai_proxy": {
    "enabled": false,                       // 仅代理 AI 请求，不等于浏览器业务代理；需要时改为 true
    "bootstrap": {
      "host": "proxy-bootstrap.example.com",
      "port": 10000,
      "username": "PROXY_USERNAME",
      "password": "PROXY_PASSWORD"
    }
  }
}
```

### 账号文件格式

`data/accounts/pending.txt`，每行一个账号，支持三种格式：

```
email1@example.com:password1
email2@example.com:password2
email3@example.com----password3
email4@outlook.com----password4----client_id----refresh_token
```

四段格式用于 Microsoft OAuth + IMAP，登录 Binance 时仍使用第二段 `password4`，第三/四段只用于邮箱验证码拉取。

## 运行

### 从账号文件提交云端任务

在 Linux API 已启动、Windows Worker 已注册并可访问时，可从任意能访问 Linux 的机器执行：

```bash
PYTHONPATH=src python -m binance_cloud.batch_submit \
  --file data/accounts/pending.txt \
  --cloud-url http://Linux公网IP:8000 \
  --mode login \
  --batch-size 20 \
  --count 100 \
  --timeout-seconds 86400
```

命令会读取账号文件（支持 `email:password` 和 `email----password`），按批次调用
`POST /api/login-jobs` 并轮询任务结果。Linux 的 `config/cloud.json` 中配置：

```json
{
  "lark": {
    "webhook_url": "你的 Lark 机器人 Webhook"
  }
}
```

全局任务连续 5 个失败只发送一次告警，全部账号完成只发送一次汇总通知；单账号任务不发送通知。通知由 Linux API 统一发送，Webhook 不从命令行传入。
`--timeout-seconds` 是批量客户端的整体等待上限，超时后命令退出，但 Linux 中已创建的任务继续运行。接口请求可提供 `idempotency_key`，同一键重复提交会返回原任务，不重复创建账号任务。

```bash
# 正常运行
python main.py

# 刷新缓存（删除旧缓存并重新预热）
python main.py --refresh-cache
```

---

## 代理配置

### AI 请求代理

`ai_proxy` 只用于 OpenRouter 验证码识别请求，不影响浏览器访问 Binance 的出口 IP。配置示例默认关闭该能力；如果需要代理 OpenRouter，请把 `config/automation.json` 的 `ai_proxy.enabled` 改为 `true` 并填入真实代理。

### 代理模式

支持以下代理模式，统一使用 subprocess 启动浏览器（无 `--enable-automation` 特征）：

| 模式 | 配置 | 说明 |
|------|------|------|
| 动态 IP + 本地 gost 转发 | `mode: dynamic` + `gost` | 通过本地 gost 转发，无需白名单 |
| 动态 IP 直连 | `mode: dynamic` + `gost.binary: "__disabled_gost__"` | 本机 IP 需在白名单，直接使用动态 IP |
| 静态代理 | `mode: static` | 直接使用静态代理，可带认证 |

注意：当前配置解析会给 `gost.binary` 补默认值 `gost`。如果动态代理要直连，不要只删除 `gost` 配置，需要显式设置 `gost.binary` 为 `__disabled_gost__`。

### 安装 gost

```bash
# macOS
brew install gost

# Linux
# 下载最新版本: https://github.com/ginuerzh/gost/releases
wget https://github.com/ginuerzh/gost/releases/download/v2.11.5/gost-linux-amd64-2.11.5.gz
gunzip gost-linux-amd64-2.11.5.gz
chmod +x gost-linux-amd64-2.11.5
sudo mv gost-linux-amd64-2.11.5 /usr/local/bin/gost
```

### 启动 gost 本地转发

```bash
# 格式: gost -L=http://:本地端口 -F=http://用户名:密码@代理服务器:端口
gost -L=http://:8888 -F=http://PROXY_USERNAME:PROXY_PASSWORD@proxy-bootstrap.example.com:10000
```

启动成功后会显示：
```
{"handler":"http","kind":"service","level":"info","listener":"tcp","msg":"listening on [::]:8888/tcp","service":"service-0","time":"..."}
```

### 配置示例

#### 方式 1：动态 IP + 本地 gost 转发（推荐，无需白名单）

```json
"proxy": {
  "enabled": true,
  "mode": "dynamic",
  "api_url": "https://proxy-api.example.com/gen?region=JP&count=1&proto=http",
  "proxy_quality_check_enabled": true,
  "proxy_quality_check_max_latency_ms": 2500,
  "proxy_quality_check_url": "https://accounts.binance.com/zh-CN/login",
  "bootstrap": {
    "host": "proxy-bootstrap.example.com",
    "port": 10000,
    "username": "PROXY_USERNAME",
    "password": "PROXY_PASSWORD"
  },
  "gost": {
    "binary": "gost",
    "listen_host": "127.0.0.1",
    "listen_port": 0
  }
}
```

**工作流程：**
1. 通过 `bootstrap`（带认证）请求 `api_url` 获取动态 IP
2. 请求 `proxy_quality_check_url` 检查延迟，超过 `proxy_quality_check_max_latency_ms`（默认 2500ms）则丢弃并继续获取下一个 IP
3. 浏览器使用自动分配的本地端口 → gost 转发 → 认证代理 → 目标网站

#### 方式 2：动态 IP 直连（需白名单）

```json
"proxy": {
  "enabled": true,
  "mode": "dynamic",
  "api_url": "https://proxy-api.example.com/gen?region=JP&count=1&proto=http",
  "proxy_quality_check_enabled": true,
  "proxy_quality_check_max_latency_ms": 2500,
  "proxy_quality_check_url": "https://accounts.binance.com/zh-CN/login",
  "bootstrap": {
    "host": "proxy-bootstrap.example.com",
    "port": 10000,
    "username": "PROXY_USERNAME",
    "password": "PROXY_PASSWORD"
  },
  "gost": {
    "binary": "__disabled_gost__",
    "listen_host": "127.0.0.1",
    "listen_port": 0
  }
}
```

**前置条件：**
- 在代理服务商后台将本机公网 IP 加入白名单
- 查看本机 IP：`curl ifconfig.me`

**工作流程：**
1. 通过 `bootstrap`（带认证）请求 `api_url` 获取动态 IP
2. 请求 `proxy_quality_check_url` 检查延迟，超过 `proxy_quality_check_max_latency_ms`（默认 2500ms）则丢弃并继续获取下一个 IP
3. API 返回无认证代理（如 `162.128.86.126:10000`）
4. 浏览器直接使用动态 IP（本机 IP 已在白名单）

---

## 浏览器环境配置详解

### 登录模式 vs 注册模式

| 特性 | 登录模式 | 注册模式 |
|------|----------|----------|
| 浏览器启动 | subprocess + `connect_over_cdp()` | subprocess + `connect_over_cdp()` |
| 反检测脚本 | 完整 11 项伪造 | 完整 11 项伪造 |
| User-Agent | 随机 Chrome 138-145 | 随机 Chrome 138-145 |
| 视口大小 | 随机（基于指纹配置） | 随机（基于指纹配置） |
| 时区/语言 | 随机 | 随机 |
| WebGL 伪造 | 随机 Mac Apple Silicon 显卡 | 随机 Mac Apple Silicon 显卡 |
| 屏幕/硬件伪造 | 完整伪造 | 完整伪造 |
| 输入方式 | `insert_text()` 粘贴式 | `insert_text()` 粘贴式 |

### 浏览器启动方式

使用 subprocess 直接启动 Playwright 内置 Chromium，不经过 `chromium.launch()`，避免 Playwright 自动注入 `--enable-automation` 等 30+ 自动化标志：

```python
# browser_context.py - build_stealth_context()
cmd = [
    chromium_path,                                    # Playwright 内置 Chromium
    f'--remote-debugging-port={port}',
    f'--user-data-dir={user_data_dir}',
    '--disable-blink-features=AutomationControlled',  # 禁用自动化特征
    '--no-first-run',
    '--no-default-browser-check',
    ...
]
chrome_process = subprocess.Popen(cmd, ...)
browser = p.chromium.connect_over_cdp(f'http://127.0.0.1:{port}')
```

对比 Playwright 默认 `chromium.launch()` 的区别：

| 特性 | `chromium.launch()` | subprocess + CDP |
|------|---------------------|------------------|
| `--enable-automation` | 自动添加，无法移除 | 不添加 |
| `navigator.webdriver` | `true` | `undefined` |
| 自动化标志 | 30+ 个 | 仅必要参数 |
| CDP Fetch.enable | 可能启用 | 不启用（除非开缓存） |

### 输入行为反检测

Binance 的行为分析系统会检测键盘输入模式。Playwright 的 `type()` 方法逐字符输入，每个字符间隔均匀（50-100ms），这是典型的自动化特征。

解决方案：使用 `keyboard.insert_text()` 一次性插入文本（等同于 Cmd+V 粘贴），与真人操作一致：

```python
# web_actions.py
# 之前（被检测）：
element.type(email_addr, delay=random.randint(50, 100))

# 现在（通过）：
page.keyboard.insert_text(email_addr)  # 等同于粘贴
```

| 方法 | 行为 | 事件 | 检测风险 |
|------|------|------|----------|
| `type()` | 逐字符，均匀间隔 | 每字符 keydown/keypress/keyup | 高（节奏均匀） |
| `fill()` | JS 直接设置 value | 无键盘事件 | 高（无事件链） |
| `insert_text()` | 一次性插入 | input 事件，isTrusted=true | 低（等同粘贴） |

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
3. 访问注册页 `https://accounts.binance.com/zh-CN/register`
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

`login_flow.py` 与 `register_flow.py` 使用 URL 驱动的状态机，每次迭代检查当前 URL 决定执行什么操作；`flows.py` 只保留两类流程共享的页面判断和状态机辅助函数。

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

### 账号状态说明

公开流程与进程池边界统一使用 `AccountStatus`，不再返回 `True` / `False` / 字符串混合协议。

| 状态 | 含义 | CLI 处理 |
|--------|------|----------|
| `AccountStatus.SUCCESS` | 成功 | 计入成功数 |
| `AccountStatus.FAILED` | 失败 | 重试（最多 max_login_retries 次） |
| `AccountStatus.RATE_LIMITED` | IP 被风控 | 换代理/重建会话重试 |
| `AccountStatus.PROXY_FAILED` | 代理会话失败 | 换代理/重建会话重试 |
| `AccountStatus.NEED_REGISTER` | 账号未注册 | 不重试，计入未注册数 |
| `AccountStatus.ALREADY_REGISTERED` | 账号已注册 | 不重试，计入已注册数 |
| `AccountStatus.AUTH_FAILED` | 平台认证失败 | 不重试，计入认证失败数 |
| `AccountStatus.IMAP_AUTH_FAILED` | IMAP 认证失败 | 不重试，计入 IMAP 失败数 |
| `AccountStatus.EMAIL_VERIFICATION_REQUIRED` | 已到邮箱验证码页但配置关闭自动取码 | 不计失败，账号保留队列 |

---

## AI 验证码识别

通过 OpenRouter API 调用视觉 AI 模型识别验证码，当前通过 `src/binance_analyzer/captcha/` 独立库扩展，支持三种类型：

### 1. 复选框验证码

**识别流程：**
1. 检测 `.bcapc-popup` 中的“进行人机身份验证”等复选框挑战
2. 截图验证码容器
3. 发送截图 + 复选框提示词给 AI
4. AI 返回复选框中心截图坐标
5. 按截图尺寸和页面元素尺寸换算为页面坐标
6. 点击复选框并等待验证码稳定消失

**AI 返回格式：**
```json
{"found": true, "x": 408, "y": 494}
```

真实注册链路已验证过坐标换算与点击路径，日志形态如下：

```text
[复选框] AI 坐标(408,494) -> 页面(204.0,247.0)
[验证码] checkbox 验证码通过!
```

### 2. 点击验证码（3x3 图片网格）

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

### 3. 滑块验证码

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

- 每轮最多 `max_attempts_per_round` 次尝试
- 最多 `max_rounds` 轮
- AI 调用失败自动重试 3 次，带指数退避
- 检测到风控签名时按 `cooldown_on_risk_min_sec` / `cooldown_on_risk_max_sec` 冷却
- 验证码消失需连续检测确认

---

## 邮箱验证码

当 `mfa.email_verification_enabled` 为 `false` 时，流程到 `/register/verification` 或 `verification-new-register` 会停止并返回 `AccountStatus.EMAIL_VERIFICATION_REQUIRED`。这种状态不写入失败账号文件，也不会从账号队列移除，适合只验收到邮箱验证码页的注册测试。

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

### Microsoft OAuth + IMAP 模式

账号文件使用四段格式时启用 OAuth + IMAP：

```text
email@outlook.com----login_password----client_id----refresh_token
```

其中 `login_password` 用于 Binance 登录/注册，`client_id` 和 `refresh_token` 只用于邮箱 IMAP 认证。

---

## 本地缓存系统

通过 Playwright 的 `page.route()` 拦截请求，对静态资源使用 `route.fulfill()` 直接从本地返回。

**缓存范围：**
- `bin.bnbstatic.com/static` 的 JS/CSS
- `public.bnbstatic.com/unpkg` 的 JS/CSS

### 缓存 vs 普通模式

| 特性 | `cache.enabled: true` | `cache.enabled: false` |
|------|----------------------|------------------------|
| 浏览器启动 | subprocess + CDP 连接 | subprocess + CDP 连接 |
| 请求拦截 | `page.route("**/*")` | 无 |
| 静态资源缓存 | `route.fulfill()` 本地返回 | 无 |

---

## 日志系统

```
logs/
  failures/
    {账号}_{时间}.log                    # 失败账号的详细流程日志
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

- 查看控制台日志中的验证码类型、AI 返回 JSON 和坐标换算信息
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
| 批量处理 | 2-3 | true | 动态 IP 推荐 | true |

不建议 `max_workers >= 5`，风控触发概率明显增加。

---

## 相关链接

- [B2Proxy 代理服务](https://dashboard.b2proxy.com/zh-CN/proxy/residential) - 动态 IP 代理服务商
