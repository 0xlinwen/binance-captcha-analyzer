# binance_captcha_analyzer

Binance 登录/注册自动化工具（Playwright + 验证码识别 + IMAP 邮箱验证码）。

当前代码已完成：
- 并发稳定性修复（MFA 提交、验证码容器点击、风控冷却）
- 单文件拆分为模块化结构
- 保持原入口兼容：`python captcha_analyzer.py`

## 项目结构

```text
captcha_analyzer.py                # 兼容入口（转调 src/binance_analyzer/cli.py）
src/binance_analyzer/
  __init__.py
  cli.py                           # 主入口、并发调度、信号处理
  config.py                        # 配置加载与默认值
  orchestrator.py                  # 单账号编排（浏览器、登录/注册、cookie提取）
  flows.py                         # 登录/注册状态机
  web_actions.py                   # 页面交互动作（输入、点击、跳转）
  captcha_solver.py                # 点击/滑块验证码执行与重试
  captcha_ai.py                    # OpenRouter 调用与 JSON 解析
  email_imap.py                    # IMAP 读取与 MFA 提交
  storage.py                       # 账号读取、结果写入、清理
```

## 安装与准备

```bash
pip install -r requirements.txt
playwright install chromium
```

配置 API Key（推荐环境变量）：

```bash
export OPENROUTER_API_KEY="your_key"
```

## 运行

```bash
python captcha_analyzer.py
```

## 配置说明（`config.json`）

### 核心字段

- `openrouter_api_key`：可留空，优先读取 `OPENROUTER_API_KEY`
- `models`：验证码识别模型列表，默认取第一个
- `imap_host` / `imap_port`：邮箱 IMAP 连接信息
- `accounts_file`：账号文件（`email:password` 每行一个）
- `output_file`：成功账号输出路径

### 浏览器

- `headless`：是否无头
- `browser.use_builtin_chromium`：是否使用 Playwright 内置 Chromium
- `proxy.enabled/server/username/password`：代理配置

### 登录

- `login.start_url`：登录起始页面，默认 `https://accounts.binance.com/zh-CN/login`

### 验证码

- `captcha.retry_mode`：`fast` / 其他
- `captcha.max_attempts_per_round`：每轮尝试次数
- `captcha.max_rounds`：轮次数
- `captcha.cooldown_on_risk_min_sec`：风控冷却最小秒数
- `captcha.cooldown_on_risk_max_sec`：风控冷却最大秒数
- `captcha.click_retry_per_cell`：点击验证码单格重试次数

### MFA

- `mfa.submit_retry`：提交 MFA 的重试次数
- `mfa.not_registered_keywords`：明确未注册关键词

### 并发

- `max_workers`：并发进程数（建议 2-3）
- `runtime.max_workers_default`：默认并发
- `runtime.start_delay_min_sec` / `runtime.start_delay_max_sec`：启动错峰区间

## 推荐并发策略

- 稳定优先：`max_workers = 2`
- 吞吐优先：`max_workers = 3`（建议配合代理池）
- 不建议：`max_workers >= 5`（风控触发概率明显增加）

## 常见问题

### 1) `208075` / `认证失败，请刷新页面后重试`

说明触发风控：
- 降低并发（2-3）
- 增大启动错峰
- 启用高质量独立代理出口

### 2) `Element is not attached to the DOM`

已在 MFA 提交流程做规避（不依赖旧句柄回车）。
若仍出现，通常是页面高频重渲染或网络抖动，建议降低并发与加长等待。

### 3) 验证码点击被遮挡 (`intercepts pointer events`)

已改为在当前可见验证码容器内重试点击。
若频繁出现，通常是风控弹层覆盖，等待冷却后重试。

## 输出结果

成功账号会写入 `output/registered_accounts.json`，包含：
- `email`
- `cookie`
- `csrftoken`
- `enabled`
