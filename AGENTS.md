# 项目开发记录

## 项目目标与当前进展

- 目标：自动化处理 Binance 登录/注册流程，包含浏览器自动化、验证码识别、邮箱验证码提取、代理运行时和结果持久化。
- 当前进展：主流程已按入口层、编排层、登录/注册流程、动作/验证码/邮箱/存储能力模块拆分；代理能力已抽为 `src/proxy_forwarder` 可复用包。
- 下一步：继续拆分 `email_imap.py` 和 `src/proxy_forwarder/runtime.py` 中的大函数，优先提取类级抽象；真实登录复测创作者中心 API 提取仍待安排。
- 最近完成：文件锁已改为 `src/binance_analyzer/file_lock.py` + `portalocker`，账号存储、代理 IP 存储、注册账号存储和创作者 API 配额均不再直接依赖 Unix-only 的 `fcntl`，Windows/macOS 共用同一实现。
- 最近完成：登录/注册成功后的 Dashboard 判定改为使用 `page_signals.is_dashboard_url()`；Creator API 入口点击后会等待导航并切换到新打开的 tab，避免在旧页面读取密钥。
- 最近实测：2026-08-20 真实登录 `accounts.txt` 中的 2 个账号均通过邮箱 MFA 并写入 Cookie/CSRF；Creator Center 页面实际按钮文案为“创建 API 密钥”，已加入提取入口选择器。因成功账号已从队列移除，修复后的 API 真实读取尚未复测。
- 最近修复：Creator API 读取曾把 `Square-Creator-*` 用户名误保存为 `api_key`；现改为仅读取明确 API key 语义的字段，找不到时保存 `output/creator_api_debug/` 截图与文本并失败，不再从整页文本猜值。已清理 `output/registered_accounts.json` 中错误 API 字段。
- 最近实测：2026-08-20 对 `tommimjr0@outlook.com` 执行真实登录时，在登录页滑块验证码阶段被弹窗拦截，流程返回 `AUTH_FAILED`，未进入 Creator Center，未生成 API 调试截图；账号已写入 `output/failed_accounts.txt`，需重新加入待处理队列后再测。
- 最近修复：创作者中心进页后无点击、弹窗已有密钥仍失败。入口「查看 API >」无法 exact 匹配；新手引导层会挡住点击；密钥是「API 密钥」标签后的纯文本而不是 input。提取器改为包含匹配、先关引导、禁止 `networkidle` 空等，并按标签读取密钥。真实页面点击尚未复测。
- 最近完成：提取 API 密钥后同时读取资料卡展示名称（`@Square-Creator-` 前方的 display_name，例如 `Alan Searchfield diwl`），写入 `registered_accounts.json` 的 `display_name`。找不到名称时密钥仍保存，display_name 留空。
- 最近完成：抽完 API key 后点击「编辑」，从「编辑个人资料」读取「昵称」写入 `display_name`、「用户名」写入 `username`，然后点取消关闭，不改资料。
- 最近完成：新增 `src/binance_cloud/` 实验性 SQLite 云端 API 与 Windows Worker 入口；Linux 创建登录任务并异步 POST 给 Windows，Windows 复用 `register_account`，每个账号完成后回调 Linux；Cookie 额外记录 `cookie_expires_at`，SQLite 长字段使用 `TEXT`。
- 最近完成：云端服务保留可选 Worker/回调 Token 校验（默认未配置时放行），并增加 Worker 注册与执行心跳、任务租约超时回收、Cookie 在线检查接口、固定代理任务计数和回调重试；新增 2 项 SQLite 服务测试。
- 最近完成：继续增加云端维护循环（过期租约回收、Worker offline、retryable 自动重派）、Cookie 周期检查、任务取消/数据库备份/日志清理接口、SQLite WAL 和部署模板；Worker 任务已传递 `client_id`/`refresh_token`，Cookie 过期时间统一为 UTC ISO 字符串。
- 最近完成：成功回调缺少 Cookie 时拒绝；新增 `/api/accounts/{id}/relogin` 重新登录入口；全量测试仍为 151 项通过。
- 最近完成：通过本机 Chrome 抓到 Creator Center 使用的真实登录态接口 `POST /bapi/accounts/v1/public/authcenter/auth`；`cookie_checker.py` 现直接携带 Cookie 调用该接口，不启动浏览器、不调用 Creator API 信息提取；全量测试更新后需重新验证。

## 架构约定

- `src/binance_analyzer/cli.py`：命令行入口、并发调度、账号结果落盘。
- `src/binance_analyzer/orchestrator.py`：单账号运行编排，负责代理、缓存、流程调用和 cookie 提取；浏览器上下文细节不放在此处。
- `src/binance_analyzer/browser_context.py`：浏览器上下文、反检测初始化脚本、subprocess 启动本机 Google Chrome 和清理。
- `src/binance_analyzer/cache_routes.py`：浏览器静态资源缓存路由和响应跟踪。
- `src/binance_analyzer/flows.py`：登录/注册流程共享状态机工具和页面状态辅助函数。
- `src/binance_analyzer/login_flow.py`：登录 URL 状态机。
- `src/binance_analyzer/register_flow.py`：注册 URL 状态机。
- `src/binance_analyzer/results.py`：账号处理状态的唯一来源。入口层、编排层和公开流程函数只传递 `AccountStatus`。
- `src/binance_analyzer/page_signals.py`：URL 状态、风控、代理失败、认证失败等页面信号检测的唯一来源。
- `src/binance_analyzer/captcha/`：验证码库包。`types.py` 定义类型，`detector.py` 识别页面类型，`prompts.py` 维护 AI 提示词，`ai_client.py` 封装 OpenRouter 请求，`solvers.py` 注册各类型 solver，`service.py` 提供求解主循环。
- `src/binance_analyzer/account_storage.py`：账号队列与成功/失败账号结果文件。
- `src/binance_analyzer/proxy_ip_storage.py`：代理出口 IP 使用记录。
- `src/binance_analyzer/registered_account_storage.py`：`registered_accounts.json` 持久化，完整保存账号凭据供后续复用。
- `src/binance_analyzer/creator_api.py`：创作者中心提取；先读 API 密钥，再点「编辑」读取昵称（`display_name`）和用户名（`username`）。
- `src/binance_analyzer/creator_api_quota.py`：单次运行 API 提取名额，失败释放、成功占用。
- `src/binance_analyzer/screenshot_storage.py`：截图清理。
- `src/proxy_forwarder/`：代理解析、质量检查、本地转发和运行时管理的可复用包；`proxy_utils.py` 放纯代理解析/格式化工具，业务代码通过 `proxy_integration.py` 适配。
- `src/binance_cloud/`：SQLite 数据库、Linux FastAPI 接口和 Windows 执行服务；服务入口目前为实验性 MVP，生产 HTTPS、真实 Binance 在线检查和双机联调仍待验收。

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
- 必需配置：`config.json`，可从 `config.example.json` 复制。
- 必需凭证：`OPENROUTER_API_KEY` 或 `config.json.openrouter_api_key`。
- 账号文件：`accounts_file` 指向的文本文件，每行支持 `email:password`、`email----password` 或 `email----password----client_id----refresh_token`。

## 重要技术决策

- 账号状态使用 `AccountStatus` 统一建模，进程池边界也传递枚举值，不保留 `True/False/字符串` 结果协议。
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
- `creator_api.max_accounts` 表示单次运行累计成功提取的 API 数，不限制登录/注册账号数量；并发任务通过输出目录配额状态文件协调，提取失败会释放名额供后续成功账号补位。

## 踩坑记录

- 现象：`accounts.binance.com/login?return_to=/my/dashboard` 可能被字符串包含逻辑误判为登录成功。
  原因：旧逻辑直接在完整 URL 字符串中查找 `/my/dashboard`。
  解决方案：统一使用 `page_signals.py` 解析 hostname/path 后判断登录态。

- 现象：代理失败文本能被代理检测函数识别，但没有进入通用风险分支。
  原因：`has_risk` 只检查通用风控关键词，未包含代理失败关键词。
  解决方案：`assess_risk_text()` 将代理失败和认证失败也纳入 `has_risk`。

- 现象：Windows 原生 Python 启动时提示 `ModuleNotFoundError: No module named 'fcntl'`，`pip install fcntl` 无可用发行版。
  原因：`fcntl` 是 Unix 标准库模块，不支持 Windows。
  解决方案：统一通过 `file_lock.py` 调用 `portalocker`，保留共享锁/排他锁语义；完整测试在 macOS 环境 149 项通过。

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
