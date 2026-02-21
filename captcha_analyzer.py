#!/usr/bin/env python3
"""
币安批量自动注册脚本
支持：图片点击验证码 + 滑块验证码 + IMAP 邮件验证码
"""

import json
import base64
import time
import imaplib
import email
from email.header import decode_header
import re
import os
import hashlib
import fcntl
import random
import signal
import sys
import traceback
import requests
from pathlib import Path
from concurrent.futures import ProcessPoolExecutor, as_completed
from playwright.sync_api import sync_playwright

# 页面加载超时时间（毫秒）
PAGE_TIMEOUT = 60000

# 全局进程池引用，用于信号处理
executor_ref = None


def ensure_debug_dir():
    """调试目录已禁用（兼容保留）"""
    return


def save_debug_artifacts(page, tag, email_addr=""):
    """调试落盘已禁用（兼容保留）"""
    return


def load_config():
    """读取配置文件"""
    config_path = Path(__file__).parent / "config.json"
    with open(config_path, "r", encoding="utf-8") as f:
        config = json.load(f)

    # 默认配置（向后兼容）
    config.setdefault("login", {})
    config["login"].setdefault("start_url", "https://accounts.binance.com/zh-CN/login")

    config.setdefault("browser", {})
    config["browser"].setdefault("channel", "chrome")
    config["browser"].setdefault("persistent_profile_dir", "output/chrome_profile")
    config["browser"].setdefault("minimal_stealth", True)

    config.setdefault("captcha", {})
    config["captcha"].setdefault("retry_mode", "fast")
    config["captcha"].setdefault("max_attempts_per_round", 5)
    config["captcha"].setdefault("max_rounds", 3)

    # 优先使用环境变量，兼容旧配置
    env_api_key = os.getenv("OPENROUTER_API_KEY", "").strip()
    if env_api_key:
        config["openrouter_api_key"] = env_api_key

    if not config.get("openrouter_api_key"):
        raise ValueError("缺少 OpenRouter API Key，请设置 OPENROUTER_API_KEY 或在 config.json 中配置 openrouter_api_key")

    return config


def load_accounts(accounts_file):
    """读取账号文件，返回 [(email, password), ...]"""
    accounts = []
    accounts_path = Path(__file__).parent / accounts_file
    with open(accounts_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line and ":" in line:
                email_addr, password = line.split(":", 1)
                accounts.append((email_addr.strip(), password.strip()))
    return accounts


def save_registered_account(output_file, account_data):
    """保存注册成功的账号到 JSON 文件（追加模式，不覆盖，进程安全）"""
    output_path = Path(__file__).parent / output_file
    output_path.parent.mkdir(exist_ok=True)
    lock_path = output_path.with_suffix(".lock")

    # 使用文件锁实现跨进程同步
    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            # 读取现有数据
            data = {"accounts": []}
            if output_path.exists() and output_path.stat().st_size > 0:
                try:
                    with open(output_path, "r", encoding="utf-8") as f:
                        content = f.read().strip()
                        if content:
                            loaded = json.loads(content)
                            if isinstance(loaded, dict) and "accounts" in loaded:
                                data = loaded
                            else:
                                data = {"accounts": [loaded] if isinstance(loaded, dict) else []}
                    print(f"已读取现有 {len(data['accounts'])} 个账号")
                except (json.JSONDecodeError, Exception) as e:
                    print(f"读取现有数据失败: {e}")
                    backup_path = output_path.with_suffix(".json.bak")
                    if output_path.exists():
                        import shutil
                        shutil.copy(output_path, backup_path)
                        print(f"已备份到: {backup_path}")
                    data = {"accounts": []}

            # 检查是否已存在相同邮箱的账号
            existing_emails = {acc.get("email") for acc in data["accounts"]}
            if account_data.get("email") in existing_emails:
                for i, acc in enumerate(data["accounts"]):
                    if acc.get("email") == account_data.get("email"):
                        data["accounts"][i] = account_data
                        print(f"更新已存在的账号: {account_data.get('email')}")
                        break
            else:
                data["accounts"].append(account_data)
                print(f"添加新账号: {account_data.get('email')}")

            # 保存
            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)

            print(f"账号已保存到: {output_path} (共 {len(data['accounts'])} 个账号)")
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def screenshot_to_base64(screenshot_bytes):
    """将截图转换为 base64"""
    return base64.standard_b64encode(screenshot_bytes).decode("utf-8")


def analyze_click_captcha(api_key, model, screenshot_base64, prompt_text):
    """使用 OpenRouter API 分析点击验证码"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""这是一个验证码图片，是一个 3x3 的图片网格。
提示文字是："{prompt_text}"

请分析这个验证码，告诉我应该点击哪些图片。
图片位置用行列表示，从左上角开始：
- 第1行第1列 = (1,1), 第1行第2列 = (1,2), 第1行第3列 = (1,3)
- 第2行第1列 = (2,1), 第2行第2列 = (2,2), 第2行第3列 = (2,3)
- 第3行第1列 = (3,1), 第3行第2列 = (3,2), 第3行第3列 = (3,3)

请只返回 JSON 格式，例如：
{{"positions": [[1,2], [2,3], [3,1]]}}

不要返回其他内容，只返回 JSON。"""
                    }
                ],
            }
        ],
        "max_tokens": 1024,
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"]


def analyze_slider_captcha(api_key, model, screenshot_base64, image_width):
    """使用 OpenRouter API 分析滑块验证码"""
    url = "https://openrouter.ai/api/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": f"data:image/png;base64,{screenshot_base64}"
                        }
                    },
                    {
                        "type": "text",
                        "text": f"""分析这个滑块验证码图片，图片宽度 {image_width}px。

观察要点：
1. 左侧有一个拼图块（puzzle piece），它的左边缘大约在 x=5~15px
2. 背景图中有一个缺口（gap/notch），颜色比周围更暗或有明显边缘
3. 缺口的形状与拼图块完全匹配

你需要找到：
- 缺口左边缘的精确 X 坐标（不是中心，是左边缘）
- 拼图块左边缘的精确 X 坐标

计算方法：仔细观察缺口区域，找到缺口最左侧的像素位置。

只返回JSON，格式：
{{"gap_x": 缺口左边缘X坐标, "puzzle_x": 拼图块左边缘X坐标}}

示例：如果缺口左边缘在 x=180，拼图块左边缘在 x=8，返回：
{{"gap_x": 180, "puzzle_x": 8}}"""
                    }
                ],
            }
        ],
        "max_tokens": 256,
        "temperature": 0,  # 零温度，最大确定性
    }

    response = requests.post(url, headers=headers, json=payload, timeout=60)
    response.raise_for_status()
    result = response.json()
    return result["choices"][0]["message"]["content"]


def parse_json_response(result):
    """解析可能包含 markdown 代码块的 JSON 响应"""
    clean_result = result.strip()
    if clean_result.startswith("```"):
        lines = clean_result.split("\n")
        clean_result = "\n".join(lines[1:-1])
    return json.loads(clean_result)


def click_captcha_images(page, positions):
    """根据位置点击验证码图片"""
    clicked = []
    for row, col in positions:
        selector = f".bcap-image{row}{col}"
        try:
            element = page.query_selector(selector)
            if element:
                element.click()
                clicked.append((row, col))
                print(f"  点击了位置 ({row},{col})")
                time.sleep(random.uniform(0.2, 0.5))
        except Exception as e:
            print(f"  点击位置 ({row},{col}) 失败: {e}")
    return clicked


def simulate_human_drag(page, slider_element, distance):
    """模拟人类拖动滑块 - 更自然的轨迹"""
    box = slider_element.bounding_box()
    if not box:
        return False

    start_x = box["x"] + box["width"] / 2
    start_y = box["y"] + box["height"] / 2

    # 移动到滑块位置
    page.mouse.move(start_x, start_y)
    time.sleep(random.uniform(0.1, 0.2))

    # 按下鼠标
    page.mouse.down()
    time.sleep(random.uniform(0.05, 0.1))

    # 随机步数，模拟人类不规则操作
    steps = random.randint(20, 30)
    for i in range(steps):
        progress = (i + 1) / steps
        # 使用贝塞尔曲线缓动（ease-out）
        eased = progress * (2 - progress)
        target_x = start_x + distance * eased
        # 添加微小随机抖动
        jitter_y = random.uniform(-0.5, 0.5)
        page.mouse.move(target_x, start_y + jitter_y)
        # 随机间隔
        time.sleep(random.uniform(0.01, 0.03))

    # 确保到达精确位置
    page.mouse.move(start_x + distance, start_y)
    time.sleep(random.uniform(0.1, 0.15))

    # 松开鼠标
    page.mouse.up()
    return True


def detect_captcha_type(page):
    """检测验证码类型"""
    # 点击验证码（图片选择）
    click_modal = page.query_selector(".bcap-modal")
    if click_modal:
        return "click", click_modal

    # 滑块验证码 - 币安使用 bs- 前缀
    slider_selectors = [
        ".bs-modal",  # 币安滑块弹窗
        ".bs-slide-container",  # 币安滑块容器
        ".verify-slider",  # 验证滑块
        ".bcap-slider",
        ".bcap-drag",
        "[class*='slider-container']",
        "[class*='slide-verify']",
        "[class*='captcha-slider']",
        ".geetest_slider",
    ]
    for selector in slider_selectors:
        slider = page.query_selector(selector)
        if slider and slider.is_visible():
            print(f"检测到滑块验证码: {selector}")
            return "slider", slider

    slider_bg = page.query_selector("[class*='slider-bg'], [class*='captcha-bg'], .bcap-bg, .bs-main-image")
    if slider_bg:
        return "slider", slider_bg

    # 检查是否有验证码弹窗
    popup = page.query_selector(".bcapc-popup, .bs-popup")
    if popup and popup.is_visible():
        print(f"检测到验证码弹窗")
        return "slider", popup

    return "unknown", None


def solve_captcha(page, api_key, model, max_attempts=3, email_addr="", retry_mode="fast", max_rounds=1, reload_url=None):
    """解决验证码（支持多轮快速重试）"""
    fast_mode = retry_mode == "fast"

    rate_limit_signatures = [
        "too_many_attempts", "尝试次数过多", "cap_too_many", "cap_too_many_attempts",
        "208075", "认证失败，请刷新页面后重试", "$e.execute is not a function"
    ]

    for round_idx in range(max_rounds):
        if round_idx > 0 and reload_url:
            try:
                print(f"进入第 {round_idx + 1}/{max_rounds} 轮，重开登录页: {reload_url}")
                page.goto(reload_url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(random.randint(1200, 1800) if fast_mode else random.randint(2200, 3000))
            except Exception as e:
                print(f"重开登录页失败: {e}")
                continue

        for attempt in range(max_attempts):
            print(f"\n--- 验证码轮次 {round_idx + 1}/{max_rounds}，尝试 {attempt + 1}/{max_attempts} ---")
            if attempt > 0:
                time.sleep(random.uniform(0.2, 0.6) if fast_mode else random.uniform(2.0, 4.0))

            page_text = page.inner_text("body") if page.query_selector("body") else ""
            if any(sig.lower() in page_text.lower() for sig in rate_limit_signatures):
                print("[WARNING] 检测到验证码限流/异常签名")
                try:
                    dismiss_btn = page.query_selector("button:has-text('已知晓'), button:has-text('Got it'), button:has-text('OK')")
                    if dismiss_btn and dismiss_btn.is_visible():
                        dismiss_btn.click()
                        page.wait_for_timeout(random.randint(300, 700) if fast_mode else random.randint(800, 1200))
                except:
                    pass
                if round_idx == max_rounds - 1:
                    return "rate_limited"
                break

            if attempt == 0:
                save_debug_artifacts(page, f"captcha_attempt_r{round_idx+1}_{attempt+1}", email_addr)

            captcha_type, captcha_element = detect_captcha_type(page)
            if captcha_type == "unknown":
                print("未检测到验证码，可能已通过")
                return True

            if captcha_type == "click":
                prompt_element = page.query_selector("#tagLabel, .bcap-text-message-title2")
                prompt_text = prompt_element.inner_text() if prompt_element else "选择正确的图片"
                screenshot_bytes = captcha_element.screenshot()
                screenshot_base64 = screenshot_to_base64(screenshot_bytes)
                try:
                    result = analyze_click_captcha(api_key, model, screenshot_base64, prompt_text)
                    positions = parse_json_response(result).get("positions", [])
                    if positions:
                        click_captcha_images(page, positions)
                        page.wait_for_timeout(random.randint(600, 900) if fast_mode else random.randint(1200, 1800))
                        # 点击验证码确认按钮；部分页面文案为“验证/确认/提交”
                        verify_clicked = False
                        verify_selectors = [
                            ".bcap-verify-button",
                            "button:has-text('验证')",
                            "button:has-text('确认')",
                            "button:has-text('提交')",
                            "button:has-text('Verify')",
                            "button:has-text('Confirm')",
                            "[class*='verify']",
                        ]
                        for selector in verify_selectors:
                            try:
                                verify_btn = page.query_selector(selector)
                                if verify_btn and verify_btn.is_visible():
                                    verify_btn.click()
                                    verify_clicked = True
                                    print(f"点击了验证码确认按钮: {selector}")
                                    break
                            except:
                                pass

                        # 无按钮时尝试回车触发提交
                        if not verify_clicked:
                            try:
                                page.keyboard.press("Enter")
                                print("未找到确认按钮，尝试回车提交")
                            except:
                                pass

                        page.wait_for_timeout(random.randint(700, 1100) if fast_mode else random.randint(1500, 2200))
                        if not page.query_selector(".bcap-modal"):
                            return True
                except Exception as e:
                    print(f"识别失败: {e}")

            if captcha_type == "slider":
                slider_bg = page.query_selector(".bs-main-image, [class*='slider-bg'], [class*='captcha-bg'], .bcap-bg, [class*='verify-img']")
                if slider_bg:
                    screenshot_bytes = slider_bg.screenshot()
                    box = slider_bg.bounding_box()
                else:
                    screenshot_bytes = captcha_element.screenshot()
                    box = captcha_element.bounding_box()

                image_width = int(box["width"]) if box else 300
                screenshot_base64 = screenshot_to_base64(screenshot_bytes)
                print(f"滑块图已截取并发送AI识别，宽度: {image_width}px")

                try:
                    result = analyze_slider_captcha(api_key, model, screenshot_base64, image_width)
                    result_json = parse_json_response(result)
                    gap_x = result_json.get("gap_x", 0)
                    puzzle_x = result_json.get("puzzle_x", 8)
                    print(f"AI识别坐标: gap_x={gap_x}, puzzle_x={puzzle_x}")
                    if gap_x <= 0:
                        print("[ERROR] AI 未能识别缺口位置")
                        continue

                    slider_btn = None
                    for selector in [".bs-slide-thumb", ".bcap-slider-btn", "[class*='slider-button']", "[class*='drag-btn']", "[class*='slide-thumb']"]:
                        btn = page.query_selector(selector)
                        if btn and btn.is_visible():
                            slider_btn = btn
                            break

                    if not slider_btn:
                        print("[ERROR] 未找到滑块按钮")
                        continue

                    simulate_human_drag(page, slider_btn, gap_x - puzzle_x)
                    page.wait_for_timeout(random.randint(900, 1300) if fast_mode else random.randint(1800, 2200))
                    save_debug_artifacts(page, f"after_slide_r{round_idx+1}_{attempt+1}", email_addr)

                    error_text = page.inner_text("body") if page.query_selector("body") else ""
                    if any(sig.lower() in error_text.lower() for sig in rate_limit_signatures):
                        if round_idx == max_rounds - 1:
                            return "rate_limited"
                        break
                    if not page.query_selector(".bs-modal, .bcapc-popup"):
                        return True
                except Exception as e:
                    print(f"识别失败: {e}")

            page.wait_for_timeout(random.randint(500, 900) if fast_mode else random.randint(1000, 1500))

    print("验证码尝试次数已用完")
    return False


def get_email_verification_code(imap_host, imap_port, username, password, timeout=90, initial_count=0):
    """通过 IMAP 获取币安验证码邮件"""
    print(f"连接 IMAP: {imap_host}:{imap_port}, 用户: {username}")
    print(f"等待新邮件 (初始邮件数: {initial_count})...")

    start_time = time.time()

    while time.time() - start_time < timeout:
        try:
            mail = imaplib.IMAP4_SSL(imap_host, imap_port)
            mail.login(username, password)
            mail.select("INBOX")

            _, messages = mail.search(None, "ALL")
            if not messages[0]:
                mail.logout()
                print("邮箱为空，等待...")
                time.sleep(3)
                continue

            mail_ids = messages[0].split()
            current_count = len(mail_ids)

            if current_count <= initial_count:
                mail.logout()
                print(f"等待新邮件... (当前: {current_count})")
                time.sleep(3)
                continue

            new_mail_ids = mail_ids[initial_count:]
            print(f"检测到 {len(new_mail_ids)} 封新邮件")

            for mail_id in reversed(new_mail_ids):
                _, msg_data = mail.fetch(mail_id, "(RFC822)")

                for response_part in msg_data:
                    if isinstance(response_part, tuple):
                        msg = email.message_from_bytes(response_part[1])
                        from_addr = msg.get("From", "").lower()

                        if "binance" not in from_addr:
                            continue

                        # 获取邮件内容
                        body = ""
                        html_body = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                content_type = part.get_content_type()
                                if content_type == "text/plain":
                                    try:
                                        payload = part.get_payload(decode=True)
                                        charset = part.get_content_charset() or "utf-8"
                                        body = payload.decode(charset, errors="ignore")
                                    except:
                                        pass
                                elif content_type == "text/html":
                                    try:
                                        payload = part.get_payload(decode=True)
                                        charset = part.get_content_charset() or "utf-8"
                                        html_body = payload.decode(charset, errors="ignore")
                                    except:
                                        pass
                        else:
                            try:
                                payload = msg.get_payload(decode=True)
                                charset = msg.get_content_charset() or "utf-8"
                                content = payload.decode(charset, errors="ignore")
                                if "<html" in content.lower():
                                    html_body = content
                                else:
                                    body = content
                            except:
                                pass

                        # 从 HTML 提取验证码
                        if not body and html_body:
                            verification_section = re.search(
                                r'(验证码|激活码|code)[^>]*>[\s\S]{0,200}?>(\d{6})<',
                                html_body, re.IGNORECASE
                            )
                            if verification_section:
                                code = verification_section.group(2)
                                print(f"从HTML提取验证码: {code}")
                                mail.logout()
                                return code

                            body = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_body, flags=re.IGNORECASE)
                            body = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', body, flags=re.IGNORECASE)
                            body = re.sub(r'<[^>]+>', ' ', body)
                            body = re.sub(r'&nbsp;', ' ', body)
                            body = re.sub(r'\s+', ' ', body)

                        if not body:
                            continue

                        # 提取验证码
                        code_patterns = [
                            r'账户验证码[：:\s]*(\d{6})',
                            r'验证码[：:\s]*(\d{6})',
                            r'激活码[：:\s]*(\d{6})',
                        ]

                        for pattern in code_patterns:
                            match = re.search(pattern, body, re.IGNORECASE)
                            if match:
                                code = match.group(1)
                                if code != "000000":
                                    print(f"找到验证码: {code}")
                                    mail.logout()
                                    return code

            mail.logout()

        except Exception as e:
            print(f"IMAP 错误: {e}")

        time.sleep(3)

    print("获取邮件验证码超时")
    return None


def extract_cookies_and_csrf(context):
    """提取 cookies 和 csrftoken（通过 cr00 的 MD5 计算）"""
    cookies = context.cookies()

    # 生成 cookie 字符串
    cookie_string = "; ".join([
        f"{c['name']}={c['value']}" for c in cookies
        if "binance" in c.get("domain", "")
    ])

    # 构建 cookie map
    cookie_map = {c["name"]: c["value"] for c in cookies if "binance" in c.get("domain", "")}

    # 通过 cr00 计算 csrftoken
    csrftoken = None
    if "cr00" in cookie_map:
        csrftoken = hashlib.md5(cookie_map["cr00"].encode()).hexdigest()
        print(f"cr00: {cookie_map['cr00'][:20]}...")
        print(f"csrftoken (md5): {csrftoken}")
    else:
        # 兜底：尝试从 cookie 中直接获取 csrftoken
        csrftoken = cookie_map.get("csrftoken")
        if csrftoken:
            print(f"csrftoken (cookie): {csrftoken}")
        else:
            print("警告: 未找到 cr00，无法计算 csrftoken")

    return cookie_string, csrftoken


def cleanup_screenshots(screenshots_dir):
    """清理截图目录"""
    if screenshots_dir.exists():
        for f in screenshots_dir.glob("*.png"):
            try:
                f.unlink()
            except:
                pass


# ========== URL 状态机辅助函数 ==========

def click_button(page, texts, timeout=3000):
    """点击包含指定文本的按钮"""
    for text in texts:
        try:
            btn = page.query_selector(f"button:has-text('{text}')")
            if btn and btn.is_visible():
                btn.click()
                print(f"点击了按钮: {text}")
                return True
        except:
            pass
    return False


def get_initial_mail_count(imap_host, imap_port, email_addr, email_password):
    """获取当前邮件数量"""
    try:
        mail = imaplib.IMAP4_SSL(imap_host, imap_port)
        mail.login(email_addr, email_password)
        mail.select("INBOX")
        _, messages = mail.search(None, "ALL")
        count = len(messages[0].split()) if messages[0] else 0
        mail.logout()
        print(f"当前邮件数量: {count}")
        return count
    except Exception as e:
        print(f"获取邮件数量失败: {e}")
        return 0


def dismiss_cookie_popup(page):
    """关闭 Cookie 弹窗"""
    try:
        # 尝试点击"接受所有 Cookie"或"全部拒绝"
        cookie_btns = [
            "button:has-text('接受所有')",
            "button:has-text('Accept All')",
            "button:has-text('全部拒绝')",
            "button:has-text('Reject All')",
            "#onetrust-accept-btn-handler",
            "#onetrust-reject-all-handler",
        ]
        for selector in cookie_btns:
            try:
                btn = page.query_selector(selector)
                if btn and btn.is_visible():
                    btn.click()
                    print(f"关闭了 Cookie 弹窗: {selector}")
                    page.wait_for_timeout(random.randint(400, 600))
                    return True
            except:
                pass
    except:
        pass
    return False


def input_email(page, email_addr):
    """输入邮箱 - 模拟人类打字"""
    # 先关闭 Cookie 弹窗
    dismiss_cookie_popup(page)
    page.wait_for_timeout(random.randint(400, 600))

    # 币安登录页的输入框选择器
    email_selectors = [
        "input[data-e2e='input-username']",
        "input[name='username']",
        "input[placeholder*='邮箱']",
        "input[placeholder*='手机']",
        "input[name='email']",
        "input[type='email']",
        "input[id*='email']",
    ]

    email_input = None
    for selector in email_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                email_input = el
                print(f"找到邮箱输入框: {selector}")
                break
        except:
            pass

    if email_input:
        # 点击输入框
        email_input.click()
        time.sleep(random.uniform(0.2, 0.4))
        email_input.fill("")
        time.sleep(random.uniform(0.1, 0.2))
        # 使用 type() 逐字输入，带随机延迟模拟人类打字
        email_input.type(email_addr, delay=random.randint(50, 100))
        print(f"输入邮箱: {email_addr}")
    else:
        # 兜底：找第一个文本输入框
        inputs = page.query_selector_all("input[type='text'], input:not([type])")
        if inputs:
            inputs[0].click()
            time.sleep(random.uniform(0.2, 0.4))
            inputs[0].type(email_addr, delay=random.randint(50, 100))
            print(f"使用兜底输入框输入邮箱: {email_addr}")
        else:
            print(f"[ERROR] 未找到邮箱输入框!")


def input_password(page, password):
    """输入密码 - 模拟人类打字"""
    password_input = page.query_selector("input[name='password'], input[type='password']")
    if password_input:
        password_input.click()
        time.sleep(random.uniform(0.1, 0.2))
        # 使用 type() 逐字输入，带随机延迟
        password_input.type(password, delay=random.randint(30, 80))
        print("密码已输入")
        return True
    return False


def solve_captcha_if_present(page, api_key, model, email_addr="", captcha_config=None, reload_url=None):
    """如果存在验证码则解决

    返回值:
    - True: 验证码通过或无验证码
    - False: 验证码失败
    - "rate_limited": IP 被风控
    """
    captcha_type, _ = detect_captcha_type(page)
    if captcha_type != "unknown":
        print(f"检测到{captcha_type}验证码，开始识别...")
        captcha_config = captcha_config or {}
        return solve_captcha(
            page,
            api_key,
            model,
            max_attempts=captcha_config.get("max_attempts_per_round", 5),
            email_addr=email_addr,
            retry_mode=captcha_config.get("retry_mode", "fast"),
            max_rounds=captcha_config.get("max_rounds", 3),
            reload_url=reload_url,
        )
    return True


def handle_email_verification(page, imap_host, imap_port, email_addr, email_password, initial_count):
    """处理邮件验证码输入"""
    code_input_selectors = [
        "input[maxlength='6']",
        "input[autocomplete='one-time-code']",
        "input[placeholder*='验证']",
        "input[placeholder*='code']",
        "input[type='tel']",
    ]

    code_input = None
    for selector in code_input_selectors:
        try:
            el = page.query_selector(selector)
            if el and el.is_visible():
                code_input = el
                print(f"找到验证码输入框: {selector}")
                break
        except:
            pass

    if not code_input:
        print("未找到验证码输入框")
        return False

    email_code = get_email_verification_code(
        imap_host, imap_port, email_addr, email_password,
        timeout=90, initial_count=initial_count
    )

    if not email_code:
        print("未能获取邮件验证码")
        return False

    print(f"输入验证码: {email_code}")
    code_input.fill(email_code)
    page.wait_for_timeout(random.randint(800, 1200))

    # 点击提交按钮
    if not click_button(page, ["提交", "Submit", "确认", "Confirm", "继续", "Continue", "验证", "Verify"]):
        print("未找到提交按钮，尝试按回车...")
        code_input.press("Enter")

    return True


def need_register(page):
    """检查是否需要注册"""
    page_text = page.inner_text("body") if page.query_selector("body") else ""
    return (
        "未注册" in page_text or
        "not registered" in page_text.lower() or
        "没有账号" in page_text or
        "don't have an account" in page_text.lower() or
        "未找到" in page_text or
        "not found" in page_text.lower() or
        "找不到" in page_text or
        "账号不存在" in page_text or
        "account does not exist" in page_text.lower()
    )


def goto_with_retry(page, url, max_retries=3, email_addr=""):
    """带重试的页面跳转"""
    for attempt in range(max_retries):
        try:
            print(f"正在访问: {url}")
            page.goto(url, wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
            page.wait_for_timeout(random.randint(1800, 2200))

            # 保存页面加载后的状态
            save_debug_artifacts(page, f"page_loaded_attempt{attempt+1}", email_addr)

            # 检查是否有错误提示
            body_text = ""
            try:
                body = page.query_selector("body")
                if body:
                    body_text = body.inner_text()
            except:
                pass

            # 检测常见错误
            error_keywords = [
                "网络连接失败", "network error", "连接失败", "connection failed",
                "请稍后重试", "please try again", "操作失败", "operation failed",
                "403", "forbidden", "blocked", "拒绝访问",
                "cap_too_many_attempts", "208075", "$e.execute is not a function"
            ]
            for keyword in error_keywords:
                if keyword.lower() in body_text.lower():
                    print(f"检测到错误关键词: {keyword}")
                    save_debug_artifacts(page, f"error_detected_{keyword.replace(' ', '_')}", email_addr)

            return True
        except Exception as e:
            print(f"页面加载失败 (尝试 {attempt + 1}/{max_retries}): {e}")
            print(f"异常详情: {traceback.format_exc()}")
            if attempt < max_retries - 1:
                time.sleep(2)
    return False


def login_with_url_state(page, email_addr, email_password, config):
    """基于 URL 状态机的登录流程

    URL 状态流程:
    /login → /login/password → /login/mfa → /login/stay-signed-in → /my/dashboard

    返回值:
    - True: 登录成功
    - "need_register": 需要注册
    - "rate_limited": IP 被风控
    - False: 登录失败
    """
    api_key = config["openrouter_api_key"]
    model = config["models"][0]
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    login_start_url = config.get("login", {}).get("start_url", "https://accounts.binance.com/zh-CN/login")
    captcha_config = config.get("captcha", {})

    print("\n[URL状态机] 打开登录页面...")
    if not goto_with_retry(page, login_start_url, email_addr=email_addr):
        print("登录页面加载失败")
        save_debug_artifacts(page, "login_page_failed", email_addr)
        return False

    # 预先获取邮件数量
    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)

    max_iterations = 20
    for iteration in range(max_iterations):
        page.wait_for_timeout(random.randint(1800, 2500))
        url = page.url
        print(f"\n[迭代 {iteration + 1}] 当前 URL: {url}")

        # 每次迭代保存调试信息
        if iteration < 3:  # 只保存前3次迭代，避免太多文件
            save_debug_artifacts(page, f"iteration_{iteration+1}", email_addr)

        # 检查页面是否有错误提示
        try:
            body = page.query_selector("body")
            if body:
                body_text = body.inner_text()
                # 检测风控/网络错误
                risk_signatures = [
                    "网络连接失败", "操作失败", "PRECHECK",
                    "cap_too_many_attempts", "208075",
                    "认证失败，请刷新页面后重试", "$e.execute is not a function"
                ]
                if any(sig.lower() in body_text.lower() for sig in risk_signatures):
                    print(f"检测到错误页面!")
                    save_debug_artifacts(page, "error_page", email_addr)
                    # 打印错误信息的前500字符
                    print(f"页面内容: {body_text[:500]}")

                    # 尝试点击"已知晓"按钮关闭弹窗
                    dismiss_btns = [
                        "button:has-text('已知晓')",
                        "button:has-text('Got it')",
                        "button:has-text('OK')",
                        "button:has-text('确定')",
                        "button:has-text('关闭')",
                        "button:has-text('Close')",
                    ]
                    for selector in dismiss_btns:
                        try:
                            btn = page.query_selector(selector)
                            if btn and btn.is_visible():
                                btn.click()
                                print(f"点击了关闭按钮: {selector}")
                                page.wait_for_timeout(random.randint(800, 1200))
                                break
                        except:
                            pass

                    # 如果是网络错误，可能需要刷新页面重试
                    if ("网络连接失败" in body_text or "208075" in body_text) and iteration < 5:
                        print("尝试刷新页面...")
                        page.reload()
                        page.wait_for_timeout(random.randint(2500, 3500))
                        continue
        except Exception as e:
            print(f"检查页面内容失败: {e}")

        # 状态: stay-signed-in 页面
        if "/login/stay-signed-in" in url:
            print("[状态] stay-signed-in - 点击'是'按钮")
            click_button(page, ["是", "Yes", "确定", "OK"])
            continue

        # 状态: MFA 验证页面
        if "/login/mfa" in url:
            print("[状态] mfa - 处理邮件验证码")
            url_before = url
            if not handle_email_verification(page, imap_host, imap_port, email_addr, email_password, initial_mail_count):
                return False
            page.wait_for_timeout(random.randint(2500, 3500))

            # 检查提交验证码后 URL 是否变化
            url_after = page.url
            if url_after == url_before or "/login/mfa" in url_after:
                print("[状态] 验证码提交后 URL 未变化，账号可能不存在，转注册流程")
                return "need_register"
            continue

        # 状态: 密码页面
        if "/login/password" in url:
            print("[状态] password - 输入密码")
            # 在提交密码前记录邮件数（提交后可能触发验证码）
            initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
            input_password(page, email_password)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            page.wait_for_timeout(random.randint(1800, 2200))
            captcha_result = solve_captcha_if_present(
                page, api_key, model, email_addr,
                captcha_config=captcha_config, reload_url=login_start_url
            )
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            continue

        # 状态: 登录首页（输入邮箱）
        if "/login" in url and "/login/" not in url:
            # 先检查是否有验证码弹窗，如果有则处理验证码而不是输入邮箱
            captcha_type, _ = detect_captcha_type(page)
            if captcha_type != "unknown":
                print(f"[状态] 检测到{captcha_type}验证码弹窗，处理验证码...")
                captcha_result = solve_captcha(
                    page,
                    api_key,
                    model,
                    max_attempts=captcha_config.get("max_attempts_per_round", 5),
                    email_addr=email_addr,
                    retry_mode=captcha_config.get("retry_mode", "fast"),
                    max_rounds=captcha_config.get("max_rounds", 3),
                    reload_url=login_start_url,
                )
                if captcha_result == "rate_limited":
                    print("[ERROR] IP 被风控，请更换代理或等待后重试")
                    return "rate_limited"
                continue

            # 检查邮箱输入框是否已有内容（避免重复输入）
            email_input = page.query_selector("input[data-e2e='input-username'], input[name='username'], input[name='email']")
            if email_input:
                current_value = email_input.input_value()
                if current_value and email_addr in current_value:
                    print("[状态] 邮箱已输入，点击继续...")
                    click_button(page, ["继续", "Continue", "下一步", "Next"])
                    page.wait_for_timeout(random.randint(2500, 3500))
                    continue

            print("[状态] login - 输入邮箱")
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            page.wait_for_timeout(random.randint(2500, 3500))

            # 检查是否需要注册
            if need_register(page):
                print("[状态] 检测到账号未注册")
                return "need_register"

            captcha_result = solve_captcha_if_present(
                page, api_key, model, email_addr,
                captcha_config=captcha_config, reload_url=login_start_url
            )
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            continue

        # 状态: 登录成功（dashboard 或其他非登录页面）
        if "/my/" in url or ("binance.com" in url and "login" not in url and "register" not in url):
            print("[状态] 登录成功!")
            return True

        # 未知状态，检查是否需要注册
        if need_register(page):
            print("[状态] 检测到账号未注册")
            return "need_register"

        # 检查是否有验证码需要处理
        captcha_result = solve_captcha_if_present(
            page, api_key, model, email_addr,
            captcha_config=captcha_config, reload_url=login_start_url
        )
        if captcha_result == "rate_limited":
            print("[ERROR] IP 被风控，请更换代理或等待后重试")
            return "rate_limited"

    print("登录流程超过最大迭代次数")
    return False


def register_with_url_state(page, email_addr, email_password, config):
    """基于 URL 状态机的注册流程

    URL 状态流程:
    /register → /register/verification-new-register → /register/register-set-password → /invite → /my/dashboard

    返回值:
    - True: 注册成功
    - False: 注册失败
    """
    api_key = config["openrouter_api_key"]
    model = config["models"][0]
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]

    print("\n[注册状态机] 打开注册页面...")
    if not goto_with_retry(page, "https://www.binance.com/zh-CN/register", email_addr=email_addr):
        print("注册页面加载失败")
        save_debug_artifacts(page, "register_page_failed", email_addr)
        return False

    # 预先获取邮件数量
    initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)

    max_iterations = 20
    for iteration in range(max_iterations):
        page.wait_for_timeout(random.randint(1800, 2500))
        url = page.url
        print(f"\n[注册迭代 {iteration + 1}] 当前 URL: {url}")

        # 每次迭代保存调试信息
        if iteration < 3:
            save_debug_artifacts(page, f"register_iteration_{iteration+1}", email_addr)

        # 状态: 邀请页面（注册完成前的最后一步）
        if "/invite" in url:
            print("[状态] invite - 点击下一步")
            click_button(page, ["下一步", "Next", "跳过", "Skip"])
            continue

        # 状态: 设置密码页面
        if "/register/register-set-password" in url or "/register-set-password" in url:
            print("[状态] set-password - 输入密码")
            input_password(page, email_password)
            page.wait_for_timeout(random.randint(400, 600))
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            page.wait_for_timeout(random.randint(1800, 2200))
            captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            continue

        # 状态: 邮箱验证码页面
        if "/register/verification" in url or "/verification-new-register" in url:
            print("[状态] verification - 处理邮件验证码")
            if not handle_email_verification(page, imap_host, imap_port, email_addr, email_password, initial_mail_count):
                return False
            page.wait_for_timeout(random.randint(2500, 3500))
            continue

        # 状态: 注册首页（输入邮箱）
        if "/register" in url and "/register/" not in url:
            print("[状态] register - 输入邮箱")
            input_email(page, email_addr)
            page.wait_for_timeout(random.randint(400, 600))

            # 勾选创建账户复选框
            try:
                checkbox = page.query_selector("input[type='checkbox']")
                if checkbox and not checkbox.is_checked():
                    checkbox.click()
                    print("勾选了创建账户复选框")
            except:
                pass

            page.wait_for_timeout(random.randint(400, 600))
            # 记录邮件数（点击继续后会发送验证码）
            initial_mail_count = get_initial_mail_count(imap_host, imap_port, email_addr, email_password)
            click_button(page, ["继续", "Continue", "下一步", "Next"])
            page.wait_for_timeout(random.randint(2500, 3500))
            captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
            if captcha_result == "rate_limited":
                print("[ERROR] IP 被风控，请更换代理或等待后重试")
                return "rate_limited"
            continue

        # 状态: 注册成功（dashboard 或其他非注册页面）
        if "/my/" in url or ("binance.com" in url and "register" not in url and "login" not in url):
            print("[状态] 注册成功!")
            return True

        # 检查是否有验证码需要处理
        captcha_result = solve_captcha_if_present(page, api_key, model, email_addr)
        if captcha_result == "rate_limited":
            print("[ERROR] IP 被风控，请更换代理或等待后重试")
            return "rate_limited"

    print("注册流程超过最大迭代次数")
    return False


def register_account(email_addr, email_password, config):
    """登录或注册账号（使用 URL 状态机）"""
    api_key = config["openrouter_api_key"]
    model = config["models"][0]
    imap_host = config["imap_host"]
    imap_port = config["imap_port"]
    output_file = config["output_file"]
    headless = config.get("headless", False)
    browser_config = config.get("browser", {})
    use_builtin_chromium = browser_config.get("use_builtin_chromium", True)

    # 代理配置
    proxy_config = config.get("proxy", {})
    # 按当前策略默认不使用代理，除非显式打开
    proxy_enabled = proxy_config.get("enabled", False)

    base_dir = Path(__file__).parent
    screenshots_dir = base_dir / "screenshots"
    screenshots_dir.mkdir(exist_ok=True)

    print(f"\n{'='*60}")
    print(f"开始处理: {email_addr}")
    print(f"浏览器引擎: {'Playwright Chromium' if use_builtin_chromium else 'Chromium'}")
    print("浏览器会话: 每账号全新上下文")
    if proxy_enabled:
        print(f"代理: {proxy_config.get('server', 'N/A')}")
    print(f"{'='*60}")

    context = None
    browser = None
    with sync_playwright() as p:
        # 构建浏览器启动参数（Playwright 内置 Chromium + 全新会话）
        launch_args = {
            "headless": headless,
            "slow_mo": random.randint(50, 100),  # 随机化操作延迟
            "args": [
                "--disable-infobars",
                "--no-sandbox",
                "--disable-dev-shm-usage",
                "--disable-extensions",
                "--disable-gpu",
                "--lang=zh-CN",
                "--disable-features=FedCm,IdentityCredentialManagement",
            ]
        }

        # 代理设置
        proxy_settings = None
        if proxy_enabled and proxy_config.get("server"):
            server = proxy_config["server"]
            if not server.startswith("http://") and not server.startswith("https://") and not server.startswith("socks"):
                server = f"http://{server}"
            proxy_settings = {"server": server}
            if proxy_config.get("username"):
                proxy_settings["username"] = proxy_config["username"]
            if proxy_config.get("password"):
                proxy_settings["password"] = proxy_config["password"]
            print(f"使用代理: {server}")
            launch_args["proxy"] = proxy_settings

        # 默认使用 Playwright 内置浏览器，不指定 channel
        browser = p.chromium.launch(**launch_args)
        context = browser.new_context(
            viewport={"width": 1280, "height": 800},
            locale="zh-CN",
            ignore_https_errors=False,
        )
        page = context.new_page()

        try:
            # 使用 URL 状态机登录
            result = login_with_url_state(page, email_addr, email_password, config)

            if result == "rate_limited":
                print("\n[ERROR] IP 被风控，建议：")
                print("  1. 启用代理 (config.json 中设置 proxy.enabled = true)")
                print("  2. 更换代理 IP")
                print("  3. 等待一段时间后重试")
                return False

            if result == "need_register":
                # 使用 URL 状态机注册
                print("\n检测到账号未注册，启动注册流程...")
                reg_result = register_with_url_state(page, email_addr, email_password, config)
                if reg_result == "rate_limited":
                    print("\n[ERROR] IP 被风控，建议启用或更换代理")
                    return False
                if not reg_result:
                    print("注册失败")
                    return False

            elif result:
                # 登录成功，继续提取 cookie
                pass
            else:
                # 登录失败
                print("登录失败")
                return False

            # ========== 访问 dashboard 并提取 cookie ==========
            print("\n访问 dashboard...")
            try:
                page.goto("https://www.binance.com/zh-CN/my/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(random.randint(2500, 3500))
            except Exception as e:
                print(f"访问 dashboard 失败: {e}")

            # 提取 cookie 和 csrftoken
            print("\n提取 cookie 和 csrftoken...")
            cookie_string, csrftoken = extract_cookies_and_csrf(context)

            if cookie_string and csrftoken:
                account_data = {
                    "name": f"账号_{email_addr.split('@')[0]}",
                    "email": email_addr,
                    "cookie": cookie_string,
                    "csrftoken": csrftoken,
                    "enabled": True
                }
                save_registered_account(output_file, account_data)
                print(f"\n处理成功: {email_addr}")
                print(f"csrftoken: {csrftoken}")
                return True
            else:
                print("未能获取有效的 cookie 或 csrftoken")
                return False

        except Exception as e:
            print(f"处理过程出错: {e}")
            return False
        finally:
            if context:
                context.close()
            if browser:
                browser.close()


def process_account(args):
    """单个账号处理（进程入口）"""
    account, config, index = args
    email_addr, password = account

    # 错开启动时间，避免同时请求
    delay = index * 1.5  # 每个进程间隔 1.5 秒启动
    if delay > 0:
        print(f"[{email_addr}] 等待 {delay:.1f} 秒后启动...")
        time.sleep(delay)

    try:
        return email_addr, register_account(email_addr, password, config)
    except Exception as e:
        print(f"处理 {email_addr} 出错: {e}")
        return email_addr, False


def signal_handler(signum, frame):
    """处理中断信号，终止所有子进程"""
    # 重置信号处理器，避免递归
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    print("\n\n收到中断信号，正在终止所有进程...")
    global executor_ref
    if executor_ref:
        executor_ref.shutdown(wait=False, cancel_futures=True)

    # 强制终止所有子进程
    import psutil
    current_process = psutil.Process()
    children = current_process.children(recursive=True)
    for child in children:
        try:
            child.terminate()
        except:
            pass

    # 等待子进程终止
    psutil.wait_procs(children, timeout=3)

    # 强制杀死未终止的子进程
    for child in children:
        try:
            if child.is_running():
                child.kill()
        except:
            pass

    os._exit(1)


def main():
    global executor_ref

    # 注册信号处理
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    config = load_config()
    accounts = load_accounts(config["accounts_file"])

    max_workers = config.get("max_workers", 10)
    headless = config.get("headless", False)

    print(f"共加载 {len(accounts)} 个账号")
    print(f"进程数: {max_workers}, 无头模式: {headless}")

    base_dir = Path(__file__).parent
    screenshots_dir = base_dir / "screenshots"

    success_count = 0
    fail_count = 0

    # 准备参数，添加索引用于错开启动
    tasks = [(acc, config, i) for i, acc in enumerate(accounts)]

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor_ref = executor
            futures = {
                executor.submit(process_account, task): task[0]
                for task in tasks
            }

            for future in as_completed(futures):
                try:
                    email_addr, success = future.result()
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                    print(f"进度: {success_count + fail_count}/{len(accounts)} (成功: {success_count}, 失败: {fail_count})")
                except Exception as e:
                    fail_count += 1
                    print(f"任务异常: {e}")
    except KeyboardInterrupt:
        print("\n用户中断，正在清理...")
    finally:
        executor_ref = None

    # 最终清理截图
    cleanup_screenshots(screenshots_dir)

    print(f"\n\n{'='*60}")
    print(f"批量注册完成!")
    print(f"成功: {success_count}, 失败: {fail_count}")
    print(f"{'='*60}")


if __name__ == "__main__":
    main()
