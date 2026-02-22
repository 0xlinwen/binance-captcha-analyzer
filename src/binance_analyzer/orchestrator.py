import hashlib
import random
from pathlib import Path

from playwright.sync_api import sync_playwright

from .flows import login_with_url_state, register_with_url_state
from .storage import save_registered_account

PAGE_TIMEOUT = 60000


def extract_cookies_and_csrf(page):
    """从 page 的 context 提取 cookies"""
    context = page.context
    cookies = context.cookies()
    cookie_string = "; ".join([f"{c['name']}={c['value']}" for c in cookies if "binance" in c.get("domain", "")])

    cookie_map = {c["name"]: c["value"] for c in cookies if "binance" in c.get("domain", "")}
    csrftoken = None
    if "cr00" in cookie_map:
        csrftoken = hashlib.md5(cookie_map["cr00"].encode()).hexdigest()
        print(f"cr00: {cookie_map['cr00'][:20]}...")
        print(f"csrftoken (md5): {csrftoken}")
    else:
        csrftoken = cookie_map.get("csrftoken")
        if csrftoken:
            print(f"csrftoken (cookie): {csrftoken}")
        else:
            print("警告: 未找到 cr00，无法计算 csrftoken")

    return cookie_string, csrftoken


def register_account(base_dir: Path, email_addr: str, email_password: str, config: dict):
    output_file = config["output_file"]
    headless = config.get("headless", False)
    proxy_config = config.get("proxy", {})
    proxy_enabled = proxy_config.get("enabled", False)

    print(f"\n{'='*60}")
    print(f"开始处理: {email_addr}")
    if proxy_enabled:
        print(f"代理: {proxy_config.get('server', 'N/A')}")
    print(f"{'='*60}")

    browser = None
    with sync_playwright() as p:
        launch_args = {
            "headless": headless,
            "args": [
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox'
            ]
        }

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

        browser = p.chromium.launch(**launch_args)
        page = browser.new_page()

        try:
            result = login_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
            if result == "rate_limited":
                print("\n[ERROR] IP 被风控，建议：")
                print("  1. 启用代理 (config.json 中设置 proxy.enabled = true)")
                print("  2. 更换代理 IP")
                print("  3. 等待一段时间后重试")
                return False

            if result == "need_register":
                print("\n检测到账号未注册，启动注册流程...")
                reg_result = register_with_url_state(page, email_addr, email_password, config, page_timeout=PAGE_TIMEOUT)
                if reg_result == "rate_limited":
                    print("\n[ERROR] IP 被风控，建议启用或更换代理")
                    return False
                if not reg_result:
                    print("注册失败")
                    return False
            elif not result:
                print("登录失败")
                return False

            print("\n访问 dashboard...")
            try:
                page.goto("https://www.binance.com/zh-CN/my/dashboard", wait_until="domcontentloaded", timeout=PAGE_TIMEOUT)
                page.wait_for_timeout(random.randint(2500, 3500))
            except Exception as e:
                print(f"访问 dashboard 失败: {e}")

            print("\n提取 cookie 和 csrftoken...")
            cookie_string, csrftoken = extract_cookies_and_csrf(page)
            if cookie_string and csrftoken:
                account_data = {
                    "name": f"账号_{email_addr.split('@')[0]}",
                    "email": email_addr,
                    "cookie": cookie_string,
                    "csrftoken": csrftoken,
                    "enabled": True,
                }
                save_registered_account(base_dir, output_file, account_data)
                print(f"\n处理成功: {email_addr}")
                print(f"csrftoken: {csrftoken}")
                return True

            print("未能获取有效的 cookie 或 csrftoken")
            return False
        except Exception as e:
            print(f"处理过程出错: {e}")
            return False
        finally:
            if browser:
                browser.close()
