import os
import random
import signal
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from .config import load_config
from .orchestrator import register_account
from .storage import cleanup_screenshots, load_accounts

executor_ref = None


def process_account(args):
    base_dir, account, config, index = args
    email_addr, password = account

    runtime_cfg = config.get("runtime", {})
    delay_min = float(runtime_cfg.get("start_delay_min_sec", 8))
    delay_max = float(runtime_cfg.get("start_delay_max_sec", 20))

    # index=0 可立即启动，其余随机错峰
    if index > 0:
        delay = random.uniform(delay_min, delay_max)
        print(f"[{email_addr}] 等待 {delay:.1f} 秒后启动...")
        time.sleep(delay)

    try:
        return email_addr, register_account(base_dir, email_addr, password, config)
    except Exception as e:
        print(f"处理 {email_addr} 出错: {e}")
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


def main():
    global executor_ref

    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    base_dir = Path(__file__).resolve().parents[2]
    config = load_config(base_dir)
    accounts = load_accounts(base_dir, config["accounts_file"])

    runtime_cfg = config.get("runtime", {})
    max_workers = config.get("max_workers", runtime_cfg.get("max_workers_default", 2))
    headless = config.get("headless", False)

    print(f"共加载 {len(accounts)} 个账号")
    print(f"进程数: {max_workers}, 无头模式: {headless}")

    screenshots_dir = base_dir / "screenshots"
    success_count = 0
    fail_count = 0

    tasks = [(base_dir, acc, config, i) for i, acc in enumerate(accounts)]

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
                    print(f"进度: {success_count + fail_count}/{len(accounts)} (成功: {success_count}, 失败: {fail_count})")
                except Exception as e:
                    fail_count += 1
                    print(f"任务异常: {e}")
    except KeyboardInterrupt:
        print("\n用户中断，正在清理...")
    finally:
        executor_ref = None

    cleanup_screenshots(screenshots_dir)

    print(f"\n\n{'='*60}")
    print("批量注册完成!")
    print(f"成功: {success_count}, 失败: {fail_count}")
    print(f"{'='*60}")
