import argparse
import os
import random
import shutil
import signal
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .config import load_config
from .orchestrator import register_account, warmup_cache, MASTER_CACHE_DIR, CACHE_DIR
from .creator_api_quota import initialize_creator_api_quota
from .account_storage import (
    append_account_result,
    load_accounts,
    remove_account_from_file,
)
from .screenshot_storage import cleanup_screenshots
from .logger import get_logger_manager
from .results import AccountResult, AccountStatus

executor_ref = None


def _sleep_random_delay(config, *, min_key, max_key, label, short_email):
    runtime_cfg = config["runtime"]
    min_delay = runtime_cfg[min_key]
    max_delay = runtime_cfg[max_key]
    delay = random.uniform(min_delay, max_delay) if max_delay > min_delay else float(min_delay)
    print(f"[{short_email}] {label}，等待 {delay:.1f}s")
    time.sleep(delay)


def _is_static_proxy_mode(config) -> bool:
    """判断当前配置是否使用固定静态代理。

    参数:
        config: 项目运行配置字典。
    返回值:
        bool: 使用静态代理返回 True，否则返回 False。
    """
    return config["proxy"]["mode"].strip().lower() == "static"


def process_account(args):
    base_dir, account, config, worker_id = args
    email_addr, password = account
    short_email = email_addr.split("@")[0]
    last_status = AccountStatus.FAILED
    max_retries = config["max_login_retries"]

    for attempt in range(max_retries):
        try:
            automation_result = register_account(base_dir, email_addr, password, config, worker_id=worker_id)
            # 兼容外部注入的旧式驱动（测试/第三方调用），正式编排始终返回 AutomationResult。
            status = automation_result.status if hasattr(automation_result, "status") else automation_result
            last_status = status
            if status is AccountStatus.SUCCESS or status.is_terminal_without_retry:
                return AccountResult(email_addr, password, status).to_process_tuple()
            if status.should_retry_proxy:
                if attempt < max_retries - 1:
                    retry_action = "重试固定代理" if _is_static_proxy_mode(config) else "换代理重试"
                    print(f"[{short_email}] 代理失败，{retry_action} {attempt + 2}/{max_retries}")
                    _sleep_random_delay(
                        config,
                        min_key="proxy_retry_delay_min_sec",
                        max_key="proxy_retry_delay_max_sec",
                        label="代理重试冷却",
                        short_email=short_email,
                    )
                continue
            if attempt < max_retries - 1:
                print(f"[{short_email}] ⟳ 重试 {attempt + 2}/{max_retries}")
                continue
        except Exception as e:
            print(f"[{short_email}] ✗ 异常: {e}")
            last_status = AccountStatus.FAILED
            if attempt < max_retries - 1:
                print(f"[{short_email}] ⟳ 重试 {attempt + 2}/{max_retries}")
                continue

    return AccountResult(email_addr, password, last_status).to_process_tuple()


def build_account_tasks(base_dir, accounts, config):
    # worker_id is also used for browser cache directories. It must be unique per
    # submitted task because ProcessPoolExecutor does not bind queued tasks to a
    # stable modulo worker slot.
    return [(base_dir, account, config, index) for index, account in enumerate(accounts)]


def finalize_account_result(base_dir, accounts_file, success_file, failed_file, email_addr, password, status: AccountStatus):
    if status.is_success_like:
        append_account_result(success_file, email_addr, password, delimiter="----")
        remove_account_from_file(base_dir, accounts_file, email_addr, password)
        return status.value

    if status.keeps_account_in_queue:
        return status.value

    append_account_result(failed_file, email_addr, password, delimiter="----")
    remove_account_from_file(base_dir, accounts_file, email_addr, password)
    return status.value


def signal_handler(signum, frame):
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)

    print("\n\n收到中断信号，正在终止所有进程...")
    global executor_ref
    if executor_ref:
        executor_ref.shutdown(wait=False, cancel_futures=True)

    try:
        import psutil

        current_process = psutil.Process()
        children = current_process.children(recursive=True)
        for child in children:
            try:
                child.terminate()
            except Exception:
                pass

        psutil.wait_procs(children, timeout=3)

        for child in children:
            try:
                if child.is_running():
                    child.kill()
            except Exception:
                pass
    except Exception:
        pass

    os._exit(1)


def refresh_cache(config, headless=True):
    """刷新缓存：重新预热 master 并删除所有 worker 缓存"""
    print("刷新浏览器缓存...")

    # 删除所有 worker 缓存
    if CACHE_DIR.exists():
        for item in CACHE_DIR.iterdir():
            if item.name.startswith("worker_"):
                print(f"删除 {item.name}...")
                shutil.rmtree(item, ignore_errors=True)

    # 删除旧的 master
    if MASTER_CACHE_DIR.exists():
        print("删除旧的 master 缓存...")
        shutil.rmtree(MASTER_CACHE_DIR, ignore_errors=True)

    # 重新预热
    warmup_cache(proxy_config=config.get("proxy", {}), headless=headless)
    print("缓存刷新完成")


def main():
    global executor_ref

    # 解析命令行参数
    parser = argparse.ArgumentParser(description="Binance 账号处理工具")
    parser.add_argument("--refresh-cache", action="store_true", help="刷新浏览器缓存（重新预热）")
    parser.add_argument("--count", type=int, help="本次最多处理的账号数量")
    args = parser.parse_args()
    if args.count is not None and args.count <= 0:
        parser.error("--count 必须是正整数")

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    base_dir = Path(__file__).resolve().parents[2]
    config = load_config(base_dir, "config/automation.json")
    headless = config["headless"]

    # 如果指定了刷新缓存，执行后退出
    if args.refresh_cache:
        refresh_cache(config, headless=headless)
        return

    accounts = load_accounts(base_dir, config["accounts_file"])
    if args.count is not None:
        accounts = accounts[:args.count]
    initialize_creator_api_quota(base_dir, config)

    runtime_cfg = config.get("runtime", {})
    max_workers = config.get("max_workers", runtime_cfg["max_workers_default"])

    print(f"账号: {len(accounts)} | 进程: {max_workers} | 无头: {headless}")

    # 预热缓存（如果启用且 master 缓存不存在）
    cache_enabled = config["cache"]["enabled"]
    if cache_enabled and not MASTER_CACHE_DIR.exists():
        print("\n首次运行，预热浏览器缓存...")
        warmup_cache(proxy_config=config.get("proxy", {}), headless=headless)
        print("")
    elif not cache_enabled:
        print("本地缓存: 已禁用")

    screenshots_dir = base_dir / "screenshots"
    output_dir = base_dir / "output"
    output_dir.mkdir(exist_ok=True)

    success_file = output_dir / "success_accounts.txt"
    failed_file = output_dir / "failed_accounts.txt"
    accounts_file = config["accounts_file"]

    success_count = 0
    fail_count = 0
    already_registered_count = 0
    need_register_count = 0
    imap_auth_failed_count = 0
    email_verification_required_count = 0
    auth_failed_count = 0
    rate_limited_count = 0
    proxy_failed_count = 0

    tasks = build_account_tasks(base_dir, accounts, config)

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor_ref = executor
            futures = {executor.submit(process_account, task): task for task in tasks}
            for future in as_completed(futures):
                try:
                    email_addr, password, result = future.result()
                    task = futures[future]
                    worker_id = task[3]
                    mode = config["mode"]

                    # 记录结果并处理日志
                    get_logger_manager().record_result(
                        email=email_addr,
                        result=result,
                        mode=mode,
                        worker_id=worker_id,
                    )

                    outcome = finalize_account_result(
                        base_dir,
                        accounts_file,
                        success_file,
                        failed_file,
                        email_addr,
                        password,
                        result,
                    )

                    if outcome == "success":
                        success_count += 1
                    elif outcome == "already_registered":
                        already_registered_count += 1
                    elif outcome == "need_register":
                        need_register_count += 1
                    elif outcome == "imap_auth_failed":
                        imap_auth_failed_count += 1
                    elif outcome == "email_verification_required":
                        email_verification_required_count += 1
                    elif outcome == "auth_failed":
                        auth_failed_count += 1
                    elif outcome == "rate_limited":
                        rate_limited_count += 1
                    elif outcome == "proxy_failed":
                        proxy_failed_count += 1
                    else:
                        fail_count += 1
                    total = (
                        success_count
                        + fail_count
                        + already_registered_count
                        + need_register_count
                        + imap_auth_failed_count
                        + email_verification_required_count
                        + auth_failed_count
                        + rate_limited_count
                        + proxy_failed_count
                    )
                    status = f"进度: {total}/{len(accounts)} | 成功: {success_count} | 失败: {fail_count}"
                    if already_registered_count > 0:
                        status += f" | 已注册: {already_registered_count}"
                    if need_register_count > 0:
                        status += f" | 未注册: {need_register_count}"
                    if imap_auth_failed_count > 0:
                        status += f" | IMAP失败: {imap_auth_failed_count}"
                    if email_verification_required_count > 0:
                        status += f" | 待邮箱验证: {email_verification_required_count}"
                    if auth_failed_count > 0:
                        status += f" | 认证失败: {auth_failed_count}"
                    if rate_limited_count > 0:
                        status += f" | 风控限制: {rate_limited_count}"
                    if proxy_failed_count > 0:
                        status += f" | 代理失败: {proxy_failed_count}"
                    print(status)
                except Exception as e:
                    task = futures[future]
                    _, account, _, worker_id = task
                    email_addr, password = account
                    mode = config["mode"]
                    get_logger_manager().record_result(
                        email=email_addr,
                        result=AccountStatus.FAILED,
                        mode=mode,
                        worker_id=worker_id,
                        extra=f"任务异常: {e}",
                    )
                    finalize_account_result(
                        base_dir,
                        accounts_file,
                        success_file,
                        failed_file,
                        email_addr,
                        password,
                        AccountStatus.FAILED,
                    )
                    fail_count += 1
                    print(f"任务异常: {email_addr} | {e}")
    except KeyboardInterrupt:
        print("\n用户中断，正在清理...")
    finally:
        executor_ref = None

    cleanup_screenshots(screenshots_dir)

    # 实际成功数 = 成功 + 已注册
    total_success = success_count + already_registered_count

    print(f"\n{'='*50}")
    print(f"完成 | 成功: {total_success}（注册成功: {success_count} | 已注册: {already_registered_count}）| 失败: {fail_count}")
    if need_register_count > 0:
        print(f"     未注册(需切换register模式): {need_register_count}")
    if imap_auth_failed_count > 0:
        print(f"     IMAP认证失败: {imap_auth_failed_count}")
    if email_verification_required_count > 0:
        print(f"     待邮箱验证(账号保留队列): {email_verification_required_count}")
    if auth_failed_count > 0:
        print(f"     平台认证失败: {auth_failed_count}")
    if rate_limited_count > 0:
        print(f"     风控限制: {rate_limited_count}")
    if proxy_failed_count > 0:
        print(f"     代理失败(账号保留队列): {proxy_failed_count}")
    print(f"{'='*50}")

    # 写入每日日志汇总（success/ failure/ 目录 + 统计行）
    get_logger_manager().log_daily_summary()
