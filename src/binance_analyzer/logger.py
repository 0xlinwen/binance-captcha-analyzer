"""
统一日志管理模块

提供统一的日志管理器，支持每日日志、账号专用日志、日志轮转等功能。

目录结构：
    logs/
    ├── binance_2024-01-01.log      # 每日主日志（完整执行过程）
    ├── success/
    │   └── 2024-01-01.log          # 当日成功账号列表（含 already_registered）
    └── failure/
        └── 2024-01-01.log          # 当日失败账号列表
"""

import logging
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict
from logging.handlers import RotatingFileHandler

from .constants import (
    DEFAULT_LOG_LEVEL,
    DEFAULT_LOG_DIR,
    LOG_MAX_FILE_SIZE_MB,
    LOG_BACKUP_COUNT,
    LOG_FORMAT,
    LOG_DATE_FORMAT,
)
from .utils import sanitize_filename


class LoggerManager:
    """
    日志管理器

    负责创建和管理所有日志记录器，防止重复创建和内存泄漏。
    """

    def __init__(
        self,
        base_dir: Optional[Path] = None,
        log_level: str = DEFAULT_LOG_LEVEL,
        max_file_size_mb: int = LOG_MAX_FILE_SIZE_MB,
        backup_count: int = LOG_BACKUP_COUNT
    ):
        self.base_dir = Path(base_dir) if base_dir else Path(DEFAULT_LOG_DIR)
        self.base_dir.mkdir(parents=True, exist_ok=True)

        # 创建 success / failure 子目录
        self.success_dir = self.base_dir / "success"
        self.failure_dir = self.base_dir / "failure"
        self.success_dir.mkdir(parents=True, exist_ok=True)
        self.failure_dir.mkdir(parents=True, exist_ok=True)

        self.log_level = getattr(logging, log_level.upper(), logging.INFO)
        self.max_file_size = max_file_size_mb * 1024 * 1024
        self.backup_count = backup_count

        # 日志记录器缓存
        self._loggers: Dict[str, logging.Logger] = {}

        # 日志格式
        self.formatter = logging.Formatter(
            fmt=LOG_FORMAT,
            datefmt=LOG_DATE_FORMAT
        )

        # 当日统计
        self._today = datetime.now().strftime("%Y-%m-%d")
        self._stats = {
            "total": 0,
            "success": 0,
            "failure": 0,
            "already_registered": 0,
            "rate_limited": 0,
        }

    # ------------------------------------------------------------------ #
    # 核心日志记录器
    # ------------------------------------------------------------------ #

    def get_daily_logger(self, name: str = "binance") -> logging.Logger:
        """获取每日主日志记录器（输出到文件 + 控制台）"""
        logger_key = f"daily_{name}"
        if logger_key in self._loggers:
            return self._loggers[logger_key]

        logger = logging.getLogger(logger_key)
        logger.setLevel(self.log_level)
        logger.propagate = False

        today = datetime.now().strftime("%Y-%m-%d")
        log_file = self.base_dir / f"{name}_{today}.log"

        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=self.max_file_size,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(self.formatter)
        logger.addHandler(file_handler)

        console_handler = logging.StreamHandler()
        console_handler.setFormatter(self.formatter)
        logger.addHandler(console_handler)

        self._loggers[logger_key] = logger
        return logger

    def _get_result_logger(self, subdir: Path, suffix: str) -> logging.Logger:
        """获取 success 或 failure 专用记录器（纯追加，无轮转）"""
        today = datetime.now().strftime("%Y-%m-%d")
        logger_key = f"{suffix}_{today}"

        if logger_key in self._loggers:
            return self._loggers[logger_key]

        logger = logging.getLogger(logger_key)
        logger.setLevel(logging.INFO)
        logger.propagate = False

        log_file = subdir / f"{today}.log"
        handler = logging.FileHandler(log_file, mode='a', encoding='utf-8')
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s",
            datefmt=LOG_DATE_FORMAT
        ))
        logger.addHandler(handler)

        self._loggers[logger_key] = logger
        return logger

    # ------------------------------------------------------------------ #
    # 结果写入（供 orchestrator 调用）
    # ------------------------------------------------------------------ #

    def record_result(
        self,
        email: str,
        result,
        mode: str = "register",
        worker_id: int = 0,
        extra: str = "",
    ):
        """
        记录单个账号的执行结果，写入 success/ 或 failure/ 并更新当日统计。

        result 取值说明：
            True              → 成功
            "already_registered" → 已注册，算成功
            False             → 失败
            "rate_limited"    → IP 被风控，算失败
            "need_register"   → 账号不存在，算失败
        """
        self._stats["total"] += 1

        # 判断结果类型
        if result is True:
            category = "success"
            label = "✅ 成功"
        elif result == "already_registered":
            category = "success"          # already_registered 计入成功
            label = "✅ 已注册(成功)"
            self._stats["already_registered"] += 1
        elif result == "rate_limited":
            category = "failure"
            label = "🚫 IP风控"
            self._stats["rate_limited"] += 1
        elif result == "need_register":
            category = "failure"
            label = "⚠️  未注册"
        else:
            category = "failure"
            label = "❌ 失败"

        self._stats[category] += 1

        # 写入对应结果文件
        target_dir = self.success_dir if category == "success" else self.failure_dir
        result_logger = self._get_result_logger(target_dir, category)

        msg = f"[Worker-{worker_id}] [{mode}] {label} | {email}"
        if extra:
            msg += f" | {extra}"
        result_logger.info(msg)

    def log_daily_summary(self, main_logger: Optional[logging.Logger] = None):
        """
        将当日汇总统计写入主日志。
        调用时机：所有 worker 全部完成后。
        """
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
            f"  🚫 IP风控   : {stats['rate_limited']}",
            f"  成功率     : {rate}",
            f"  详细结果   → success/{today}.log  |  failure/{today}.log",
            f"{'='*60}\n",
        ]

        lg = main_logger or self.get_daily_logger()
        for line in summary_lines:
            lg.info(line)

    # ------------------------------------------------------------------ #
    # 兼容旧接口
    # ------------------------------------------------------------------ #

    def get_account_logger(
        self,
        email: str,
        log_type: str = "account"
    ) -> logging.Logger:
        """兼容旧接口：获取账号专用日志记录器"""
        safe_email = sanitize_filename(email)
        logger_key = f"{log_type}_{safe_email}"

        if logger_key in self._loggers:
            return self._loggers[logger_key]

        logger = logging.getLogger(logger_key)
        logger.setLevel(self.log_level)
        logger.propagate = False

        log_file = self.base_dir / f"{log_type}_{safe_email}.log"
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=self.max_file_size,
            backupCount=self.backup_count,
            encoding='utf-8'
        )
        file_handler.setFormatter(self.formatter)
        logger.addHandler(file_handler)

        self._loggers[logger_key] = logger
        return logger

    def get_failure_logger(self, email: str) -> logging.Logger:
        return self.get_account_logger(email, log_type="failure")

    def get_success_logger(self, email: str) -> logging.Logger:
        return self.get_account_logger(email, log_type="success")

    # ------------------------------------------------------------------ #
    # 清理
    # ------------------------------------------------------------------ #

    def cleanup_old_loggers(self, keep_count: int = 100):
        """清理旧的日志记录器，防止内存泄漏"""
        if len(self._loggers) <= keep_count:
            return
        logger_keys = list(self._loggers.keys())
        to_remove = logger_keys[:-keep_count]
        for key in to_remove:
            logger = self._loggers.pop(key)
            for handler in logger.handlers[:]:
                handler.close()
                logger.removeHandler(handler)

    def close_all(self):
        """关闭所有日志记录器"""
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
    """获取日志记录器（快捷方式）"""
    manager = get_logger_manager()
    return manager.get_daily_logger(name)