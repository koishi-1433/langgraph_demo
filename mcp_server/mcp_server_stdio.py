"""MCP stdio Server - 统一注册所有外部工具（全部 async，避免 asyncio.run 黑洞）

V2 契约（P2-V2 #6/#10）：
- 所有工具返回 FastMCP ToolResult：text = 精简文本摘要（可直接展示），
  structuredContent = 完整结构化数据（LLM 无需自行重组 JSON）；
- 每次调用记录耗时 / 成功 / error_code 结构化日志（走 stderr，不污染 stdio 协议）。
"""
import asyncio
import functools
import json
import logging
import re
import sys
import os
import time
from datetime import datetime
from typing import Annotated

from pydantic import Field

# Windows 下强制 UTF-8，避免日志/输出中的 emoji 触发 GBK UnicodeEncodeError
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# 确保项目根目录在路径中，使 config.tool_amap 等绝对导入可用
_project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _project_root)

from fastmcp import FastMCP
from fastmcp.tools.base import ToolResult
from mcp.types import TextContent

# 使用绝对导入（脚本独立运行时 sys.path 已包含项目根目录）
from mcp_server.tool_tavily import web_search
from mcp_server.tool_amap.async_wrappers import geocode, regeocode, route_planning, query_train_schedule

mcp = FastMCP("langgraph_demo_tools", version="1.0.0")

# ==================== 调用级指标日志（P2-V2 #10） ====================
logger = logging.getLogger("mcp_server_tools")
if not logger.handlers:
    _h = logging.StreamHandler(sys.stderr)
    _h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))
    logger.addHandler(_h)
logger.setLevel(logging.INFO)


def _instrument(name: str):
    """记录每次工具调用的耗时 / 成功 / error_code（结构化日志，输出到 stderr）。"""
    def deco(fn):
        @functools.wraps(fn)
        async def wrapper(*args, **kwargs):
            start = time.perf_counter()
            try:
                result = await fn(*args, **kwargs)
                elapsed_ms = (time.perf_counter() - start) * 1000
                payload = None
                if isinstance(result, ToolResult):
                    payload = result.structured_content
                elif isinstance(result, str):
                    try:
                        payload = json.loads(result)
                    except Exception:
                        pass
                if isinstance(payload, dict) and payload.get("success") is False:
                    logger.info("[call] name=%s elapsed_ms=%.1f status=ERR error_code=%s",
                                name, elapsed_ms, payload.get("error_code", "UNKNOWN"))
                else:
                    logger.info("[call] name=%s elapsed_ms=%.1f status=OK", name, elapsed_ms)
                return result
            except Exception as e:
                elapsed_ms = (time.perf_counter() - start) * 1000
                logger.error("[call] name=%s elapsed_ms=%.1f status=EXC error=%s",
                             name, elapsed_ms, e)
                raise
        return wrapper
    return deco


# ==================== 结构化返回（P2-V2 #6） ====================
def _result(payload: dict, summary: str) -> ToolResult:
    """构造结构化工具结果：text = 精简摘要（可直接展示），structuredContent = 完整数据。

    FastMCP 3.x 对 ToolResult 原样透传：output_schema 不自动生成（避免二次包装），
    服务端同时下发 content 与 structuredContent。
    """
    return ToolResult(
        content=[TextContent(type="text", text=summary)],
        structured_content=payload,
    )


_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_TIME_RE = re.compile(r"^\d{2}:\d{2}$")
_VALID_MODES = ("auto", "train", "driving", "transit")
_HINT_INVALID = "请检查参数格式后重试"


def _invalid_input(msg: str, hint: str = None) -> ToolResult:
    payload = {"success": False, "error_code": "INVALID_INPUT", "error": msg,
               "hint": hint or _HINT_INVALID}
    return _result(payload, f"参数错误：{msg}（{payload['hint']}）")


def _valid_date(date: str) -> bool:
    """YYYY-MM-DD 且为真实日期（拒绝 2026-13-99 之类）。"""
    if not _DATE_RE.match(date):
        return False
    try:
        datetime.strptime(date, "%Y-%m-%d")
        return True
    except ValueError:
        return False


def _valid_time(value: str) -> bool:
    """HH:MM 且为真实时刻（拒绝 25:99 之类）。"""
    if not _TIME_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False


@mcp.tool()
@_instrument("tavily_web_search")
async def tavily_web_search(
    query: Annotated[str, Field(description="搜索关键词，如「DeepSeek 最新发布」")],
    max_results: Annotated[int, Field(default=5, ge=1, le=10,
                                      description="返回结果数量，1~10，默认 5")] = 5,
) -> ToolResult:
    """通过 Tavily 搜索引擎查询网页内容。返回摘要和来源列表。"""
    if max_results < 1 or max_results > 10:
        return _invalid_input(f"max_results 需在 1~10 之间，收到 {max_results}")
    # TavilySearch.invoke 为同步实现，在工作线程中执行避免阻塞事件循环
    raw = await asyncio.to_thread(web_search, query=query, max_results=max_results)
    payload = json.loads(raw)
    if payload.get("error_code"):
        return _result(payload, f"搜索「{query}」失败：{payload.get('error')}")
    sources = payload.get("sources", [])
    lines = [f"搜索「{query}」共 {len(sources)} 条结果"]
    if payload.get("answer"):
        lines.append(f"摘要：{payload['answer']}")
    if sources:
        lines.append("来源：")
        lines += [f"{i + 1}. {s.get('title')} ({s.get('url')})"
                  for i, s in enumerate(sources)]
    return _result(payload, "\n".join(lines))


@mcp.tool()
@_instrument("amap_geocode")
async def amap_geocode(
    address: Annotated[str, Field(description="要转换为经纬度的地址文本，如「北京市朝阳区建国门外大街1号」")],
) -> ToolResult:
    """将地址文本转换为经纬度坐标 (lng,lat)。"""
    payload = json.loads(await geocode(address))
    if payload.get("status") == "success":
        summary = f"地址「{address}」的经纬度坐标：{payload['location']}"
    else:
        summary = f"地址解析失败：{payload.get('reason') or payload.get('error')}；{payload.get('hint', '')}"
    return _result(payload, summary)


@mcp.tool()
@_instrument("amap_regeocode")
async def amap_regeocode(
    location: Annotated[str, Field(description="经纬度坐标，格式 lng,lat，如「116.397428,39.90923」")],
) -> ToolResult:
    """将经纬度坐标转回地址信息（城市、区县等）。"""
    payload = json.loads(await regeocode(location))
    if payload.get("status") == "success" or payload.get("formatted_address"):
        summary = f"坐标 {location} 对应地址：{payload.get('formatted_address')}"
    else:
        summary = f"逆地理编码失败：{payload.get('reason') or payload.get('error')}；{payload.get('hint', '')}"
    return _result(payload, summary)


@mcp.tool()
@_instrument("amap_route_planning")
async def amap_route_planning(
    origin: Annotated[str, Field(description="起点城市名，如「北京」；留空表示使用当前位置")],
    destination: Annotated[str, Field(description="终点城市名，如「上海」")],
    mode: Annotated[str, Field(default="auto",
                               description="交通方式：auto 自动选择（>120km 火车，短途驾车/公交择优）、train、driving、transit")] = "auto",
) -> ToolResult:
    """规划路线（驾车/公交/火车），mode=auto 时按距离自动选择最佳交通方式。"""
    if mode not in _VALID_MODES:
        return _invalid_input(f"mode 仅支持 {_VALID_MODES}，收到 {mode!r}")
    payload = json.loads(await route_planning(origin=origin, destination=destination, mode=mode))
    if payload.get("error"):
        summary = f"路线规划失败：{payload.get('error')}；{payload.get('hint', '')}"
    else:
        summary = "\n".join(payload.get("route_details") or []) or f"路线规划完成：{payload.get('actual_mode')}"
    return _result(payload, summary)


@mcp.tool()
@_instrument("py12306_query_train_schedule")
async def py12306_query_train_schedule(
    from_station: Annotated[str, Field(description="出发城市或车站名，如「北京」（自动映射主要车站）")],
    to_station: Annotated[str, Field(description="到达城市或车站名，如「上海」")],
    date: Annotated[str, Field(default=None, description="出发日期 YYYY-MM-DD，默认当天")] = None,
    only_available: Annotated[bool, Field(default=False,
                                          description="仅返回有余票的班次（P2-V2 #8）")] = False,
    earliest_time: Annotated[str, Field(default=None,
                                        description="最早发车时间 HH:MM（含），过滤早于该时段的班次")] = None,
    latest_time: Annotated[str, Field(default=None,
                                      description="最晚发车时间 HH:MM（含），过滤晚于该时段的班次")] = None,
) -> ToolResult:
    """查询两个车站之间的火车班次信息（车次、时间、票价；支持余票/发车时段过滤）。"""
    if date is not None and not _valid_date(date):
        return _invalid_input(f"date 需为有效的 YYYY-MM-DD 日期，收到 {date!r}")
    for label, value in (("earliest_time", earliest_time), ("latest_time", latest_time)):
        if value is not None and not _valid_time(value):
            return _invalid_input(f"{label} 需为 HH:MM 格式（00:00~23:59），收到 {value!r}")
    payload = json.loads(await query_train_schedule(
        from_station=from_station, to_station=to_station, date=date,
        only_available=only_available, earliest_time=earliest_time, latest_time=latest_time,
    ))
    if not payload.get("success"):
        return _result(payload, f"班次查询失败：{payload.get('error')}；{payload.get('hint', '')}")
    show_date = date or datetime.now().strftime("%Y-%m-%d")
    lines = [
        f"{payload.get('from_station')} → {payload.get('to_station')}（{show_date}）："
        f"共 {payload.get('total_trains')} 趟，匹配 {payload.get('total_matched')} 趟"
    ]
    for t in payload.get("trains", []):
        price = t.get("price") if isinstance(t.get("price"), dict) else {}
        second = price.get("second_class")
        if isinstance(second, (int, float)):
            second_txt = f"¥{second:.0f}"
        else:
            second_txt = (t.get("seats") or {}).get("second_class", "--")
        lines.append(
            f"  {t.get('train_number')} {t.get('departure_time')}→{t.get('arrival_time')} "
            f"历时{t.get('duration')} 二等座{second_txt}"
        )
    return _result(payload, "\n".join(lines))


if __name__ == "__main__":
    mcp.run(transport="stdio")
