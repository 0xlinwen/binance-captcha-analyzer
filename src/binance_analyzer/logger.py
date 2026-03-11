"""
统一日志管理模块

日志策略：
- 成功账号：只在控制台输出，不保留日志文件
- 失败账号：详细记录到 logs/failures/{email}_{timestamp}.log

目录结构：
    logs/
    └── failures/
        ├── user1_2024-01-01_123456.log   # 失败账号详细日志
        └── user2_2024-01-01_234567.log
"""

import logging
import io
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict

from .constants import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_DIR,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
)
from .utils import sanitize_filename


class AccountLogCapture:
    """
    账号日志捕获器

    在内存中捕获单个账号的所有日志，执行结束后根据结果决定是否保存到文件。
    """

    def __init__(self, email: str, base_dir: Path):
        self.email = email
        self.base_dir = base_dir
        self.buffer = io.StringIO()
        self.start_time = datetime.now()

        # 创建内存 handler
        self.handler = logging.StreamHandler(self.buffer)
        self.handler.setFormatter(logging.Formatter(
            fmt=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT
        ))
        self.handler.setLevel(logging.DEBUG)

    def get_handler(self) -> logging.Handler:
        return self.handler

    def save_failure_log(self):
        """保存失败日志到文件"""
        failures_dir = self.base_dir / "failures"
        failures_dir.mkdir(parents=True, exist_ok=True)

        safe_email = sanitize_filename(self.email.split("@")[0])
        timestamp = self.start_time.strftime("%Y-%m-%d_%H%M%S")
        log_file = failures_dir / f"{safe_email}_{timestamp}.log"

        content = self.buffer.getvalue()
        if content:
            with open(log_file, "w", encoding="utf-8") as f:
                f.write(f"# 账号: {self.email}\n")
                f.write(f"# 开始时间: {self.start_time.strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write(f"# 结束时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
                f.write("=" * 60 + "\n\n")
                f.write(content)

        return log_file

    def discard(self):
        """丢弃日志（成功时调用）"""
        self.buffer.close()

    def close(self):
        """关闭资源"""
        self.handler.close()
        if not self.buffer.closed:
            self.buffer.close()


class LoggerManager:
    """
    日志管理器

    - 成功账号：只输出到控制台
    - 失败账号：保存详细日志到 logs/failures/
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        log_level: str = DEFAULT_LOG_LEVEL,
    ):
        self.base_dir = Path(base_dir) if base_dir else Path(DEFAULT_LOG_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 创建 failures 子目录
        self.failures_dir = self.base_dir / "failures"
        self.failures_dir.mkdir(parents=True, exist_ok=True)

        self.log_level = getattr(logging, log_level.upper(), logging.INFO)

        # 日志格式
        self.formatter = logging.Formatter(
            fmt=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT
        )

        # 账号日志捕获器缓存
        self._captures: Dict[str, AccountLogCapture] = {}

        # 日志记录器缓存
        self._loggers: Dict[str, logging.Logger] = {}

        # 当日统计
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._stats = {
            "total": 0,
            "success": 0,
            "failure": 0,
            "already_registered": 0,
            "rate_limited": 0,
            "imap_auth_failed": 0,
            "need_register": 0,
        }

    def get_account_logger(self, email: str) -> logging.Logger:
        """
        获取账号专用日志记录器

        日志同时输出到：
        1. 控制台（实时显示）
        2. 内存缓冲区（用于失败时保存）
        """
        safe_email = sanitize_filename(email)
        logger_key = f"account_{safe_email}"

        if logger_key in self._loggers:
            return self._loggers[logger_key]

        logger = logging.getLogger(logger_key)
        logger.setLevel(logging.DEBUG)
        logger.propagate = False

        # 清除旧 handler
        for h in logger.handlers[:]:
            logger.removeHandler(h)

        # 控制台 handler
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self.formatter)
        console_handler.setLevel(self.log_level)
        logger.addHandler(console_handler)

        # 内存捕获 handler
        capture = AccountLogCapture(email, self.base_dir)
        self._captures[email] = capture
        logger.addHandler(capture.get_handler())

        self._loggers[logger_key] = logger
        return logger

    def finalize_account(self, email: str, success: bool) -> Optional[Path]:
        """
        完成账号处理，根据结果决定是否保存日志

        Args:
            email: 账号邮箱
            success: 是否成功

        Returns:
            失败时返回日志文件路径，成功时返回 None
        """
        capture = self._captures.pop(email, None)
        if not capture:
            return None

        log_file = None
        if not success:
            log_file = capture.save_failure_log()
        else:
            capture.discard()

        capture.close()

        # 清理 logger
        safe_email = sanitize_filename(email)
        logger_key = f"account_{safe_email}"
        if logger_key in self._loggers:
            logger = self._loggers.pop(logger_key)
            for h in logger.handlers[:]:
                h.close()
                logger.removeHandler(h)

        return log_file

    def record_result(
        self,
        email: str,
        result,
        mode: str = "register",
        worker_id: int = 0,
        extra: str = "",
    ):
        """
        记录单个账号的执行结果

        result 取值说明：
            True              → 成功
            "already_registered" → 已注册，算成功
            False             → 失败
            "rate_limited"    → IP 被风控，算失败
            "need_register"   → 账号不存在，算失败
            "imap_auth_failed" → IMAP 认证失败，算失败
        """
        self._stats["total"] += 1

        # 判断结果类型
        if result is True:
            success = True
            label = "✅ 成功"
            self._stats["success"] += 1
        elif result == "already_registered":
            success = True
            label = "✅ 已注册(成功)"
            self._stats["success"] += 1
            self._stats["already_registered"] += 1
        elif result == "rate_limited":
            success = False
            label = "🚫 IP风控"
            self._stats["failure"] += 1
            self._stats["rate_limited"] += 1
        elif result == "need_register":
            success = False
            label = "⚠️  未注册"
            self._stats["failure"] += 1
            self._stats["need_register"] += 1
        elif result == "imap_auth_failed":
            success = False
            label = "📧 IMAP失败"
            self._stats["failure"] += 1
            self._stats["imap_auth_failed"] += 1
        else:
            success = False
            label = "❌ 失败"
            self._stats["failure"] += 1

        # 完成账号日志处理
        log_file = self.finalize_account(email, success)

        # 控制台输出结果
        msg = f"[Worker-{worker_id}] [{mode}] {label} | {email}"
        if extra:
            msg += f" | {extra}"
        if log_file:
            msg += f" | 日志: {log_file}"
        print(msg)

    def log_daily_summary(self):
        """输出当日汇总统计"""
        stats = self._stats
        today = self._today
        rate = f"{stats['success']/stats['total']*100:.1f}%" if stats['total'] > 0 else "N/A"

        summary_lines = [
            f"\n{'='*60}",
            f"  📊 当日执行汇总  [{today}]",
            f"{'='*60}",
            f"  总执行数   : {stats['total']}",
            f"  ✅ 成功     : {stats['success']}  (含已注册 {stats['already_registered']})",
            f"  ❌ 失败     : {stats['failure']}",
            f"     - IP风控  : {stats['rate_limited']}",
            f"     - IMAP失败: {stats['imap_auth_failed']}",
            f"     - 未注册  : {stats['need_register']}",
            f"  成功率     : {rate}",
            f"  失败日志   → {self.failures_dir}/",
            f"{'='*60}\n",
        ]

        for line in summary_lines:
            print(line)

    def cleanup_old_logs(self, keep_days: int = 7):
        """清理旧的失败日志文件"""
        if not self.failures_dir.exists():
            return

        cutoff = datetime.now().timestamp() - (keep_days * 24 * 3600)
        removed = 0
        for f in self.failures_dir.glob("*.log"):
            try:
                if f.stat().st_mtime < cutoff:
                    f.unlink()
                    removed += 1
            except Exception:
                pass

        if removed > 0:
            print(f"清理了 {removed} 个过期日志文件")

    def close_all(self):
        """关闭所有资源"""
        for capture in self._captures.values():
            capture.close()
        self._captures.clear()

        for logger in self._loggers.values():
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)
        self._loggers.clear()


# ============================================================================
# 全局日志管理器实例
# ============================================================================

_global_logger_manager: Optional[LoggerManager] = None


def get_logger_manager(
    base_dir: Optional[Path] = None,
    log_level: str = DEFAULT_LOG_LEVEL
) -> LoggerManager:
    """获取全局日志管理器实例（单例模式）"""
    global _global_logger_manager
    if _global_logger_manager is None:
        _global_logger_manager = LoggerManager(
            base_dir=base_dir,
            log_level=log_level
        )
    return _global_logger_manager


def get_logger(name: str = "binance") -> logging.Logger:
    """获取日志记录器（快捷方式，用于非账号相关的通用日志）"""
    logger = logging.getLogger(name)
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter(
            fmt=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT
        ))
        logger.addHandler(handler)
    return logger
