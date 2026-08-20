"""截图文件清理。"""

from __future__ import annotations

from pathlib import Path


def cleanup_screenshots(screenshots_dir: Path) -> None:
    """清理流程生成的截图文件。"""
    if screenshots_dir.exists():
        for screenshot_path in screenshots_dir.glob("*.png"):
            try:
                screenshot_path.unlink()
            except Exception:
                pass
