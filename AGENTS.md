# 项目开发记录

## 项目目标与当前进展

- 目标：自动化处理 Binance 登录/注册流程，包含浏览器自动化、验证码识别、邮箱验证码提取、代理运行时和结果持久化。
- 当前进展：主流程已按入口层、编排层、登录/注册流程、动作/验证码/邮箱/存储能力模块拆分；代理能力已抽为 `src/proxy_forwarder` 可复用包。
- 下一步：继续拆分 `email_imap.py` 和 `src/proxy_forwarder/runtime.py` 中的大函数，优先提取类级抽象，降低状态机维护成本。

## 架构约定

- `src/binance_analyzer/cli.py`：命令行入口、并发调度、账号结果落盘。
- `src/binance_analyzer/orchestrator.py`：单账号运行编排，负责代理、缓存、流程调用和 cookie 提取；浏览器上下文细节不放在此处。
- `src/binance_analyzer/browser_context.py`：浏览器上下文、反检测初始化脚本、subprocess 启动和清理。
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
- `src/binance_analyzer/screenshot_storage.py`：截图清理。
- `src/proxy_forwarder/`：代理解析、质量检查、本地转发和运行时管理的可复用包；`proxy_utils.py` 放纯代理解析/格式化工具，业务代码通过 `proxy_integration.py` 适配。

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
- 浏览器：需要安装 Playwright Chromium。
- 必需配置：`config.json`，可从 `config.example.json` 复制。
- 必需凭证：`OPENROUTER_API_KEY` 或 `config.json.openrouter_api_key`。
- 账号文件：`accounts_file` 指向的文本文件，每行支持 `email:password` 或 `email----password`。

## 重要技术决策

- 账号状态使用 `AccountStatus` 统一建模，进程池边界也传递枚举值，不保留 `True/False/字符串` 结果协议。
- `rate_limited` 代表 IP/代理会话失败，应允许入口层重建代理重试；最终仍失败时再计入风控限制。
- `proxy_failed` / `rate_limited` 属于环境或代理会话问题，不能写入失败账号文件，也不能从账号队列移除。
- 页面登录态判断必须解析 URL 的 hostname/path，不使用整串包含判断，避免 `return_to=/my/dashboard` 这类 query 参数误判。
- 账号密码在本项目中属于账号复用所需数据，不做脱敏清理。`registered_accounts.json`、成功/失败账号队列文件均保留完整凭据。
- 配置错误和显式启用的核心依赖缺失必须 fail-fast。已移除的兜底包括：代理预热失败后直连、未知 mode 自动当 login、gost 配置缺失后静默直连、找不到邮箱输入框时填写第一个文本框、全局弹窗无法点击时用 JS 强行隐藏。
- 动态代理必须配置 `proxy.bootstrap`，不支持无 bootstrap 直连请求代理 API；代理布尔配置只接受 JSON bool，不接受 `"true"` / `"false"` 字符串。
- 登录/注册核心页面动作必须显式成功：输入邮箱、输入密码、点击继续、勾选协议失败时立即返回失败，不做 JS 扫按钮、回车提交、点击页面中心等宽泛兜底。
- 新增验证码类型时，直接扩展 `src/binance_analyzer/captcha/`：新增 `CaptchaType`、提示词模板、检测规则、对应 solver，并注册到 `build_default_solver_registry()`。验证码求解结果使用 `CaptchaSolveStatus`，由流程层映射到账号状态。

## 踩坑记录

- 现象：`accounts.binance.com/login?return_to=/my/dashboard` 可能被字符串包含逻辑误判为登录成功。
  原因：旧逻辑直接在完整 URL 字符串中查找 `/my/dashboard`。
  解决方案：统一使用 `page_signals.py` 解析 hostname/path 后判断登录态。

- 现象：代理失败文本能被代理检测函数识别，但没有进入通用风险分支。
  原因：`has_risk` 只检查通用风控关键词，未包含代理失败关键词。
  解决方案：`assess_risk_text()` 将代理失败和认证失败也纳入 `has_risk`。

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
