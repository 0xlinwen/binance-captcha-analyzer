from __future__ import annotations

from typing import Any, Protocol


class ProxyLogger(Protocol):
    def __call__(self, stage: str, message: str = "", /, **fields: Any) -> None: ...


def _format_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "on" if value else "off"
    if isinstance(value, float):
        return f"{value:.3f}".rstrip("0").rstrip(".")
    if isinstance(value, (list, tuple, set)):
        return ",".join(part for part in (_format_value(item) for item in value) if part)
    return str(value).strip()


def default_log(stage: str, message: str = "", /, **fields: Any) -> None:
    parts = [f"[{str(stage).strip() or 'proxy'}]"]
    message_text = str(message).strip()
    if message_text:
        parts.append(message_text)
    for key, value in fields.items():
        formatted = _format_value(value)
        if formatted:
            parts.append(f"{key}={formatted}")
    print(" ".join(parts), flush=True)


def emit_log(logger: ProxyLogger | None, stage: str, message: str = "", /, **fields: Any) -> None:
    (logger or default_log)(stage, message, **fields)
