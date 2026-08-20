"""
logger.py
路径: langgraph_demo/config/logger.py
功能: 日志管理系统，提供统一的日志记录和格式化输出。

设计：
- LoggerManager 为单例：同一 name 的 logger 只初始化一次（控制台 + 文件双输出）。
- 控制台输出带颜色（colorlog），级别恒为 DEBUG，便于开发期观察；
  文件输出级别由 settings.LOG_LEVEL 控制（默认 INFO），用于留存。
- 全项目统一通过 get_logger(__name__) 获取 logger，保证格式一致。
"""

import logging
from pathlib import Path
from typing import Optional
import colorlog

from .settings import settings


class LoggerManager:
    """日志管理器 - 单例模式：保证所有 get_logger 走同一套初始化逻辑。"""

    _instance: Optional['LoggerManager'] = None   # 单例实例
    _loggers: dict = {}                           # 已创建 logger 的缓存（name -> logger）

    def __new__(cls):
        """单例入口：首次调用创建实例，之后直接复用，确保只初始化一次。"""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        """初始化日志管理器（幂等：_initialized 标志避免重复初始化）。"""
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._setup_log_directory()

    @staticmethod
    def _setup_log_directory():
        """确保日志文件所在目录存在（mkdir -p，存在时忽略）。"""
        log_file = Path(settings.LOG_FILE)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    def get_logger(self, name: str) -> logging.Logger:
        """获取（或创建并缓存）一个 logger。

        Args:
            name: logger 名称，惯例传 __name__，便于日志里定位来源模块。

        Returns:
            配置好的 logging.Logger：控制台（彩色）+ 文件双输出。
        """
        # 已创建过则直接返回缓存，避免重复添加 handler 导致重复打印
        if name in self._loggers:
            return self._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)

        # 防御：若 logger 已被外部添加过 handler，不再重复挂载
        if logger.handlers:
            return logger

        # 输出1：控制台 handler（彩色，恒为 DEBUG，开发期可见所有级别）
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_formatter = colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            log_colors={
                'DEBUG': 'cyan',       # DEBUG 青色
                'INFO': 'green',       # INFO 绿色
                'WARNING': 'yellow',   # WARNING 黄色
                'ERROR': 'red',        # ERROR 红色
                'CRITICAL': 'red,bg_white',  # CRITICAL 红底白字
            }
        )
        console_handler.setFormatter(console_formatter)

        # 确保日志目录存在（get_logger 可能在 __init__ 之后被独立调用）
        log_path = Path(settings.LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)

        # 输出2：文件 handler（级别由 LOG_LEVEL 控制，无颜色，便于留存归档）
        file_handler = logging.FileHandler(settings.LOG_FILE, encoding='utf-8')
        log_level_str = (settings.LOG_LEVEL or "INFO").upper()
        # getattr 将字符串（如 "INFO"）转换为 logging 级别常量
        file_handler.setLevel(getattr(logging, log_level_str, logging.INFO))
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        file_handler.setFormatter(file_formatter)

        # 两个 handler 挂到 logger 并缓存
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        self._loggers[name] = logger
        return logger


# 全局日志管理器实例（单例）
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """获取统一配置的日志对象（单例，格式一致）。

    Args:
        name: 模块名，惯例传 __name__。

    Returns:
        控制台彩色 + 文件双输出的 logging.Logger。
    """
    return logger_manager.get_logger(name)


if __name__ == '__main__':
    # 简单自测：打印一条 INFO 日志验证输出
    logger = get_logger(__name__)
    logger.info("这是信息日志")