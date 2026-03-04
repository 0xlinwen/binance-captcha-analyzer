"""
自定义异常类型

定义了项目中使用的所有自定义异常，用于区分不同类型的错误，
支持更精确的错误处理和重试策略。
"""


class BinanceAutomationError(Exception):
    """自动化流程基础异常类"""
    pass


# ============================================================================
# 验证码相关异常
# ============================================================================

class CaptchaError(BinanceAutomationError):
    """验证码处理基础异常"""
    pass


class CaptchaNotDetected(CaptchaError):
    """验证码未检测到（页面上没有验证码元素）"""
    pass


class CaptchaTimeout(CaptchaError):
    """验证码处理超时（等待验证码出现或验证超时）"""
    pass


class CaptchaRateLimited(CaptchaError):
    """IP 被风控或触发频率限制"""
    pass


class CaptchaAIError(CaptchaError):
    """AI 识别验证码失败"""
    pass


class CaptchaValidationFailed(CaptchaError):
    """验证码验证失败（识别正确但服务器拒绝）"""
    pass


# ============================================================================
# 邮箱相关异常
# ============================================================================

class IMAPError(BinanceAutomationError):
    """IMAP 操作基础异常"""
    pass


class IMAPAuthFailed(IMAPError):
    """IMAP 认证失败（用户名或密码错误）"""
    pass


class IMAPConnectionError(IMAPError):
    """IMAP 连接失败（网络问题或服务器不可达）"""
    pass


class IMAPTimeout(IMAPError):
    """IMAP 操作超时"""
    pass


class EmailCodeNotFound(IMAPError):
    """邮件验证码未找到"""
    pass


# ============================================================================
# 浏览器相关异常
# ============================================================================

class BrowserError(BinanceAutomationError):
    """浏览器操作基础异常"""
    pass


class BrowserLaunchFailed(BrowserError):
    """浏览器启动失败"""
    pass


class PageLoadTimeout(BrowserError):
    """页面加载超时"""
    pass


class ElementNotFound(BrowserError):
    """页面元素未找到"""
    pass


# ============================================================================
# 配置相关异常
# ============================================================================

class ConfigError(BinanceAutomationError):
    """配置错误基础异常"""
    pass


class ConfigValidationError(ConfigError):
    """配置验证失败"""
    pass


class ConfigFileNotFound(ConfigError):
    """配置文件未找到"""
    pass


# ============================================================================
# 工具函数：判断异常是否可重试
# ============================================================================

def is_retryable(exception: Exception) -> bool:
    """
    判断异常是否可以重试

    Args:
        exception: 异常对象

    Returns:
        True 表示可以重试，False 表示不应重试
    """
    # 可重试的异常类型
    retryable_types = (
        CaptchaTimeout,
        CaptchaAIError,
        IMAPConnectionError,
        IMAPTimeout,
        PageLoadTimeout,
        ElementNotFound,
    )

    # 不可重试的异常类型
    non_retryable_types = (
        IMAPAuthFailed,
        ConfigValidationError,
        ConfigFileNotFound,
        CaptchaRateLimited,
    )

    if isinstance(exception, non_retryable_types):
        return False

    if isinstance(exception, retryable_types):
        return True

    # 默认：未知异常不重试
    return False
