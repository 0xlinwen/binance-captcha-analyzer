import argparse
import os
import shutil
import signal
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .config import load_config
from .orchestrator import register_account, warmup_cache, MASTER_CACHE_DIR, CACHE_DIR
from .storage import cleanup_screenshots, load_accounts

executor_ref = None


def process_account(args):
    base_dir, account, config, index, worker_id = args
    email_addr, password = account
    short_email = email_addr.split("@")[0]
    max_retries = config.get("max_login_retries", 3)

    for attempt in range(max_retries):
        try:
            result = register_account(base_dir, email_addr, password, config, worker_id=worker_id)
            if result:
                return email_addr, True
            # 登录失败，重试
            if attempt < max_retries - 1:
                print(f"[{short_email}] ⟳ 重试 {attempt + 2}/{max_retries}")
                time.sleep(2)
        except Exception as e:
            print(f"[{short_email}] ✗ 异常: {e}")
            if attempt < max_retries - 1:
                print(f"[{short_email}] ⟳ 重试 {attempt + 2}/{max_retries}")
                time.sleep(2)

    return email_addr, False


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
    args = parser.parse_args()

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    base_dir = Path(__file__).resolve().parents[2]
    config = load_config(base_dir)
    headless = config.get("headless", False)

    # 如果指定了刷新缓存，执行后退出
    if args.refresh_cache:
        refresh_cache(config, headless=headless)
        return

    accounts = load_accounts(base_dir, config["accounts_file"])

    runtime_cfg = config.get("runtime", {})
    max_workers = config.get("max_workers", runtime_cfg.get("max_workers_default", 2))

    print(f"账号: {len(accounts)} | 进程: {max_workers} | 无头: {headless}")

    # 预热缓存（如果启用且 master 缓存不存在）
    cache_enabled = config.get("cache", {}).get("enabled", True)
    if cache_enabled and not MASTER_CACHE_DIR.exists():
        print("\n首次运行，预热浏览器缓存...")
        warmup_cache(proxy_config=config.get("proxy", {}), headless=headless)
        print("")
    elif not cache_enabled:
        print("本地缓存: 已禁用")

    screenshots_dir = base_dir / "screenshots"
    success_count = 0
    fail_count = 0

    # 为每个任务分配 worker_id（循环使用 0 到 max_workers-1）
    tasks = [(base_dir, acc, config, i, i % max_workers) for i, acc in enumerate(accounts)]

    try:
        with ProcessPoolExecutor(max_workers=max_workers) as executor:
            executor_ref = executor
            futures = {executor.submit(process_account, task): task[1][0] for task in tasks}
            for future in as_completed(futures):
                try:
                    email_addr, success = future.result()
                    if success:
                        success_count += 1
                    else:
                        fail_count += 1
                    print(f"进度: {success_count + fail_count}/{len(accounts)} | 成功: {success_count} | 失败: {fail_count}")
                except Exception as e:
                    fail_count += 1
                    print(f"任务异常: {e}")
    except KeyboardInterrupt:
        print("\n用户中断，正在清理...")
    finally:
        executor_ref = None

    cleanup_screenshots(screenshots_dir)

    print(f"\n{'='*50}")
    print(f"完成 | 成功: {success_count} | 失败: {fail_count}")
    print(f"{'='*50}")
