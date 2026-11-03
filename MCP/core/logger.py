"""
logger.py
路径: MCP/core/logger.py
功能: 日志管理系统，提供统一的日志记录和格式化输出
"""

import logging
import os
from pathlib import Path
from typing import Optional

import colorlog

from MCP.config.settings import settings


class LoggerManager:
    """日志管理器 - 单例模式"""

    _instance: Optional['LoggerManager'] = None
    _loggers: dict = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    def __init__(self):
        if not hasattr(self, "_initialized"):
            self._initialized = True
            self._setup_log_directory()

    def _setup_log_directory(self):
        """设置日志目录"""
        log_file = Path(settings.LOG_FILE)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    def get_logger(self, name: str) -> logging.Logger:
        """获取日志记录器"""
        if name in self._loggers:
            return self._loggers[name]

        logger = logging.getLogger(name)
        logger.setLevel(logging.DEBUG)

        # 避免重复添加handler
        if logger.handlers:
            return logger

        # 控制台处理器
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_formatter = colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            log_colors={
                'DEBUG': 'cyan',
                'INFO': 'green',
                'WARNING': 'yellow',
                'ERROR': 'red',
                'CRITICAL': 'red,bg_white',
            }
        )
        console_handler.setFormatter(console_formatter)

        # 文件处理器
        file_handler = logging.FileHandler(settings.LOG_FILE, encoding='utf-8')
        file_handler.setLevel(getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO))
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        file_handler.setFormatter(file_formatter)

        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        self._loggers[name] = logger
        return logger


# 全局日志管理器实例
logger_manager = LoggerManager()


def get_logger(name: str) -> logging.Logger:
    """获取日志对象"""
    return logger_manager.get_logger(name)
