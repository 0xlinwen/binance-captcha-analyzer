import fcntl
import json
from pathlib import Path


def load_accounts(base_dir: Path, accounts_file: str):
    accounts = []
    accounts_path = base_dir / accounts_file
    with open(accounts_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            # 支持两种分隔符格式: ---- 和 :
            if "----" in line:
                email_addr, password = line.split("----", 1)
                accounts.append((email_addr.strip(), password.strip()))
            elif ":" in line:
                email_addr, password = line.split(":", 1)
                accounts.append((email_addr.strip(), password.strip()))
    return accounts


def save_registered_account(base_dir: Path, output_file: str, account_data: dict):
    output_path = base_dir / output_file
    output_path.parent.mkdir(exist_ok=True)
    lock_path = output_path.with_suffix(".lock")

    with open(lock_path, "w") as lock_file:
        fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
        try:
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
                except Exception:
                    data = {"accounts": []}

            existing_emails = {acc.get("email") for acc in data["accounts"]}
            if account_data.get("email") in existing_emails:
                for i, acc in enumerate(data["accounts"]):
                    if acc.get("email") == account_data.get("email"):
                        data["accounts"][i] = account_data
                        break
            else:
                data["accounts"].append(account_data)

            with open(output_path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        finally:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)


def cleanup_screenshots(screenshots_dir: Path):
    if screenshots_dir.exists():
        for f in screenshots_dir.glob("*.png"):
            try:
                f.unlink()
            except Exception:
                pass
