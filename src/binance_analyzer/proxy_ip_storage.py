"""代理出口 IP 使用记录存储。"""

from __future__ import annotations

import fcntl
from pathlib import Path


def load_used_proxy_ips(base_dir: Path, used_ips_file: str) -> set[str]:
    """读取已使用的代理出口 IP。"""
    used_ips_path = base_dir / used_ips_file
    if not used_ips_path.exists():
        return set()

    lock_path = used_ips_path.with_suffix(f"{used_ips_path.suffix}.lock")
    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_SH)
        try:
            with open(used_ips_path, "r", encoding="utf-8") as file:
                return {
                    line.strip()
                    for line in file
                    if line.strip() and not line.lstrip().startswith("#")
                }
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def append_used_proxy_ip(base_dir: Path, used_ips_file: str, exit_ip: str) -> bool:
    """写入代理出口 IP，已存在则不重复追加。"""
    ip = str(exit_ip or "").strip()
    if not ip:
        return False

    used_ips_path = base_dir / used_ips_file
    used_ips_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = used_ips_path.with_suffix(f"{used_ips_path.suffix}.lock")
    appended = False

    with open(lock_path, "a+", encoding="utf-8") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
            existing_ips = set()
            if used_ips_path.exists():
                with open(used_ips_path, "r", encoding="utf-8") as file:
                    existing_ips = {
                        line.strip()
                        for line in file
                        if line.strip() and not line.lstrip().startswith("#")
                    }

            if ip not in existing_ips:
                with open(used_ips_path, "a", encoding="utf-8") as file:
                    file.write(f"{ip}\n")
                appended = True
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    return appended
