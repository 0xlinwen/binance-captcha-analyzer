"""账号队列与账号结果文件存储。"""

from __future__ import annotations

from pathlib import Path

from .file_lock import lock, unlock


def parse_account_line(line: str) -> tuple[str, str] | None:
    """解析账号行，返回邮箱和完整密码。"""
    text = str(line or "").strip()
    if not text:
        return None
    if "----" in text:
        email_addr, password = text.split("----", 1)
        return email_addr.strip(), password.strip()
    if ":" in text:
        email_addr, password = text.split(":", 1)
        return email_addr.strip(), password.strip()
    return None


def load_accounts(base_dir: Path, accounts_file: str) -> list[tuple[str, str]]:
    """读取账号队列文件。"""
    accounts: list[tuple[str, str]] = []
    accounts_path = base_dir / accounts_file
    with open(accounts_path, "r", encoding="utf-8") as file:
        for line in file:
            parsed = parse_account_line(line)
            if parsed:
                accounts.append(parsed)
    return accounts


def remove_account_from_file(base_dir: Path, accounts_file: str, email_addr: str, password: str) -> bool:
    """从账号队列中移除指定邮箱和密码完全匹配的账号。"""
    accounts_path = base_dir / accounts_file
    accounts_path.parent.mkdir(parents=True, exist_ok=True)
    if not accounts_path.exists():
        return False

    lock_path = accounts_path.with_suffix(f"{accounts_path.suffix}.lock")
    removed = False

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        lock(lock_file)
        try:
            with open(accounts_path, "r", encoding="utf-8") as file:
                lines = file.readlines()

            kept_lines = []
            for line in lines:
                parsed = parse_account_line(line)
                if parsed == (email_addr, password):
                    removed = True
                    continue
                kept_lines.append(line)

            if removed:
                with open(accounts_path, "w", encoding="utf-8") as file:
                    file.writelines(kept_lines)
        finally:
            unlock(lock_file)

    return removed


def append_account_result(filepath: Path, email_addr: str, password: str, delimiter: str = ":") -> bool:
    """追加账号处理结果，按邮箱去重并完整保留密码。"""
    filepath.parent.mkdir(parents=True, exist_ok=True)
    lock_path = filepath.with_suffix(f"{filepath.suffix}.lock")
    target_line = f"{email_addr}{delimiter}{password}\n"
    appended = False

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        lock(lock_file)
        try:
            existing_emails = set()
            if filepath.exists():
                with open(filepath, "r", encoding="utf-8") as file:
                    for line in file:
                        parsed = parse_account_line(line)
                        if parsed:
                            existing_emails.add(parsed[0])

            if email_addr not in existing_emails:
                with open(filepath, "a", encoding="utf-8") as file:
                    file.write(target_line)
                appended = True
        finally:
            unlock(lock_file)

    return appended
