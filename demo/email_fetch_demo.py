#!/usr/bin/env python3
"""OAuth + IMAP demo.

accounts.txt line:
  email----password----client_id----refresh_token
"""

from __future__ import annotations

import argparse
import email
import imaplib
import json
import os
import re
import ssl
from dataclasses import dataclass
from email import policy
from email.header import decode_header, make_header
from pathlib import Path
from urllib import error, parse, request


ACCOUNTS_FILE = Path(os.getenv("ACCOUNTS_FILE", "accounts.txt"))
IMAP_HOST = "outlook.office365.com"
IMAP_PORT = 993
TOKEN_URL = "https://login.microsoftonline.com/common/oauth2/v2.0/token"
TOKEN_SCOPE = "https://outlook.office.com/IMAP.AccessAsUser.All offline_access"


@dataclass(frozen=True)
class Account:
    email: str
    client_id: str
    refresh_token: str


def env_int(name: str, default: int) -> int:
    value = os.getenv(name)
    return int(value) if value else default


def parse_account_line(line: str) -> Account | None:
    parts = line.strip().split("----")
    if len(parts) < 4 or not parts[0].strip() or not parts[2].strip() or not parts[3].strip():
        return None
    return Account(parts[0].strip(), parts[2].strip(), parts[3].strip())


def load_account(path: Path, account_index: int) -> Account:
    current_index = 0
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        account = parse_account_line(line)
        if not account:
            continue
        current_index += 1
        if current_index == account_index:
            print(f"[CONFIG] account #{account_index} line {line_number}: {account.email}")
            return account
    raise RuntimeError(f"account #{account_index} not found in {path}")


def http_error_text(exc: error.HTTPError) -> str:
    text = exc.read().decode("utf-8", errors="replace")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text
    return data.get("error_description") or data.get("error") or text


def get_access_token(account: Account, timeout: int) -> str:
    body = parse.urlencode(
        {
            "client_id": account.client_id,
            "grant_type": "refresh_token",
            "refresh_token": account.refresh_token,
            "scope": TOKEN_SCOPE,
        }
    ).encode("utf-8")
    req = request.Request(
        TOKEN_URL,
        data=body,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        raise RuntimeError(f"token failed: HTTP {exc.code}: {http_error_text(exc)}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"token failed: {exc.reason}") from exc

    token = data.get("access_token")
    if not token:
        raise RuntimeError(f"token failed: {data}")
    return token


def xoauth2(account: Account, access_token: str) -> bytes:
    return f"user={account.email}\x01auth=Bearer {access_token}\x01\x01".encode("utf-8")


def header_value(message, name: str) -> str:
    value = message.get(name)
    if not value:
        return ""
    return str(make_header(decode_header(value)))


def strip_html(text: str) -> str:
    text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", " ", text)
    text = re.sub(r"(?s)<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_6digit_code(text: str) -> str | None:
    if not text:
        return None

    keyword_patterns = [
        r'[验驗][证證][码碼][：:\s]*(\d{4,8})',
        r'[激][活][码碼][：:\s]*(\d{4,8})',
        r'[Cc]ode[：:\s]*(\d{4,8})',
        r'[Vv]erification[：:\s]*(\d{4,8})',
        r'[Cc]onfirmation[：:\s]*(\d{4,8})',
        r'OTP[：:\s]*(\d{4,8})',
        r'[验驗][证證][码碼](\d{4,8})',
    ]
    for pattern in keyword_patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(1)

    clean = re.sub(r'\d{4}-\d{2}-\d{2}', '', text)
    clean = re.sub(r'\d{1,2}:\d{2}:\d{2}', '', clean)
    clean = re.sub(r'\d{4}/\d{2}/\d{2}', '', clean)
    match = re.search(r'(?<!\d)(\d{6})(?!\d)', clean)
    if match:
        return match.group(1)

    return None


def extract_code_from_message(message) -> str | None:
    body = ""
    html_body = ""

    if message.is_multipart():
        for part in message.walk():
            content_type = part.get_content_type()
            try:
                payload = part.get_payload(decode=True)
                charset = part.get_content_charset() or "utf-8"
                if not payload:
                    continue
                text = payload.decode(charset, errors="ignore")
            except Exception:
                continue

            if content_type == "text/plain":
                body += "\n" + text
            elif content_type == "text/html":
                html_body += "\n" + text
    else:
        try:
            payload = message.get_payload(decode=True)
            charset = message.get_content_charset() or "utf-8"
            text = payload.decode(charset, errors="ignore") if payload else ""
            if "<html" in text.lower():
                html_body = text
            else:
                body = text
        except Exception:
            pass

    if body.strip():
        code = extract_6digit_code(body)
        if code:
            return code

    if html_body:
        html_patterns = [
            r'[验驗][证證][码碼][^>]*>[\s\S]{0,300}?<[^>]+>\s*(\d{4,8})\s*<',
            r'<strong[^>]*>\s*(\d{6})\s*</strong>',
            r'color:#f0b90b[^>]*>\s*<strong>\s*(\d{6})\s*</strong>',
        ]
        for pattern in html_patterns:
            match = re.search(pattern, html_body, re.IGNORECASE)
            if match:
                code = match.group(1)
                if code != "000000":
                    return code

        clean = re.sub(r'<style[^>]*>[\s\S]*?</style>', '', html_body, flags=re.IGNORECASE)
        clean = re.sub(r'<script[^>]*>[\s\S]*?</script>', '', clean, flags=re.IGNORECASE)
        clean = re.sub(r'<[^>]+>', ' ', clean)
        clean = re.sub(r'&nbsp;', ' ', clean)
        clean = re.sub(r'\s+', ' ', clean)
        code = extract_6digit_code(clean)
        if code:
            return code

    return None


def print_code(raw_message: bytes) -> str | None:
    message = email.message_from_bytes(raw_message, policy=policy.default)
    from_addr = header_value(message, "From").lower()
    if "binance" not in from_addr:
        return None
    return extract_code_from_message(message)


def fetch_mail(account: Account, access_token: str, limit: int, mailbox: str, timeout: int) -> None:
    client = imaplib.IMAP4_SSL(IMAP_HOST, IMAP_PORT, timeout=timeout, ssl_context=ssl.create_default_context())
    try:
        client.authenticate("XOAUTH2", lambda _: xoauth2(account, access_token))
        status, _ = client.select(mailbox, readonly=True)
        if status != "OK":
            raise RuntimeError(f"select mailbox failed: {mailbox}")

        status, data = client.search(None, "ALL")
        if status != "OK" or not data or not data[0]:
            print("No messages found.")
            return

        message_ids = list(reversed(data[0].split()[-limit:]))
        for message_id in message_ids:
            status, fetched = client.fetch(message_id, "(RFC822)")
            if status != "OK":
                continue
            raw = next((item[1] for item in fetched if isinstance(item, tuple)), None)
            code = print_code(raw) if raw else None
            if code:
                print(code)
                return

        print("No Binance code found.")
    finally:
        try:
            client.logout()
        except Exception:
            pass


def main() -> None:
    parser = argparse.ArgumentParser(description="Fetch Outlook mail with OAuth + IMAP.")
    parser.add_argument("--account-index", type=int, default=env_int("MAIL_ACCOUNT_INDEX", 1))
    parser.add_argument("--accounts-file", type=Path, default=Path(os.getenv("MAIL_ACCOUNTS_FILE", str(ACCOUNTS_FILE))))
    parser.add_argument("--limit", type=int, default=env_int("MAIL_LIMIT", 5))
    parser.add_argument("--mailbox", default=os.getenv("MAILBOX", "INBOX"))
    parser.add_argument("--timeout", type=int, default=env_int("MAIL_TIMEOUT", 30))
    args = parser.parse_args()

    account = load_account(args.accounts_file, max(1, args.account_index))
    token = get_access_token(account, max(1, args.timeout))
    fetch_mail(account, token, max(1, args.limit), args.mailbox, max(1, args.timeout))


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, OSError, imaplib.IMAP4.error) as exc:
        raise SystemExit(f"[ERROR] {exc}") from exc
