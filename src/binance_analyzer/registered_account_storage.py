"""注册账号 JSON 存储。"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path

from .file_lock import lock, unlock


LOGIN_MANAGED_FIELDS = {
    "cookie",
    "csrftoken",
    "enabled",
    "name",
    "mail_api_url",
    "email",
    "password",
    "api_key",
    "api_extracted_at",
    "display_name",
    "username",
}


def registered_account_identity(account: dict) -> str:
    """按邮箱识别账号，保留 email 字段中的完整密码输出格式。"""
    email_value = str(account.get("email") or "").strip()
    if "----" in email_value:
        email_value = email_value.split("----", 1)[0].strip()
    return email_value


def save_registered_account(base_dir: Path, output_file: str, account_data: dict) -> None:
    """保存注册账号数据，更新登录字段并完整保留密码。"""
    output_path = base_dir / output_file
    output_path.parent.mkdir(exist_ok=True)
    lock_path = output_path.with_suffix(".lock")
    account_data = dict(account_data)
    account_identity = registered_account_identity(account_data)

    with open(lock_path, "w", encoding="utf-8") as lock_file:
        lock(lock_file)
        try:
            data = {"accounts": []}
            if output_path.exists() and output_path.stat().st_size > 0:
                try:
                    with open(output_path, "r", encoding="utf-8") as file:
                        content = file.read().strip()
                        if content:
                            loaded = json.loads(content)
                            if isinstance(loaded, dict) and "accounts" in loaded:
                                data = loaded
                            else:
                                data = {"accounts": [loaded] if isinstance(loaded, dict) else []}
                except Exception:
                    backup_path = output_path.with_suffix(
                        f"{output_path.suffix}.corrupt.{datetime.now().strftime('%Y%m%d_%H%M%S')}"
                    )
                    try:
                        shutil.copy2(output_path, backup_path)
                    except Exception:
                        pass
                    data = {"accounts": []}

            existing_idx = None
            for index, account in enumerate(data["accounts"]):
                if registered_account_identity(account) == account_identity:
                    existing_idx = index
                    break

            if existing_idx is not None:
                existing = data["accounts"][existing_idx]
                for key in LOGIN_MANAGED_FIELDS:
                    if key in account_data:
                        existing[key] = account_data[key]
                data["accounts"][existing_idx] = existing
            else:
                data["accounts"].append(account_data)

            with open(output_path, "w", encoding="utf-8") as file:
                json.dump(data, file, ensure_ascii=False, indent=2)
        finally:
            unlock(lock_file)
