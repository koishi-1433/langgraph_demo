"""
tool_adapter.py
路径: MCP/integration/tool_adapter.py
功能: MCP适配器，负责连接底层MCP协议和高层LangGraph逻辑
"""

from typing import Any, Callable, List, Optional, Dict
from contextlib import AsyncExitStack
from langchain_core.tools import Tool

from MCP.protocol.client import get_mcp_client
from MCP.core.logger import get_logger

logger = get_logger(__name__)


class MCPAdapter:
    """
    MCP 适配器 - 核心转换类（异步长连接版）
    server_script_path: MCP服务端地址
    """

    def __init__(self):
        self.mcp_client = get_mcp_client()
        self.tools_cache = {}
        self.prompts_cache = {}
        self.resources_cache = {}

        # 用于管理连接状态
        self._exit_stack = AsyncExitStack()
        self._session = None  # 就是MCPClient对象
        # 可重入计数：main.py 与 workflow 会对同一单例各 async with 一次，
        # 只在外层最后一次退出时才真正断开，避免重复建连/提前关闭。
        self._depth = 0

        logger.info("初始化 MCP 适配器实例")

    # ============ 生命周期管理 (关键) ============
    '''异步 资源管理（Connection Lifecycle Management）
    使用 Python 的异步上下文管理器。
        之前的测试，会频繁地建立和断开连接，这会反复启动/杀死 fastapi.py 进程，性能差。
        解决：代码中支持 async with get_mcp_adapter(...) as adapter:
        通过 AsyncExitStack 把连接"锁住"，直到整个任务结束才释放。
        在 LangGraph 这种需要多步迭代的流程中至关重要。
    '''
    async def __aenter__(self):
        """进入上下文：仅在最外层建立一次连接（可重入）"""
        if self._depth == 0:
            logger.info("正在建立 MCP 长连接...")
            try:
                # 进入 mcp_client.connect() 的上下文，并保持住
                self._session = await self._exit_stack.enter_async_context(
                    self.mcp_client.connect()
                )
                logger.info("✓ MCP 长连接已建立")
            except Exception as e:
                logger.error(f"建立连接失败: {e}")
                self._exit_stack = AsyncExitStack()
                raise
        self._depth += 1
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """退出上下文：只有最外层退出才真正断开连接"""
        self._depth -= 1
        if self._depth > 0:
            return
        self._depth = 0
        try:
            logger.info("正在断开 MCP 长连接...")
            await self._exit_stack.aclose()
        finally:
            self._session = None
            # 重建栈，保证下次进入时是干净状态
            self._exit_stack = AsyncExitStack()
            logger.info("✓ MCP 连接已断开")

    @property
    def session(self):
        """获取当前活跃的 session，如果未连接则报错"""
        if self._session is None:
            raise RuntimeError(
                "MCPAdapter 未连接! 请在 'async with adapter:' 代码块中使用。"
            )
        return self._session

    # ============ Tools 相关 ============

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        统一 MCP 工具调用入口（P0-4）。

        所有 Agent 都经此访问 MCP，保证全系统共用同一连接、同一传输模式；
        Agent 不再自行创建 SSE / HTTP 会话，也无需知道服务端 URL。
        返回原生 CallToolResult（含 .content），由调用方解析。
        """
        return await self.session.call_tool(tool_name, arguments)

    async def get_langgraph_tools(self) -> List[Tool]:
        """获取转换后的 LangGraph 工具列表"""
        try:
            # 直接使用 self.session (复用连接)
            mcp_tools = await self.session.list_tools()  # 真正获取MCP工具

            langgraph_tools = []
            for mcp_tool in mcp_tools:
                tool = Tool(
                    name=mcp_tool.name,
                    description=mcp_tool.description or f"MCP 工具: {mcp_tool.name}",
                    func=None,  # 同步程序去使用
                    coroutine=self._create_tool_function(mcp_tool.name)  # 异步程序
                )
                langgraph_tools.append(tool)
                self.tools_cache[mcp_tool.name] = mcp_tool

            logger.info(f"✓ 已加载 {len(langgraph_tools)} 个工具")
            return langgraph_tools
        except Exception as e:
            logger.error(f"工具转换失败: {str(e)}")
            raise

    # 闭包与作用域的运用：闭包（Closure）实现了在工具执行函数中复用 MCP 连接（Session）的能力。
    '''
    动态生成一个 tool_func。这个函数内部"捕获"了 tool_name 且共享了同一个 self.session。
    保证了当 Agent 调用搜索工具时，不需要重新握手，直接通过已经打开的管道（Pipe）发送指令，响应速度是毫秒级的。
    '''
    def _create_tool_function(self, tool_name: str) -> Callable:
        """创建工具执行函数 (闭包中复用 session)"""

        async def tool_func(**kwargs) -> str:
            try:
                # logger.debug(f"执行工具: {tool_name} | 参数: {kwargs}")

                # ✅ 关键修改：不再 connect，直接使用活跃的 self.session
                result = await self.session.call_tool(tool_name, kwargs)

                '''
                MCP 原生返回的是 CallToolResult 对象，里面嵌套了 content 列表。
                适配器把这些复杂的结构简化成了 str(result.content[0].text)。
                意义：Agent 不需要理解什么是 MCP，它只需要调用函数并得到字符串结果。
                '''
                if hasattr(result, 'content') and result.content:
                    return str(result.content[0].text)
                return str(result)

            except Exception as e:
                error_msg = f"工具 {tool_name} 执行失败: {str(e)}"
                logger.error(error_msg)
                return error_msg

        return tool_func

    # ============ Prompts 相关 ============

    # async def get_prompt(self, prompt_name: str, **arguments) -> str:
    r'''或者：清晰和直接的是实现方式：
        async def get_prompt(self, prompt_name: str, arguments: Dict[str, Any] = None) -> str:
                """
                获取提示词内容 (高级接口)
                """
                try:
                    # 复用 client.py 中的连接，直接透传参数
                    return await self.session.get_prompt(prompt_name, arguments=arguments)
                except Exception as e:
                    logger.error(f"获取提示词失败: {e}")
                    return ""
        优点：
        1. 接口一致性 (Consistency): MCPAdapter 的 get_prompt 接口与它内部调用的 MCPClient 的接口完全一致。
            代码直观，从上到下的每一层都使用相同的"调用方式"。
        2. 代码极简 (Simplicity): 实现简单，就是直接的"透传"，没有任何额外的逻辑。
    '''
    async def get_prompt(self, prompt_name: str, **kwargs) -> str:
        """
        获取提示词内容 (高级接口)
        """
        try:
            # return await self.session.get_prompt(prompt_name, arguments=arguments)
            # 智能处理参数，使其同时兼容 arguments={'key': 'value'} 和 key='value' 两种调用方式
            final_arguments = kwargs.get('arguments', kwargs)
            # 复用连接
            return await self.session.get_prompt(prompt_name, arguments=final_arguments)
        except Exception as e:
            logger.error(f"获取提示词失败: {e}")
            return ""

    # ============ Resources 相关 (新添加) ============ #
    async def read_resource(self, resource_uri: str) -> str:
        """
        读取资源内容 (高级接口)
        """
        try:
            # 复用连接
            return await self.session.read_resource(resource_uri)
        except Exception as e:
            logger.error(f"读取资源失败: {e}")
            return ""

# ============ 全局实例 ============ 单例模式

_adapter: Optional[MCPAdapter] = None


# 单例模式（get_mcp_adapter）：全系统只需要一个Client实例，保持连接状态，避免重复连接的性能损失。
def get_mcp_adapter() -> MCPAdapter:
    global _adapter
    if _adapter is None:
        _adapter = MCPAdapter()
    return _adapter
