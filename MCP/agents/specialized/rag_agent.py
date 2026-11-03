"""
rag_agent.py
路径: MCP/agents/specialized/rag_agent.py
功能: RAG 问答智能体 —— 经统一 MCPAdapter 调用 MCP Server 的 rag_* 工具。

旧实现只是把 temp_docs 用换行拼起来（没有向量检索、没有持久化），
且 graph.py 里 final_output 又把它丢弃。现在改为：
    rag_add_document  把搜索生成的攻略切块写入本地向量知识库
    rag_query         用用户原始问题做语义检索，返回带来源的片段
    rag_stats / rag_clear  知识库管理
所有调用复用 workflow 持有的同一个 MCPAdapter 连接（P0-4 传输模式统一）。
"""

import json
from typing import Any, Dict, Optional

from MCP.config.settings import settings
from MCP.core.logger import get_logger
from MCP.workflow.state import AgentResponse

logger = get_logger(__name__)


class RAGAgent:
    """RAG 工具的 Agent 层封装（不自建连接，不直接加载嵌入模型）"""

    def __init__(self, mcp_adapter=None, top_k: Optional[int] = None):
        if mcp_adapter is None:
            from MCP.integration.tool_adapter import get_mcp_adapter
            mcp_adapter = get_mcp_adapter()
        self.mcp_adapter = mcp_adapter
        self.top_k = int(top_k or settings.RAG_TOP_K)
        logger.info(f"RAGAgent 初始化：走统一 MCPAdapter，top_k={self.top_k}")

    # ------------------------------------------------------------------
    async def _call_mcp_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用 MCP 工具并把 CallToolResult 解析成 dict（与 RouteAgent 一致）。"""
        result = await self.mcp_adapter.call_tool(tool_name, arguments)
        if getattr(result, "content", None):
            text = result.content[0].text
            try:
                return json.loads(text)
            except (json.JSONDecodeError, TypeError):
                return {"success": False, "error": f"无法解析 MCP 返回: {str(text)[:200]}"}
        return {"success": False, "error": "MCP 工具返回空结果"}

    # ------------------------------------------------------------------
    async def process(self, input_data: Any) -> Dict[str, Any]:
        """BaseAgent 风格的处理入口：输入查询字符串，返回检索结果。"""
        if not isinstance(input_data, str):
            raise ValueError("RAGAgent expects string input for retrieval")
        response = await self.retrieve_rag(input_data)
        return {
            "rag_text": response.content.get("rag_text", ""),
            "hits": response.content.get("hits", []),
            "success": response.success,
            "error": response.error,
        }

    async def load_search_to_temp(self,
                                  search_content: str,
                                  source: str = "",
                                  title: str = "") -> AgentResponse:
        """把搜索攻略写入持久化知识库（保留旧方法名，行为已换成真实向量入库）。"""
        if not search_content or not search_content.strip():
            return AgentResponse(success=False, content={"message": "内容为空，跳过入库"},
                                 error="空内容")
        source = source or "workflow"
        source_key = f"tavily:{source}" if not source.startswith("tavily:") else source
        try:
            data = await self._call_mcp_tool("rag_add_document", {
                "text": search_content,
                "source": source_key,
                "title": title or f"{source}旅游攻略",
            })
            if not data.get("success"):
                return AgentResponse(
                    success=False,
                    content={"message": "内容入库失败"},
                    error=data.get("error", "未知错误"),
                    metadata={"disabled": data.get("disabled", False)},
                )
            return AgentResponse(
                success=True,
                content={
                    "message": "内容已写入本地向量知识库",
                    "chunks_added": data.get("chunks_added", 0),
                    "total_chunks": data.get("total_chunks", 0),
                },
                metadata={"source": source_key},
            )
        except Exception as exc:
            logger.error(f"RAG 入库调用异常: {exc}")
            return AgentResponse(success=False, content={"message": "入库调用异常"}, error=str(exc))

    async def retrieve_rag(self, query: str) -> AgentResponse:
        """语义检索：返回 rag_text（可直接拼接的补充文本）与命中片段列表。"""
        try:
            data = await self._call_mcp_tool("rag_query", {"question": query, "k": self.top_k})
            if not data.get("success"):
                return AgentResponse(
                    success=False,
                    content={"rag_text": "", "hits": []},
                    error=data.get("error", "RAG 检索失败"),
                    metadata={"disabled": data.get("disabled", False)},
                )
            return AgentResponse(
                success=True,
                content={
                    "rag_text": data.get("rag_text", ""),
                    "hits": data.get("hits", []),
                    "total_chunks": data.get("total_chunks", 0),
                },
                metadata={
                    "retrieved": data.get("retrieved", 0),
                    "filtered_out": data.get("filtered_out", 0),
                    "rag_type": data.get("rag_type", ""),
                },
            )
        except Exception as exc:
            logger.error(f"RAG 检索调用异常: {exc}")
            return AgentResponse(success=False, content={"rag_text": "", "hits": []}, error=str(exc))

    async def stats(self) -> AgentResponse:
        """查询知识库状态。"""
        try:
            data = await self._call_mcp_tool("rag_stats", {})
            return AgentResponse(success=bool(data.get("success")), content=data,
                                 error=data.get("error"))
        except Exception as exc:
            return AgentResponse(success=False, content={}, error=str(exc))

    async def clear_rag(self) -> AgentResponse:
        """清空知识库（显式确认，危险操作）。"""
        try:
            data = await self._call_mcp_tool("rag_clear", {"confirm": True})
            return AgentResponse(success=bool(data.get("success")), content=data,
                                 error=data.get("error"))
        except Exception as exc:
            return AgentResponse(success=False, content={}, error=str(exc))
