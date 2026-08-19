"""
logger.py
路径: MCP/core/logger.py
功能: 日志管理系统，提供统一的日志记录和格式化输出
"""

import logging
from pathlib import Path
from typing import Optional
import colorlog

from .settings import settings


class LoggerManager:
    """日志管理器 - 单例模式"""

    _instance: Optional['LoggerManager'] = None # 单例实例
    _loggers: dict = {} # 日志记录器缓存

    def __new__(cls):
        """魔法方法 __new__ 在创建实例之前调用，确保只有一个实例存在"""
        if cls._instance is None:
            cls._instance = super().__new__(cls) # 创建实例
        return cls._instance # 返回单例实例

    def __init__(self):
        """初始化日志管理器"""
        if not hasattr(self, "_initialized"): # 如果未初始化，则初始化
            self._initialized = True
            self._setup_log_directory()

    @staticmethod
    def _setup_log_directory():
        """设置日志目录"""
        log_file = Path(settings.LOG_FILE)
        log_file.parent.mkdir(parents=True, exist_ok=True)

    def get_logger(self, name: str) -> logging.Logger:
        if name in self._loggers:
            """如果日志记录器已存在，直接返回"""
            return self._loggers[name]

        logger = logging.getLogger(name) # 创建日志记录器
        logger.setLevel(logging.DEBUG) # 设置日志级别为 DEBUG

        if logger.handlers: # 如果日志记录器已存在处理器，直接返回
            return logger

        # 日志输出1：控制台处理器
        console_handler = logging.StreamHandler() # 在控制台输出日志
        console_handler.setLevel(logging.DEBUG)
        console_formatter = colorlog.ColoredFormatter(
            '%(log_color)s%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
            log_colors={
                'DEBUG': 'cyan', # DEBUG 级别日志显示为青色
                'INFO': 'green', # INFO 级别日志显示为绿色
                'WARNING': 'yellow', # WARNING 级别日志显示为黄色
                'ERROR': 'red', # ERROR 级别日志显示为红色
                'CRITICAL': 'red,bg_white', # CRITICAL 级别日志显示为红色背景白色
            }
        )
        console_handler.setFormatter(console_formatter)

        # ========== 创建 log_path 目录 ================================
        log_path = Path(settings.LOG_FILE)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        # ==============================================================

        # 日志输出2：文件处理器
        file_handler = logging.FileHandler(settings.LOG_FILE, encoding='utf-8') # 在文件中输出日志
        log_level_str = (settings.LOG_LEVEL or "INFO").upper()
        # getattr 将字符串转换为日志级别常量
        file_handler.setLevel(getattr(logging, log_level_str, logging.INFO))
        file_formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S',
        )
        file_handler.setFormatter(file_formatter)

        # 两个处理器添加到日志记录器
        logger.addHandler(console_handler)
        logger.addHandler(file_handler)

        self._loggers[name] = logger # 缓存日志记录器
        return logger

# 全局日志管理器实例
logger_manager = LoggerManager()

def get_logger(name: str) -> logging.Logger:
    """获取日志对象"""
    return logger_manager.get_logger(name)

if __name__ == '__main__':
    # 示例
    logger = get_logger(__name__)
    logger.info("这是信息日志")