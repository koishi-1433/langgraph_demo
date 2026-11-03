"""
Agent 公共父类 - 封装MCP交互与初始化逻辑
"""
from abc import ABC
from typing import List, Dict, Optional

from langchain_core.tools import Tool
from MCP.integration.tool_adapter import MCPAdapter

from MCP.core.llm_client import LLMClientFactory
from MCP.core.logger import get_logger


class BaseAgent(ABC):
    """所有智能体的基类"""

    def __init__(self, mcp_tools: List[Tool], mcp_adapter: MCPAdapter, agent_type: str):
        """
        初始化基类智能体
        Args：
            mcp_tools: 适配后的LangChain工具列表
            mcp_adapter: 已连接的MCP适配器实例
            agent_type: 智能体类型（用于获取对应的LLMClient）
        """
        self.logger = get_logger(f"{__name__}.{self.__class__.__name__}")

        # 1.统一获取LLM客户端
        self.llm = self._create_llm(agent_type)

        # 2.通义将工具列表转为字典方便0（1）查找
        self.mcp_tools: Dict[str, Tool] = {tool.name: tool for tool in mcp_tools}

        # 3.统一持有MCP适配器引用
        self.mcp_adapter = mcp_adapter

        self.logger.info(f"{self.__class__.__name__} 初始化完成 （Type:{agent_type}）")

    # 策略模式(Strategy Pattern)与工厂模式(Factory Pattern)结合
    '''
    Python 中，函数（First-class citizen）
    意味着函数可以像变量一样被存储在字典里，或者作为参数传递
    延迟执行（Lazy Execution）。在字典里只存放"怎么创建的方法"，只有在真正实例化 Agent 的时候，才会去调用工厂类生成对应的实例。
    '''
    def _create_llm(self, agent_type: str):
        """内部LLM工厂调用映射"""
        # mapping 字典：存储的是函数对象本身（注意，如：写的是 LLMClientFactory.create_researcher，后面没有括号）
        mapping = {
            "search": LLMClientFactory.create_search,
            # "plan_route": LLMClientFactory.create_plan_route,
        }

        # 变量 creator 指向具体的函数
        # 如当 agent_type 是 "researcher" 时，creator 等于 LLMClientFactory.create_researcher
        creator = mapping.get(agent_type.lower())
        if not creator:
            raise ValueError(f"未定义的Agent类型: {agent_type}")

        # 这里的括号是真正的执行动作
        # 等同调用 LLMClientFactory.create_researcher() 并返回生成的 LLMClient 实例
        return creator()

    def get_tool(self, tool_name: str) -> Optional[Tool]:
        """安全获取工具方法"""
        tool = self.mcp_tools.get(tool_name)
        if not tool:
            self.logger.warning(f"工具 '{tool_name}' 在当前MCP环境中未找到")
        return tool
