"""Tavily Web Search 工具封装（同步实现，供 MCP stdio 服务端在工作线程中调用）。

- 通过 langchain_tavily.TavilySearch 调用 Tavily API，返回 JSON 字符串
  {"answer": ..., "sources": [...]}，由 mcp_server_stdio.tavily_web_search 包成 ToolResult；
- 上层调用方（mcp_server_stdio）用 asyncio.to_thread 执行本函数，
  避免同步网络请求阻塞事件循环；
- settings 导入失败时回退为直接读环境变量，保证脚本可独立运行。
"""
import json
from typing import Dict, Any

from langchain_tavily import TavilySearch

# Settings 由 mcp_server_stdio.py 设置 sys.path 后可正常导入
try:
    from config.settings import settings
except ImportError:
    # fallback：直接读环境变量（脚本独立运行场景）
    import os

    class _FallbackSettings:
        TAVILY_API_KEY = os.getenv("TAVILY_API_KEY", "")
        TAVILY_MAX_RESULTS = int(os.getenv("TAVILY_MAX_RESULTS", "5"))

    settings = _FallbackSettings()


def web_search(query: str, max_results: int = None) -> str:
    """通过 Tavily 搜索引擎查询网页内容。返回摘要和来源列表。

    Args:
        query: 搜索关键词
        max_results: 返回结果数量，默认取 settings.TAVILY_MAX_RESULTS（边界收敛到 1~20）

    Returns:
        JSON string:
        - 成功: {"answer": "...", "sources": [{"title":"...", "url":"...", "score":0}]}
        - 失败: {"error": "...", "error_code": "TAVILY_SEARCH_FAILED"}
    """
    if max_results is None:
        max_results = settings.TAVILY_MAX_RESULTS
    # 边界保护：Tavily 只接受 1~20
    max_results = min(max(1, int(max_results)), 20)

    tavily = TavilySearch(
        api_key=settings.TAVILY_API_KEY,
        max_results=max_results,
        include_answer=True,  # 让 answer 字段真正返回 AI 摘要
    )

    try:
        response = tavily.invoke({"query": query})
        answer = response.get("answer", "")
        sources = []
        for item in response.get("results", []):
            sources.append(
                {
                    "title": item.get("title", ""),
                    "url": item.get("url", ""),
                    "score": item.get("score", 0),
                }
            )
        return json.dumps({"answer": answer, "sources": sources}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": f"Tavily 搜索失败: {str(e)}",
                           "error_code": "TAVILY_SEARCH_FAILED"}, ensure_ascii=False)
