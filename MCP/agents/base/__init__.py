"""
Agent Base Classes - 智能体基类
"""

from abc import ABC, abstractmethod
from typing import Any, Dict


class BaseAgent(ABC):
    """智能体基类"""

    def __init__(self):
        """初始化基类"""
        pass

    @abstractmethod
    async def process(self, input_data: Any) -> Dict[str, Any]:
        """处理输入数据的抽象方法"""
        pass

    def get_logger(self):
        """获取日志器"""
        from MCP.core.logger import get_logger
        return get_logger(self.__class__.__name__)
