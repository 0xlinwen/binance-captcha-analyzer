"""跨平台文件锁适配。

使用 portalocker 提供 Windows、macOS 和 Linux 一致的文件锁接口。
"""

from __future__ import annotations

from typing import IO

import portalocker


def lock(file_obj: IO[str], *, shared: bool = False) -> None:
    """为已打开的锁文件加共享锁或排他锁。"""
    flags = portalocker.LOCK_SH if shared else portalocker.LOCK_EX
    portalocker.lock(file_obj, flags)


def unlock(file_obj: IO[str]) -> None:
    """释放锁文件上的锁。"""
    portalocker.unlock(file_obj)
