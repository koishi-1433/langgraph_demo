# -*- coding: utf-8 -*-
"""MCP 工具回归测试（mcp_server/）。

离线用例：工具注册/schema、参数校验、缓存校验、时长解析、票价语义、单一映射源、会话单例、
结构化返回（P2-V2 #6）、错误 hint（P2-V2 #7）、票价估算（P1-V2 #4）。
网络用例（--run-network）：MCP stdio 端到端调用全部 5 个工具、12306 损坏缓存自愈。
"""
import asyncio
import json
import os
from datetime import timedelta
from pathlib import Path

import pytest

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from mcp_server import mcp_server_stdio as server_mod
from mcp_server.tool_amap import async_wrappers
from mcp_server.tool_amap.route_planner import RoutePlanner
from mcp_server.tool_amap.train_crawler import Py12306Client
from mcp_server.tool_amap.utils import estimate_train_cost

EXPECTED_TOOLS = {
    "tavily_web_search",
    "amap_geocode",
    "amap_regeocode",
    "amap_route_planning",
    "py12306_query_train_schedule",
}

SERVER_SCRIPT = str(
    Path(__file__).resolve().parent.parent / "mcp_server" / "mcp_server_stdio.py"
)
STATION_CACHE_FILE = (
    Path(__file__).resolve().parent.parent
    / "mcp_server" / "tool_amap" / "data" / "station_codes.json"
)


def _payload(result):
    """提取工具返回的结构化数据（兼容 str 与 FastMCP ToolResult）。"""
    if isinstance(result, str):
        return json.loads(result)
    if hasattr(result, "structured_content"):
        return result.structured_content
    return result


# ==================== 离线用例 ====================

def test_mcp_tools_registered():
    tools = asyncio.run(server_mod.mcp.list_tools())
    names = {t.name for t in tools}
    assert names == EXPECTED_TOOLS
    desc = {t.name: t.description for t in tools}
    assert "Tavily" in desc["tavily_web_search"]
    assert "经纬度" in desc["amap_geocode"]
    assert "火车" in desc["py12306_query_train_schedule"]


def test_tool_schema_fields():
    tools = {t.name: t for t in asyncio.run(server_mod.mcp.list_tools())}
    schema = tools["tavily_web_search"].parameters
    assert "query" in schema["required"]
    max_results = schema["properties"]["max_results"]
    assert max_results.get("default") == 5
    assert max_results.get("minimum") == 1
    assert max_results.get("maximum") == 10
    assert "description" in max_results  # P2-14：参数级描述已进入 schema

    py_schema = tools["py12306_query_train_schedule"].parameters
    assert "date" in py_schema["properties"]
    assert py_schema["required"] == ["from_station", "to_station"]
    # P2-V2 #8：过滤参数进入 schema
    for param in ("only_available", "earliest_time", "latest_time"):
        assert param in py_schema["properties"], f"缺少过滤参数 {param}"


def test_invalid_input_rejected():
    """非法参数在入口即返回 INVALID_INPUT，不发网络请求。"""
    cases = [
        (server_mod.tavily_web_search, {"query": "x", "max_results": 99}),
        (server_mod.amap_route_planning, {"origin": "北京", "destination": "上海", "mode": "hovercraft"}),
        (server_mod.py12306_query_train_schedule, {"from_station": "北京", "to_station": "上海", "date": "2026-13-99"}),
        (server_mod.py12306_query_train_schedule, {"from_station": "北京", "to_station": "上海", "earliest_time": "25:99"}),
    ]
    for fn, kwargs in cases:
        payload = _payload(asyncio.run(fn(**kwargs)))
        assert payload["success"] is False
        assert payload["error_code"] == "INVALID_INPUT"
        assert payload.get("hint"), "INVALID_INPUT 应带 hint（P2-V2 #7）"


def test_tool_result_structured_content():
    """P2-V2 #6：工具返回 ToolResult（text=摘要、structuredContent=完整数据），无需 LLM 重组。"""
    result = asyncio.run(server_mod.mcp.call_tool(
        "py12306_query_train_schedule",
        {"from_station": "北京", "to_station": "上海", "date": "2026-13-99"},
    ))
    assert result.structured_content["error_code"] == "INVALID_INPUT"
    assert result.content and result.content[0].text, "文本摘要可直接展示"
    assert "参数错误" in result.content[0].text
    # 文本摘要与结构化数据一致（摘要来自同一 payload）
    assert result.content[0].text != json.dumps(result.structured_content, ensure_ascii=False)


def test_estimate_train_cost_tiers():
    """P1-V2 #4：haversine×1.15 绕行系数 + 分级费率 + 硬顶 2000。"""
    # 北京 → 广州 直线约 1889km：真实二等座约 850，估算应落在 ±10%
    cost_bj_gz = estimate_train_cost("116.407394,39.904211", "113.264385,23.129112")
    assert 765 <= cost_bj_gz <= 935, f"京广估算应≈850±10%，实际 {cost_bj_gz}"
    assert cost_bj_gz <= 2000
    # 短途 <500km 用 0.5 费率：北京→天津 约 114km → 114×1.15×0.5≈65
    cost_bj_tj = estimate_train_cost("116.407394,39.904211", "117.200983,39.084158")
    assert 50 <= cost_bj_tj <= 90, f"短途估算量级，实际 {cost_bj_tj}"
    # 坐标缺失/非法：回退默认价
    assert estimate_train_cost(None, None) == 300.0
    assert estimate_train_cost("bad", "bad") == 300.0


def test_station_codes_validation():
    """P0-1：损坏缓存（如 {"": ""}）必须被拒绝。"""
    valid = {f"站{i:03d}": "ABC" for i in range(150)}
    assert Py12306Client._is_valid_station_codes(valid) is True
    assert Py12306Client._is_valid_station_codes({"": ""}) is False
    assert Py12306Client._is_valid_station_codes({"北京": "BJP"}) is False  # 条目过少
    assert Py12306Client._is_valid_station_codes({"北京": "bad!"}) is False
    assert Py12306Client._is_valid_station_codes(None) is False
    assert Py12306Client._is_valid_station_codes([]) is False


def test_parse_train_duration():
    """P2-9：时长解析覆盖多种格式。"""
    planner = RoutePlanner(amap_key=None)
    cases = {
        "06:41": 401,
        "4-6 hours": 360,   # 区间取较大值
        "5小时": 300,
        "30分钟": 30,
        "4h 30m": 270,
        "45 min": 45,
        "7h 33m": 453,      # P1-V2 #3：占位数据动态时长格式
        "--": 0,
        "": 0,
        "abc": 0,
    }
    for raw, expected in cases.items():
        assert planner._parse_train_duration(raw) == expected, f"{raw!r} -> {expected}"


def test_price_semantics():
    """P1-3：余票（seats）绝不能当票价；优先真实票价，否则估算回退。"""
    planner = RoutePlanner(amap_key=None)
    # 有真实票价：用票价
    info = {"second_class": "18", "price": {"second_class": 553.0}}
    assert planner._resolve_train_cost(info, None, None) == 553.0
    # 无票价（即使余票是数字）：回退估算，绝不用 18
    info_no_price = {"second_class": "18"}
    assert planner._resolve_train_cost(info_no_price, None, None) == 300.0
    # 票价为 0/负：回退估算
    info_bad = {"price": {"second_class": 0}}
    assert planner._resolve_train_cost(info_bad, None, None) == 300.0


def test_extract_prices_mapping():
    prices = Py12306Client._extract_prices({"A3": {"seat_price": "553.0"}})
    assert prices.get("second_class") == 553.0
    prices_bad = Py12306Client._extract_prices({"A3": {"seat_price": "无"}})
    assert prices_bad.get("second_class") is None
    prices_unknown = Py12306Client._extract_prices({"ZZ": {"seat_price": "1"}})
    assert prices_unknown == {}


def test_single_source_station_map():
    """P1-2：城市→车站映射只保留一份权威表。"""
    assert not hasattr(async_wrappers, "_CITY_STATION_MAP")
    assert Py12306Client.CITY_STATION_MAP["北京"] == "北京南"
    assert Py12306Client.CITY_STATION_MAP["上海"] == "上海虹桥"
    # route_planner 与 train_crawler 引用同一个类
    from mcp_server.tool_amap import route_planner as rp_mod
    assert rp_mod.Py12306Client is Py12306Client


def test_shared_amap_singleton():
    """P0-2：高德客户端为模块级单例（会话复用）。"""
    try:
        a1 = async_wrappers._get_shared_amap()
        a2 = async_wrappers._get_shared_amap()
        assert a1 is a2
    finally:
        async_wrappers.close_shared_clients()


# ==================== 网络用例（--run-network） ====================

async def _call_tool_raw(name: str, args: dict, timeout: int = 180):
    params = StdioServerParameters(command=os.sys.executable, args=[SERVER_SCRIPT])
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            names = {t.name for t in tools.tools}
            assert names == EXPECTED_TOOLS
            return await session.call_tool(
                name, args, read_timeout_seconds=timedelta(seconds=timeout)
            )


async def _call_tool(name: str, args: dict, timeout: int = 180):
    """调用工具并提取结构化数据：优先 structuredContent（P2-V2 #6），否则解析文本。"""
    result = await _call_tool_raw(name, args, timeout)
    if getattr(result, "structuredContent", None):
        return result.structuredContent
    texts = [
        c.text for c in result.content if getattr(c, "type", None) == "text"
    ]
    return json.loads("\n".join(texts))


@pytest.mark.network
def test_e2e_all_tools(capfd):
    """端到端调用全部 5 个工具，且服务端无会话泄漏告警。"""
    # P2-V2 #6：tools/call 返回含 structuredContent（结构化数据，非纯 JSON 文本）
    raw = asyncio.run(_call_tool_raw("amap_geocode", {"address": "北京市朝阳区建国门外大街1号"}))
    assert raw.structuredContent, "tools/call 应返回 structuredContent"
    assert raw.content and raw.content[0].text, "文本摘要可直接展示"

    tavily = asyncio.run(_call_tool("tavily_web_search", {"query": "DeepSeek 最新发布", "max_results": 3}))
    assert tavily.get("sources"), "Tavily 应返回来源列表"
    assert "answer" in tavily  # P2-12：启用 include_answer 后有 answer 字段

    geo = asyncio.run(_call_tool("amap_geocode", {"address": "北京市朝阳区建国门外大街1号"}))
    assert geo["status"] == "success"
    assert "," in geo["location"]

    regeo = asyncio.run(_call_tool("amap_regeocode", {"location": "116.397428,39.90923"}))
    assert regeo["status"] == "success"
    assert regeo.get("formatted_address")

    route = asyncio.run(_call_tool("amap_route_planning", {"origin": "北京", "destination": "上海", "mode": "auto"}))
    assert route.get("transport_mode")
    assert route.get("segment_1") and route.get("segment_2") and route.get("segment_3")
    assert route["total_duration"] > 0
    # P1-3：cost 必须是合理估算或真实票价，绝不可能是余票数字
    assert 0 < route["total_cost"] < 5000
    # P1-V2 #5：市内段费用口径标注
    assert route.get("segment_cost_note") == "市内接驳（按驾车估算）"
    assert route["segment_1"].get("cost_note") == route["segment_cost_note"]
    # P1-1：第 1 段不再必然为 0（车站编码重试链）
    if route["train_info"] and not route["train_info"].get("placeholder"):
        assert route["total_duration"] >= 300  # 北京→上海火车至少 4 小时

    schedule = asyncio.run(_call_tool("py12306_query_train_schedule", {"from_station": "北京", "to_station": "上海"}))
    assert schedule["success"] is True
    assert schedule["total_trains"] > 0
    assert len(schedule["trains"]) >= 1
    first = schedule["trains"][0]
    assert first.get("train_number")
    assert "seats" in first  # 余票状态
    # P2-V2 #8：过滤参数
    filtered = asyncio.run(_call_tool(
        "py12306_query_train_schedule",
        {"from_station": "北京", "to_station": "上海", "only_available": True, "earliest_time": "06:00"},
    ))
    assert filtered["success"] is True
    assert filtered["total_matched"] <= filtered["total_trains"]
    # 票价字段要么缺失（接口不可用回退）要么为数字
    if "price" in first:
        price = first["price"].get("second_class")
        assert price is None or isinstance(price, (int, float))

    _out, err = capfd.readouterr()
    assert "Unclosed client session" not in err, "服务端不应泄漏 aiohttp 会话"


@pytest.mark.network
def test_corrupted_cache_self_heal():
    """P0-1：损坏的车站编码缓存会被自动丢弃并重新抓取，工具照常可用。"""
    original = None
    if STATION_CACHE_FILE.exists():
        original = STATION_CACHE_FILE.read_bytes()
    try:
        STATION_CACHE_FILE.write_text('{\n  "": ""\n}\n', encoding="utf-8")

        payload = asyncio.run(_call_tool(
            "py12306_query_train_schedule", {"from_station": "北京", "to_station": "上海"}
        ))
        assert payload["success"] is True, f"损坏缓存应自愈：{payload}"
        assert payload["total_trains"] > 0

        # 缓存文件应已被合法数据覆盖
        data = json.loads(STATION_CACHE_FILE.read_text(encoding="utf-8"))
        assert Py12306Client._is_valid_station_codes(data)
    finally:
        if original is not None:
            STATION_CACHE_FILE.write_bytes(original)
        elif STATION_CACHE_FILE.exists():
            STATION_CACHE_FILE.unlink()
