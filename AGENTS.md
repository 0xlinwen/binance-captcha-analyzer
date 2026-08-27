# 项目开发记录

## 项目目标与当前进展

- 目标：自动化 Binance 登录/注册，支持浏览器自动化、验证码和邮箱 MFA、代理、Cookie/CSRF 导出，以及 Linux Cloud -> Windows Worker 的远程任务闭环。
- 当前进展：本地 CLI、Windows Worker、Linux Cloud 三个入口均可运行；本地和 Windows 共用 `config/automation.json`，Cloud 使用 SQLite 保存任务、日志和凭证。2026-08-26 已实测本机 Cloud -> Worker -> 登录 -> 回调闭环，MFA、Cookie 和 CSRF 已写入 Cloud 数据库。
- 当前欠账：Creator Center 的真实 API 密钥提取在最近一次成功登录后的页面上尚未复测；它失败不会影响登录 Cookie 保存。未实现后台 Cookie 在线检查、会话二次验证、加密和按天删除 Cookie，均为明确未纳入范围的能力。
- 最近验证：`PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'` 于当前工作区通过 166 项；`PYTHONPATH=src python3 -m compileall -q src tests demo` 和 `git diff --check` 通过。
- 配置与运行文件：配置按 `automation.json`、`worker.json`、`cloud.json` 分角色拆分，无根目录 `config.json` 回退。相对运行路径均相对各自项目根目录：账号队列在 `data/accounts/`，本地结果在 `data/results/`，运行状态在 `data/runtime/`，调试证据在 `artifacts/debug/`。
- 最近完成：Cloud 在任意工作目录启动时仍从项目根目录读取 `config/cloud.json` 和解析相对 SQLite 路径；Windows 回调 Outbox 位于 `data/runtime/callback_outbox.json`。任务组 Lark 告警和完成通知通过 SQLite 事件键去重，失败会在后续维护周期重试。
- 文档：README 已描述 Cloud/Worker/SQLite/回调/Lark 闭环、三份配置的职责与回调方向、协议版本、Worker ID/并发、Linux 监听与反向代理前提，以及 Windows 部署路径模板。
- 部署状态：Linux Cloud 已部署到 `/root/binance-captcha-analyzer`，由 `binance-cloud.service` 管理并监听 `0.0.0.0:8001`；公网健康接口已验证。Linux 专用 `config/cloud.json` 的回调地址为 `http://62.169.26.83:8001/api/worker/callback`，Linux 到 Windows Worker `43.165.177.157:8100` 的健康接口已验证，协议版本为 `1`。
- 凭证时间字段已统一为 `credential_exported_at`，表示本次从浏览器 Context 导出 Cookie/CSRF 的 UTC 时间；不再读取或保存 Cookie 内部 `expires`，也不再使用 `cookie_expires_at`、`credential_updated_at`。当前测试阶段数据库无历史数据，SQLite 直接按新 schema 创建；该字段只用于凭证新旧回调覆盖判断，不代表 Cookie 过期时间。
- 任务组连续失败停止阈值由 `config/cloud.json.consecutive_failure_limit` 配置；Linux 达到阈值后取消任务组未完成明细并发送一次 Lark 告警，Windows 通过取消状态停止后续账号。

## 架构约定

- `src/binance_analyzer/cli.py`：命令行入口、并发调度、账号结果落盘。
- `src/binance_analyzer/orchestrator.py`：单账号运行编排，负责代理、缓存、流程调用和 cookie 提取；浏览器上下文细节不放在此处。
- `src/binance_analyzer/browser_context.py`：浏览器上下文、反检测初始化脚本、subprocess 启动本机 Google Chrome 和清理。
- `src/binance_analyzer/cache_routes.py`：浏览器静态资源缓存路由和响应跟踪。
- `src/binance_analyzer/flows.py`：登录/注册流程共享状态机工具和页面状态辅助函数。
- `src/binance_analyzer/login_flow.py`：登录 URL 状态机。
- `src/binance_analyzer/register_flow.py`：注册 URL 状态机。
- `src/binance_analyzer/results.py`：账号状态枚举 `AccountStatus` 的唯一来源；`automation_driver.py` 的 `AutomationResult` 统一承载状态、错误和登录凭证，供 CLI 与 Windows Worker 使用。
- `src/binance_analyzer/page_signals.py`：URL 状态、风控、代理失败、认证失败等页面信号检测的唯一来源。
- `src/binance_analyzer/captcha/`：验证码库包。`types.py` 定义类型，`detector.py` 识别页面类型，`prompts.py` 维护 AI 提示词，`ai_client.py` 封装 OpenRouter 请求，`solvers.py` 注册各类型 solver，`service.py` 提供求解主循环。
- `src/binance_analyzer/account_storage.py`：账号队列与成功/失败账号结果文件。
- `src/binance_analyzer/proxy_ip_storage.py`：代理出口 IP 使用记录。
- `src/binance_analyzer/registered_account_storage.py`：`registered_accounts.json` 持久化，完整保存账号凭据供后续复用。
- `src/binance_analyzer/creator_api.py`：创作者中心提取；先读 API 密钥，再点「编辑」读取昵称（`display_name`）和用户名（`username`）。
- `src/binance_analyzer/creator_api_quota.py`：单次运行 API 提取名额，失败释放、成功占用。
- `src/binance_analyzer/screenshot_storage.py`：截图清理。
- `src/proxy_forwarder/`：代理解析、质量检查、本地转发和运行时管理的可复用包；`proxy_utils.py` 放纯代理解析/格式化工具，业务代码通过 `proxy_integration.py` 适配。
- `src/binance_cloud/`：SQLite 数据库、Linux FastAPI 接口和 Windows 执行服务；本机 Cloud -> Worker -> 回调已实测。公网 HTTPS、端口放行和反向代理由部署环境负责；不提供 Cookie 在线检查。

## 常用命令

```bash
pip install -r requirements.txt
playwright install chromium
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
PYTHONPATH=src python3 -m compileall -q src tests
python main.py
python main.py --refresh-cache
```

## 环境要求

- Python：建议 3.10+。
- 依赖：`requirements.txt` 包含 `portalocker>=2.8,<4`，用于跨平台文件锁。
- 浏览器：主流程使用本机 Google Chrome（可用 `CHROME_PATH` 覆盖路径）；缓存预热仍用 `channel="chrome"`。Playwright 包仍需安装以便 CDP 控制。
- 必需自动化配置：`config/automation.json`，可从 `config/automation.example.json` 复制；根目录 `config.json` 不参与运行。
- 必需凭证：`OPENROUTER_API_KEY` 或 `config/automation.json.openrouter_api_key`。
- 账号文件：`accounts_file` 指向的文本文件，每行支持 `email:password`、`email----password` 或 `email----password----client_id----refresh_token`。

## 重要技术决策

- 流程状态使用 `AccountStatus` 统一建模；编排完成后统一返回 `AutomationResult`，其中包含状态、错误和可回调的 Cookie/CSRF 凭证。
- `rate_limited` 代表 IP/代理会话失败，应允许入口层重建代理重试；最终仍失败时再计入风控限制。
- `proxy_failed` / `rate_limited` 属于环境或代理会话问题，不能写入失败账号文件，也不能从账号队列移除。
- 页面登录态判断必须解析 URL 的 hostname/path，不使用整串包含判断，避免 `return_to=/my/dashboard` 这类 query 参数误判。
- 账号密码在本项目中属于账号复用所需数据，不做脱敏清理。`registered_accounts.json`、成功/失败账号队列文件均保留完整凭据。
- 配置错误和显式启用的核心依赖缺失必须 fail-fast。已移除的兜底包括：代理预热失败后直连、未知 mode 自动当 login、gost 配置缺失后静默直连、找不到邮箱输入框时填写第一个文本框、全局弹窗无法点击时用 JS 强行隐藏。
- 动态代理必须配置 `proxy.bootstrap`，不支持无 bootstrap 直连请求代理 API；代理布尔配置只接受 JSON bool，不接受 `"true"` / `"false"` 字符串。
- `ai_proxy` 只代理 OpenRouter 验证码识别请求，不影响浏览器访问 Binance 的业务代理；示例配置默认关闭，需要时显式设置 `ai_proxy.enabled=true` 并替换真实代理。
- 动态代理直连需要显式设置 `proxy.gost.binary="__disabled_gost__"`；仅删除 `gost` 配置会被配置解析补回默认 `gost`。
- 登录/注册核心页面动作必须显式成功：输入邮箱、输入密码、点击继续、勾选协议失败时立即返回失败，不做 JS 扫按钮、回车提交、点击页面中心等宽泛兜底。
- 新增验证码类型时，直接扩展 `src/binance_analyzer/captcha/`：新增 `CaptchaType`、提示词模板、检测规则、对应 solver，并注册到 `build_default_solver_registry()`。验证码求解结果使用 `CaptchaSolveStatus`，由流程层映射到账号状态。
- 主流程浏览器改为本机 Google Chrome（`get_local_chrome_path()`），不再使用 Playwright 自带 Chromium；找不到 Chrome 时 fail-fast，禁止静默回退。
- 创作者中心 API 提取由 `creator_api.enabled` 开关控制，`creator_api.max_accounts` 限制单次运行前 N 个账号；结果写入 `registered_accounts.json` 的 `api_key`、`api_extracted_at` 与 `display_name` 字段，默认关闭。
- `creator_api.max_accounts` 表示单次运行累计成功提取的 API 数，不限制登录/注册账号数量；并发任务通过 `data/runtime/creator_api_quota.json` 协调配额，提取失败会释放名额供后续成功账号补位。

## 踩坑记录

- 现象：`accounts.binance.com/login?return_to=/my/dashboard` 可能被字符串包含逻辑误判为登录成功。
  原因：旧逻辑直接在完整 URL 字符串中查找 `/my/dashboard`。
  解决方案：统一使用 `page_signals.py` 解析 hostname/path 后判断登录态。

- 现象：代理失败文本能被代理检测函数识别，但没有进入通用风险分支。
  原因：`has_risk` 只检查通用风控关键词，未包含代理失败关键词。
  解决方案：`assess_risk_text()` 将代理失败和认证失败也纳入 `has_risk`。

- 现象：Windows 原生 Python 启动时提示 `ModuleNotFoundError: No module named 'fcntl'`，`pip install fcntl` 无可用发行版。
  原因：`fcntl` 是 Unix 标准库模块，不支持 Windows。
  解决方案：统一通过 `file_lock.py` 调用 `portalocker`，保留共享锁/排他锁语义。

- 现象：配置错误或页面结构变化时，流程可能继续走宽泛兜底，导致看似运行、实际状态不可控。
  原因：旧代码存在直连预热、未知 mode 回退、宽泛输入框、JS 隐藏弹窗等降级路径。
  解决方案：移除复杂兜底；明确元素找不到就返回失败，核心依赖缺失或配置错误直接抛错。

- 现象：真实登录验收时代理 TLS 连接失败，但入口层最终把账号写入失败文件并移出队列。
  原因：`process_account()` 在代理重试耗尽后把最后状态覆盖成通用 `failed`，`finalize_account_result()` 对所有非成功状态都消耗账号。
  解决方案：保留最后的 `proxy_failed` / `rate_limited` 状态，并在结果收尾时将环境失败视为不消耗账号的状态。

- 现象：AI 验证码返回单行 Markdown JSON、空 choices、HTTP 200 错误体或缺坐标时，验证码流程会直接中断或误点。
  原因：AI 响应解析和 solver 坐标读取缺少统一校验，裸异常不会进入 `CaptchaAIError` 可重试路径。
  解决方案：OpenRouter 响应统一提取并包装为 `CaptchaAIError`，复选框/滑块坐标必须是有限数字；检测器优先识别真实点击/滑块挑战，避免复选框文字残留误判。

- 现象：注册页复选框验证码通过后，`.bcapc-popup` 可能短暂残留并拦截下一轮邮箱输入点击。
  原因：注册状态机只在点击继续后的 `captcha` 响应分支处理验证码，下一轮回到 register 状态时没有先处理已存在的验证码弹窗。
  解决方案：register 状态准备输入邮箱前先检查可见验证码弹窗，存在时调用现有验证码服务处理后再继续状态机。

- 现象：复选框验证码点击后没有继续确认验证结果，表现为验证码弹窗被关闭或重新出现，但没有按当前验证码类型重新截图提交给 AI。
  原因：复选框验证码会消耗 `captcha.max_attempts_per_round` 的唯一尝试次数，服务层在确认复选框验证结果前就结束本轮。
  解决方案：验证码服务层按当前类型循环检测；复选框本身也作为独立验证码类型截图识别并使用复选框提示词提交给 AI，复选框尝试次数与图片/滑块挑战次数分开统计，点击后等待验证结果，再按最新检测到的类型继续处理。

- 现象：2026-08-20 实测登录 MFA 已读到邮件验证码，但页面提交按钮未命中。
  原因：MFA 页面使用“下一步”或原生 `button[type=submit]`，旧选择器只覆盖提交/确认/继续/验证文本。
  解决方案：仅在 MFA 提交函数中补充 `下一步`、`Next` 和 `button[type=submit]`，不使用回车或页面宽泛点击。

- 现象：登录成功后可能误把带有 `return_to=/my/dashboard` 的登录 URL 当作 Dashboard，或 Creator API 入口打开新 tab 后仍在旧页面读取。
  原因：Dashboard 使用整串 URL 包含判断；Creator API 提取未等待入口点击后的导航/弹窗页面。
  解决方案：Dashboard 统一调用 `is_dashboard_url()` 解析 hostname/path；Creator API 点击入口后等待页面稳定并选择新 tab。

- 现象：打开创作者中心后页面停住，看不到点击，随后直接失败；有时弹窗已经显示密钥仍报未找到。
  原因：入口文案是「查看 API >」，exact 匹配失败；`a,button` 全量扫描很慢且点不到普通 div；新手引导层拦截指针；广场页 `networkidle` 会空等满超时；密钥在「API 密钥」下方的文本节点，不在 input 里。
  解决方案：按包含文案点入口，先点「跳过」关掉引导，不等 networkidle，只从「API 密钥 / API Key」标签后读取 16–64 位密钥，继续排除 `Square-Creator-*` 用户名。

- 现象：资料卡展示名称（如 `Alan Searchfield diwl`）没有写入 `display_name`。
  原因：提取只读 API 密钥，且 `display_name` 不在登录托管字段里，二次保存不会更新。
  解决方案：在同一创作者中心页读取 `@Square-Creator-` 前方文本作为 `display_name`，排除句柄和按钮文案；`LOGIN_MANAGED_FIELDS` 加入该字段。

- 现象：`display_name` 落成 `User-f6c2f7fa` 这类值，和页面上看到的昵称不一致。
  原因：旧逻辑解析整页 `inner_text`/句柄相邻字段，拿到的是默认用户名或接口数据，不是资料卡上画出来的大字昵称。
  解决方案：用页面可见文本节点（排除弹窗、隐藏节点）按字号和位置选取 `@Square-Creator-` 左侧标题；不行再打开「编辑」读「昵称」输入框。
