"""从账号文件批量提交 Linux 云端任务，并发送一次性 Lark 通知。"""

from __future__ import annotations

import argparse
import time
from pathlib import Path

import requests

from binance_analyzer.account_storage import load_accounts


TERMINAL = {"success", "failed", "cancelled", "proxy_quota_exceeded"}


def submit_batches(cloud_url: str, accounts_file: Path, mode: str, batch_size: int, count: int | None,
                   poll_seconds: int, timeout_seconds: int = 86400) -> list[str]:
    accounts = load_accounts(Path.cwd(), str(accounts_file))
    if count is not None:
        accounts = accounts[:count]
    if not accounts:
        raise ValueError("账号文件没有可用账号")
    job_ids: list[str] = []
    task_group_id = None
    if len(accounts) > 1:
        group_response = requests.post(f"{cloud_url.rstrip('/')}/api/task-groups", json={"total_count": 0}, timeout=30)
        group_response.raise_for_status()
        task_group_id = group_response.json()["id"]
    for start in range(0, len(accounts), batch_size):
        batch = accounts[start:start + batch_size]
        payload = {"mode": mode, "accounts": [{"email": email, "password": password} for email, password in batch],
                   "proxy": {"mode": "direct"}}
        if task_group_id:
            payload["task_group_id"] = task_group_id
        response = requests.post(f"{cloud_url.rstrip('/')}/api/login-jobs", json=payload, timeout=30)
        response.raise_for_status()
        job_ids.append(response.json()["job_id"])

    started = time.monotonic()
    while True:
        total = success = failed = done = 0
        for job_id in job_ids:
            response = requests.get(f"{cloud_url.rstrip('/')}/api/login-jobs/{job_id}", timeout=30)
            response.raise_for_status()
            items = response.json().get("items", [])
            for item in items:
                total += 1
                status = item.get("status")
                if status in TERMINAL:
                    done += 1
                if status == "success":
                    success += 1
                elif status in {"failed", "cancelled", "proxy_quota_exceeded"}:
                    failed += 1
        if total and done == total:
            return job_ids
        if time.monotonic() - started >= timeout_seconds:
            for job_id in job_ids:
                requests.post(f"{cloud_url.rstrip('/')}/api/login-jobs/{job_id}/cancel", timeout=30).raise_for_status()
            raise TimeoutError("批量任务超过整体超时时间")
        time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="读取账号文件并提交 Linux 云端登录/注册任务")
    parser.add_argument("--file", required=True, help="账号文件路径（相对当前目录）")
    parser.add_argument("--cloud-url", required=True, help="Linux Cloud API 地址")
    parser.add_argument("--mode", choices=("login", "register"), default="login")
    parser.add_argument("--batch-size", type=int, default=20)
    parser.add_argument("--count", type=int)
    parser.add_argument("--poll-seconds", type=int, default=10)
    parser.add_argument("--timeout-seconds", type=int, default=86400)
    args = parser.parse_args()
    if args.batch_size <= 0 or args.poll_seconds <= 0 or args.timeout_seconds <= 0 or (args.count is not None and args.count <= 0):
        parser.error("batch-size、poll-seconds、timeout-seconds、count 必须是正整数")
    path = Path(args.file)
    if path.is_absolute():
        parser.error("--file 只允许使用相对路径")
    submit_batches(args.cloud_url, path, args.mode, args.batch_size, args.count, args.poll_seconds, args.timeout_seconds)


if __name__ == "__main__":
    main()
