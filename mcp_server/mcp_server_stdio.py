"""MCP stdio Server - 统一注册所有外部工具"""
import sys
import os

# 确保项目根目录在路径中，使 config.tool_amap 等绝对导入可用
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from fastmcp import FastMCP

# 使用绝对导入（脚本独立运行时 sys.path 已包含项目根目录）
from mcp_server.tool_tavily import web_search
from mcp_server.tool_amap.wrappers import geocode, regeocode, route_planning, query_train_schedule

mcp = FastMCP("langgraph_demo_tools", version="1.0.0")


@mcp.tool()
def tavily_web_search(query: str, max_results: int = 5) -> str:
    """通过 Tavily 搜索引擎查询网页内容。返回摘要和来源列表。"""
    return web_search(query=query, max_results=max_results)


@mcp.tool()
def amap_geocode(address: str) -> str:
    """将地址文本转换为经纬度坐标 (lng,lat)。"""
    return geocode(address)


@mcp.tool()
def amap_regeocode(location: str) -> str:
    """将经纬度坐标转回地址信息（城市、区县等）。"""
    return regeocode(location)


@mcp.tool()
def amap_route_planning(origin: str, destination: str, mode: str = "auto") -> str:
    """规划路线（驾车/公交/火车），mode=auto 时自动选择最佳交通方式。"""
    return route_planning(origin=origin, destination=destination, mode=mode)


@mcp.tool()
def py12306_query_train_schedule(from_station: str, to_station: str, date: str = None) -> str:
    """查询两个车站之间的火车班次信息（车次、时间、票价）。"""
    return query_train_schedule(from_station=from_station, to_station=to_station, date=date)


if __name__ == "__main__":
    mcp.run(transport="stdio")
