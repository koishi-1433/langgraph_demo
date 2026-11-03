"""
client.py
MCP 客户端，支持双模式：
- 同进程模式（内部调用，无网络开销）
- HTTP Streamable HTTP 模式（外部连接）
"""
from contextlib import asynccontextmanager
from typing import Optional, List, Any, Dict, Union

from fastmcp import Client
from MCP.config.settings import settings
from MCP.core.logger import get_logger

logger = get_logger(__name__)

# ==================== Streamable HTTP 客户端 ====================


class MCPStreamHTTPClient:
    """Streamable HTTP 模式的 MCP 客户端（通过 HTTP 连接远程服务器）"""

    def __init__(self, server_url: str = "http://localhost:9000"):
        self.server_url = server_url.rstrip("/")
        self.client: Optional[Client] = None
        self._tools_cache = None
        self._prompt_cache = None
        self._resources_cache = None
        logger.info(f"初始化 MCP HTTP 客户端: {self.server_url}")

    @asynccontextmanager
    async def connect(self):
        try:
            logger.info(f"连接 MCP 服务器 (Streamable HTTP): {self.server_url}...")
            # FastMCP 3.x 必须显式指定传输：根 URL 会被推断成另一种协议，
            # 与旧 SSE 服务端端点不匹配导致初始化超时（DEF-03）。
            from fastmcp.client.transports import StreamableHttpTransport
            url = (self.server_url if self.server_url.rstrip("/").endswith("/mcp")
                   else f"{self.server_url.rstrip('/')}/mcp")
            self.client = Client(StreamableHttpTransport(url), init_timeout=15)
            async with self.client:
                await self.client.ping()
                logger.info("✅ MCP HTTP 连接成功")
                yield self
        except Exception as e:
            logger.error(f"MCP HTTP 连接失败: {e}")
            raise

    async def list_tools(self) -> List[Any]:
        if self._tools_cache is not None:
            return self._tools_cache
        try:
            tools = await self.client.list_tools()
            self._tools_cache = tools
            return tools
        except Exception as e:
            logger.error(f"获取工具失败: {e}")
            return []

    async def list_prompts(self) -> List[Any]:
        if self._prompt_cache is not None:
            return self._prompt_cache
        try:
            prompts = await self.client.list_prompts()
            self._prompt_cache = prompts
            return prompts
        except Exception as e:
            logger.error(f"获取提示词失败: {e}")
            return []

    async def list_resources(self) -> List[Any]:
        if self._resources_cache is not None:
            return self._resources_cache
        try:
            resources = await self.client.list_resources()
            self._resources_cache = resources
            return resources
        except Exception as e:
            logger.error(f"获取资源失败: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        try:
            return await self.client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.error(f"调用工具失败: {e}")
            raise

    async def get_prompt(self, prompt_name: str, arguments: Dict[str, Any] = None) -> str:
        try:
            response = await self.client.get_prompt(prompt_name, arguments or {})
            if hasattr(response, 'messages') and response.messages:
                return response.messages[0].content.text
            return ""
        except Exception as e:
            logger.error(f"获取提示词失败: {e}")
            return ""

    async def read_resource(self, resource_uri: str) -> str:
        try:
            return await self.client.read_resource(resource_uri)
        except Exception as e:
            logger.error(f"读取资源失败: {e}")
            return ""


# ==================== 同进程客户端（直连 server 实例） ====================


class MCPInProcessClient:
    """同进程 MCP 客户端（直连 server 实例，零网络开销）"""

    def __init__(self):
        self.client: Optional[Client] = None
        self._tools_cache = None
        self._prompt_cache = None
        self._resources_cache = None
        logger.info("初始化 MCP 同进程客户端")

    @asynccontextmanager
    async def connect(self):
        try:
            logger.info("连接 MCP 服务器（同进程）...")
            from MCP.protocol.server import mcp as server_app

            self.client = Client(server_app)
            async with self.client:
                await self.client.ping()
                logger.info("✅ MCP 同进程连接成功")
                yield self
        except Exception as e:
            logger.error(f"同进程连接失败: {e}")
            raise

    async def list_tools(self) -> List[Any]:
        if self._tools_cache is not None:
            return self._tools_cache
        try:
            tools = await self.client.list_tools()
            self._tools_cache = tools
            return tools
        except Exception as e:
            logger.error(f"获取工具失败: {e}")
            return []

    async def list_prompts(self) -> List[Any]:
        if self._prompt_cache is not None:
            return self._prompt_cache
        try:
            prompts = await self.client.list_prompts()
            self._prompt_cache = prompts
            return prompts
        except Exception as e:
            logger.error(f"获取提示词失败: {e}")
            return []

    async def list_resources(self) -> List[Any]:
        if self._resources_cache is not None:
            return self._resources_cache
        try:
            resources = await self.client.list_resources()
            self._resources_cache = resources
            return resources
        except Exception as e:
            logger.error(f"获取资源失败: {e}")
            return []

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        try:
            return await self.client.call_tool(tool_name, arguments)
        except Exception as e:
            logger.error(f"调用工具失败: {e}")
            raise

    async def get_prompt(self, prompt_name: str, arguments: Dict[str, Any] = None) -> str:
        try:
            response = await self.client.get_prompt(prompt_name, arguments or {})
            if hasattr(response, 'messages') and response.messages:
                return response.messages[0].content.text
            return ""
        except Exception as e:
            logger.error(f"获取提示词失败: {e}")
            return ""

    async def read_resource(self, resource_uri: str) -> str:
        try:
            return await self.client.read_resource(resource_uri)
        except Exception as e:
            logger.error(f"读取资源失败: {e}")
            return ""


# ==================== 全局实例 ====================
# 默认使用同进程模式（零开销）；MCP_HTTP_URL 由 Settings 统一读取（.env），
# 非空时切换到远程 HTTP 模式。全系统只在此处决定传输模式。

_MCP_HTTP_URL = settings.MCP_HTTP_URL


def create_mcp_client():
    """根据环境变量创建合适的 MCP 客户端"""
    if _MCP_HTTP_URL:
        logger.info(f"使用 Streamable HTTP 模式连接: {_MCP_HTTP_URL}")
        return MCPStreamHTTPClient(server_url=_MCP_HTTP_URL)
    else:
        logger.info("使用同进程模式")
        return MCPInProcessClient()


_client_instance: Optional[Union[MCPInProcessClient, MCPStreamHTTPClient]] = None


def get_mcp_client():
    """获取全局 MCP 客户端实例"""
    global _client_instance
    if _client_instance is None:
        _client_instance = create_mcp_client()
    return _client_instance
