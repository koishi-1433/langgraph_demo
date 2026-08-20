# -*- coding: utf-8 -*-
"""V2 模拟数据回归（离线，无网络）— 沉淀 simulate_tools.py 的 S1~S13 场景 + V2 新行为断言。

只替换「数据源」层（Tavily 搜索、高德 geocode/driving/transit、12306 会话初始化/车站表），
工具入口（mcp_server_stdio）、async_wrappers、route_planner 三段式、缓存、错误协议全部走真实代码。

覆盖：
- S1~S13：V1 行为回归（对应 mcp_tool_test/simulate_tools.py）
- P0-V2 #1 确定性错误不重试、#9 失败负缓存
- P1-V2 #3/#4 占位动态生成 + 票价估算、#5 市内段费用口径
- P2-V2 #7 错误 hint、#8 班次过滤参数
"""
import asyncio
import json
from datetime import datetime

import pytest

from mcp_server import mcp_server_stdio as tools
from mcp_server.tool_amap import async_wrappers
from mcp_server.tool_amap.client import UnifiedAMapClient
from mcp_server.tool_amap.route_planner import RoutePlanner
from mcp_server.tool_amap.train_crawler import Py12306Client

# ==================== 模拟数据 ====================
# 高德地理编码：地址 -> "lng,lat"
FAKE_GEOCODE = {
    "北京市朝阳区建国门外大街1号": "116.459427,39.910358",
    # 注意：故意不给 "北京南"，只给 "北京南站"，用于验证 P1-1 编码重试链
    "北京": "116.407394,39.904211",
    "北京南站": "116.378471,39.865187",
    "上海": "121.473701,31.230416",
    "上海虹桥": "121.320756,31.194677",
    "天津": "117.200983,39.084158",
    "广州": "113.264385,23.129112",
    "广州南": "113.269233,22.993591",
}
FAKE_REGEO = {
    "116.397428,39.90923": {
        "status": "success",
        "location": "116.397428,39.90923",
        "formatted_address": "北京市东城区东华门街道天安门",
        "country": "中国", "province": "北京市", "city": [], "district": "东城区",
        "township": "东华门街道", "street": "", "street_number": "",
        "business_areas": [],
    }
}
# 驾车路线：坐标对 -> (distance_m, duration_s)
FAKE_DRIVING = {
    ("116.407394,39.904211", "116.378471,39.865187"): (7000, 1500),   # 北京→北京南站
    ("121.320756,31.194677", "121.473701,31.230416"): (8000, 1800),   # 上海虹桥→上海
    ("113.269233,22.993591", "113.264385,23.129112"): (8000, 1800),   # 广州南→广州
    ("116.407394,39.904211", "117.200983,39.084158"): (120000, 4200), # 北京→天津（短途）
}
# 公交路线：坐标对 -> AMap transit 响应
FAKE_TRANSIT = {
    ("116.407394,39.904211", "117.200983,39.084158"): {
        "status": "1",
        "route": {"transits": [{
            "cost": 6.0, "duration": 5400, "distance": 15000, "walking_distance": 800,
            "segments": [
                {"walking": {"distance": 500, "duration": 600}},
                {"bus": {"buslines": [{"name": "地铁1号线", "duration": 2400}]}},
            ],
        }]},
    }
}


def _raw_train(train_no, code, dep, arr, dur, seats):
    """构造 12306 leftTicket 原始 '|' 分隔行（≥35 字段，索引与真实解析器对应）。"""
    f = ["0"] * 40
    f[0] = "Y"; f[1] = "secret"; f[2] = train_no; f[3] = code
    f[4] = "BJP"; f[5] = "AOH"; f[6] = "VNP"; f[7] = "AOH"
    f[8] = dep; f[9] = arr; f[10] = dur; f[11] = "Y"
    f[12] = "无"; f[13] = "20260901"; f[14] = "0"; f[15] = "H5"
    f[16] = "01"; f[17] = "01"; f[18] = "1"; f[19] = "0"
    f[20] = "无"; f[21] = "无"; f[22] = "无"
    f[23] = seats.get("soft_sleeper", "无")    # 软卧
    f[24] = "无"; f[25] = "无"
    f[26] = seats.get("no_seat", "无")         # 无座
    f[27] = "无"
    f[28] = seats.get("hard_sleeper", "无")    # 硬卧
    f[29] = seats.get("hard_seat", "无")       # 硬座
    f[30] = seats.get("second_class", "无")    # 二等座（余票）
    f[31] = seats.get("first_class", "无")     # 一等座（余票）
    f[32] = seats.get("business_seat", "无")   # 商务座（余票）
    return "|".join(f)


# G1：二等座余票 18 张（与票价 553 区分，验证余票≠票价）
_RAW_G1 = _raw_train("240000G1010", "G1", "07:00", "11:29", "04:29",
                     {"business_seat": "18", "first_class": "有", "second_class": "18"})
# G3：全席无票（用于 only_available 过滤断言）
_RAW_G3 = _raw_train("240000G1030", "G3", "09:00", "13:28", "04:28", {})
_RAW_G5 = _raw_train("240000G1050", "G5", "10:00", "14:29", "04:29",
                     {"business_seat": "5", "first_class": "5", "second_class": "5"})

# 查询接口响应：{data: {result: [原始行...]}}；空 result 模拟"无班次"
_QUERY_PAYLOAD_OK = {"data": {"result": [_RAW_G1, _RAW_G3, _RAW_G5]}}
_QUERY_PAYLOAD_EMPTY = {"data": {"result": []}}

# 票价接口响应：座位编码 -> 价格（真实 _fetch_train_price / _extract_prices 解析）
_PRICE_PAYLOAD = {
    "status": True,
    "data": {"data": {
        "A1": {"seat_type_name": "商务座", "seat_price": "1748.0"},
        "A2": {"seat_type_name": "一等座", "seat_price": "933.0"},
        "A3": {"seat_type_name": "二等座", "seat_price": "553.0"},
    }},
}


class _FakeResp:
    def __init__(self, payload, status=200):
        self.payload = payload
        self.status = status

    async def json(self):
        return self.payload

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False


class _Fake12306Session:
    """按 URL 返回对应模拟响应：init→200；leftTicket 查询→当前班次；price→票价。"""

    def __init__(self):
        self.headers = {}

    def get(self, url, params=None):
        _State.http_calls += 1
        if "leftTicketPrice" in url:
            return _FakeResp(_PRICE_PAYLOAD)
        if url.endswith("/otn/leftTicket/init"):
            return _FakeResp(None, status=200)
        return _FakeResp(_State.query_payload)

    async def close(self):
        pass


class _State:
    """场景间共享状态：HTTP 计数 + 12306 查询响应切换。"""
    http_calls = 0
    query_payload = _QUERY_PAYLOAD_OK


async def _fake_init_session(self):
    self.session = _Fake12306Session()


async def _fake_fetch_station_codes(self):
    # 离线确定性：不依赖真实缓存文件/网络
    self.station_codes = {"北京南": "VNP", "上海虹桥": "AOH", "广州南": "IZQ",
                          "天津": "TJP", "北京": "BJP", "上海": "SHH", "广州": "GZQ"}


def _fake_load_station_codes(self):
    # 真实方法为同步（__init__ 里直接调用）；跳过缓存文件，注入模拟车站表
    self.station_codes = {"北京南": "VNP", "上海虹桥": "AOH", "广州南": "IZQ",
                          "天津": "TJP", "北京": "BJP", "上海": "SHH", "广州": "GZQ"}


async def _fake_geocode(self, address, city=None):
    if city:
        address = f"{city}{address}"
    return FAKE_GEOCODE.get(address)


async def _fake_regeocode(self, location):
    if "," not in location:
        return None
    try:
        lng, lat = location.split(",")
        float(lng.strip())
        float(lat.strip())
    except Exception:
        return None
    return FAKE_REGEO.get(location)


async def _fake_driving_route(self, origin, destination):
    dist_m, dur_s = FAKE_DRIVING.get((origin, destination), (10000, 1800))
    return {"status": "1", "route": {"paths": [{"distance": str(dist_m), "duration": str(dur_s)}]}}


async def _fake_transit_route(self, origin, destination, city, strategy="0"):
    return FAKE_TRANSIT.get((origin, destination), {"status": "0", "info": "未找到公交路线"})


async def _fake_search_poi(self, keywords, city=None, type_=None):
    return []


async def _fake_find_train_stations(self, location, radius=15000):
    return []


def _fake_web_search(query, max_results=None):
    if query.startswith("FAIL"):
        return json.dumps({"error": "Tavily 搜索失败: 模拟上游异常",
                           "error_code": "TAVILY_SEARCH_FAILED"}, ensure_ascii=False)
    return json.dumps({
        "answer": "这是模拟摘要：DeepSeek-V4 于 2026-04-24 发布，采用稀疏注意力架构。",
        "sources": [
            {"title": f"来源{i}", "url": f"https://example.com/{i}", "score": 0.9 - i / 10}
            for i in range(1, 4)
        ],
    }, ensure_ascii=False)


@pytest.fixture(autouse=True)
def _apply_mocks(monkeypatch):
    """只替换数据源层；每个用例前重置状态与缓存。"""
    monkeypatch.setattr(UnifiedAMapClient, "geocode", _fake_geocode)
    monkeypatch.setattr(UnifiedAMapClient, "regeocode", _fake_regeocode)
    monkeypatch.setattr(UnifiedAMapClient, "driving_route", _fake_driving_route)
    monkeypatch.setattr(UnifiedAMapClient, "transit_route", _fake_transit_route)
    monkeypatch.setattr(UnifiedAMapClient, "search_poi", _fake_search_poi)
    monkeypatch.setattr(UnifiedAMapClient, "find_train_stations", _fake_find_train_stations)
    # 12306：只替换会话初始化/车站表加载，query_train_schedule 等真实逻辑照跑
    monkeypatch.setattr(Py12306Client, "_init_session", _fake_init_session)
    monkeypatch.setattr(Py12306Client, "_fetch_station_codes", _fake_fetch_station_codes)
    monkeypatch.setattr(Py12306Client, "_load_station_codes", _fake_load_station_codes)
    monkeypatch.setattr(tools, "web_search", _fake_web_search)  # mcp_server_stdio 名字空间里的引用

    _State.http_calls = 0
    _State.query_payload = _QUERY_PAYLOAD_OK
    Py12306Client.SCHEDULE_CACHE.clear()
    Py12306Client.FAIL_CACHE.clear()
    yield
    # 收尾：关闭共享客户端（单例），避免跨用例残留会话
    async_wrappers.close_shared_clients()


def _payload(result):
    """提取工具返回的结构化数据（兼容 str 与 FastMCP ToolResult）。"""
    if isinstance(result, str):
        return json.loads(result)
    if hasattr(result, "structured_content"):
        return result.structured_content
    return result


# ==================== S1~S13：V1 行为回归 ====================

def test_s1_tavily_normal():
    r = _payload(asyncio.run(tools.tavily_web_search(query="DeepSeek 最新发布", max_results=3)))
    assert isinstance(r.get("answer"), str) and r["answer"], "answer 为摘要文本"
    assert len(r.get("sources", [])) == 3, f"sources 数量=3（实际 {len(r.get('sources', []))}）"
    assert all(s.get("url") for s in r["sources"]), "每条 source 含 url"


def test_s2_tavily_param_out_of_range():
    r = _payload(asyncio.run(tools.tavily_web_search(query="x", max_results=99)))
    assert r.get("error_code") == "INVALID_INPUT", f"error_code=INVALID_INPUT（实际 {r.get('error_code')}）"
    assert "1~10" in r.get("error", ""), "错误提示含取值范围"


def test_s3_tavily_upstream_fail():
    r = _payload(asyncio.run(tools.tavily_web_search(query="FAIL", max_results=3)))
    assert r.get("error_code") == "TAVILY_SEARCH_FAILED"


def test_s4_s5_geocode():
    r = _payload(asyncio.run(tools.amap_geocode(address="北京市朝阳区建国门外大街1号")))
    assert r.get("status") == "success", "status=success"
    assert r.get("location") == "116.459427,39.910358", f"坐标正确（实际 {r.get('location')}）"
    r = _payload(asyncio.run(tools.amap_geocode(address="不存在的地址XYZ")))
    assert r.get("error_code") == "GEOCODE_FAILED"
    assert r.get("hint"), "错误响应应带 hint（P2-V2 #7）"


def test_s6_s7_regeocode():
    r = _payload(asyncio.run(tools.amap_regeocode(location="116.397428,39.90923")))
    assert r.get("status") == "success", "status=success"
    assert "天安门" in r.get("formatted_address", ""), f"地址反解合理（{r.get('formatted_address')}）"
    r = _payload(asyncio.run(tools.amap_regeocode(location="abc")))
    assert r.get("error_code") == "REGEOCODE_FAILED"


def test_s8_route_long_train():
    """长距离 auto（北京→上海，>120km 应选火车）三段式 + 真实票价 + 费用口径。"""
    r = _payload(asyncio.run(tools.amap_route_planning(origin="北京", destination="上海", mode="auto")))
    ti = r.get("train_info") or {}
    assert r.get("transport_mode") == "Train Travel", f"自动选中火车（实际 {r.get('transport_mode')}）"
    assert r.get("segment_1", {}).get("distance", 0) > 0, "第1段距离>0=站名编码重试链生效"
    assert r.get("total_duration") == 324, f"总耗时=25+269+30=324 分钟（实际 {r.get('total_duration')}）"
    assert abs(r.get("total_cost", 0) - 593.0) < 0.01, f"总费用=553 真实票价+40 市内段=593（实际 {r.get('total_cost')}）"
    assert ti.get("price", {}).get("second_class") == 553.0, "train_info 含真实票价 553"
    assert ti.get("seats", {}).get("second_class") == "18", "seats 余票='18'（与票价 553 区分）"
    assert r.get("warning") is None, "无降级警告"
    # P1-V2 #5：市内段费用口径标注
    assert r.get("segment_cost_note") == "市内接驳（按驾车估算）"
    assert r["segment_1"].get("cost_note") == r["segment_cost_note"]
    assert r["segment_3"].get("cost_note") == r["segment_cost_note"]


def test_s9_route_short_driving():
    """短距离 auto（北京→天津，<120km）驾车 70min < 公交 90min → Driving。"""
    r = _payload(asyncio.run(tools.amap_route_planning(origin="北京", destination="天津", mode="auto")))
    assert r.get("transport_mode") == "Driving", f"短途择优（实际 {r.get('transport_mode')}）"
    assert r.get("total_duration") == 70, f"总耗时 70 分钟（实际 {r.get('total_duration')}）"
    assert abs(r.get("total_cost", 0) - 96.0) < 0.01, f"费用=120km×0.8=96（实际 {r.get('total_cost')}）"


def test_s10_route_degrade_placeholder():
    """火车信息不可用 → 占位降级；P1-V2 #3/#4：占位数据按距离动态生成。"""
    _State.query_payload = _QUERY_PAYLOAD_EMPTY
    r = _payload(asyncio.run(tools.amap_route_planning(origin="北京", destination="广州", mode="auto")))
    ti = r.get("train_info") or {}
    assert ti.get("placeholder") is True, "train_info.placeholder=True"
    assert "未找到符合条件的火车班次" in (r.get("warning") or ""), "warning 透传真实原因"
    # P1-V2 #3：不再显示固定 "4-6 hours"；北京→广州 1889km ≈ 7.6h
    planner = RoutePlanner(amap_key=None)
    train_min = planner._parse_train_duration(ti.get("duration", ""))
    assert ti.get("duration") != "4-6 hours", "占位时长不再固定"
    assert ti.get("estimated") is True, "占位数据标注估算值"
    assert 420 <= train_min <= 480, f"占位时长应≈7.6h（实际 {train_min} 分钟）"
    assert r.get("total_duration") == r["segment_1"]["duration"] + train_min + r["segment_3"]["duration"], \
        "总耗时=市内1+火车+市内3"
    # P1-V2 #4：票价估算 ≈850±10%（总费用 40 = 两段市内 max(20, km×0.8)）
    train_cost = r.get("total_cost", 0) - 40
    assert 765 <= train_cost <= 935, f"京广估算票价应≈850±10%（实际 {train_cost}）"
    assert r.get("segment_3", {}).get("distance", 0) > 0, "第3段仍正常计算"


def test_s11_schedule_ttl_cache():
    """班次查询成功 + TTL 缓存：第二次调用零 HTTP。"""
    _State.query_payload = _QUERY_PAYLOAD_OK
    r1 = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-01")))
    before = _State.http_calls
    r2 = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-01")))
    after = _State.http_calls
    first = (r1.get("trains") or [{}])[0]
    assert r1.get("success") is True, "success=True"
    assert r1.get("total_trains") == 3, f"total_trains=3（实际 {r1.get('total_trains')}）"
    assert len(r1.get("trains", [])) == 3, f"返回前 5 趟（实际 {len(r1.get('trains', []))}）"
    assert r1.get("total_matched") == 3, "P2-V2 #8：total_matched=全部"
    assert first.get("price", {}).get("second_class") == 553.0, "trains[0] 含真实票价 553"
    assert first.get("seats", {}).get("second_class") == "18", "seats 余票='18'（真实解析）"
    assert r2 == r1, "两次调用结果一致"
    assert after == before, f"TTL 缓存命中：第二次调用零 HTTP（实际 {after - before} 次）"


def test_s12_station_not_found_hint():
    """车站不存在 → STATION_NOT_FOUND + hint（P2-V2 #7）。"""
    r = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="火星", to_station="上海", date="2026-09-01")))
    assert r.get("error_code") == "STATION_NOT_FOUND", f"error_code=STATION_NOT_FOUND（实际 {r.get('error_code')}）"
    assert "火星" in r.get("error", ""), f"错误信息含真实原因（{r.get('error')}）"
    assert r.get("trains") == [], "trains 为空数组"
    assert "城市名" in r.get("hint", ""), "STATION_NOT_FOUND hint 引导使用城市名/标准站名"


def test_s13_invalid_date():
    r = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-13-99")))
    assert r.get("error_code") == "INVALID_INPUT"


# ==================== V2 新行为断言 ====================

def test_v2_no_retry_deterministic_error():
    """P0-V2 #1：无班次是确定性错误，重试 0 次（HTTP 恰好 init+query 各 1 次）。"""
    _State.query_payload = _QUERY_PAYLOAD_EMPTY
    _State.http_calls = 0
    r = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="上海", to_station="广州", date="2026-09-02")))
    assert r.get("error_code") == "NO_TRAINS"
    assert _State.http_calls == 2, f"确定性错误不应重试（实际 {_State.http_calls} 次 HTTP）"


def test_v2_negative_cache():
    """P2-V2 #9：失败结果 30s 负缓存，同参数立即返回不再打站。"""
    _State.query_payload = _QUERY_PAYLOAD_EMPTY
    _State.http_calls = 0
    first = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="上海", to_station="广州", date="2026-09-03")))
    assert first.get("error_code") == "NO_TRAINS"
    calls_after_first = _State.http_calls
    second = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="上海", to_station="广州", date="2026-09-03")))
    assert second.get("error_code") == "NO_TRAINS"
    assert _State.http_calls == calls_after_first, f"负缓存命中：第二次零 HTTP（实际 {_State.http_calls - calls_after_first} 次）"


def test_v2_schedule_filters():
    """P2-V2 #8：only_available / earliest_time / latest_time 过滤（G3 全席无票）。"""
    _State.query_payload = _QUERY_PAYLOAD_OK
    base = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04")))
    assert base.get("total_trains") == 3
    assert base.get("filter") == {"only_available": False, "earliest_time": None, "latest_time": None}

    only = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04", only_available=True)))
    assert only.get("total_matched") == 2, f"only_available 应排除全席无票的 G3（实际 {only.get('total_matched')}）"
    assert {t["train_number"] for t in only["trains"]} == {"G1", "G5"}

    early = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04", earliest_time="08:00")))
    assert {t["train_number"] for t in early["trains"]} == {"G3", "G5"}, "earliest_time=08:00 过滤早班"

    late = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04", latest_time="09:30")))
    assert {t["train_number"] for t in late["trains"]} == {"G1", "G3"}, "latest_time=09:30 过滤晚班"

    both = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04",
        earliest_time="08:00", latest_time="09:30")))
    assert {t["train_number"] for t in both["trains"]} == {"G3"}, "组合时段过滤"


def test_v2_filter_invalid_time():
    """P2-V2 #8：非法时段参数 → INVALID_INPUT（含 hint）。"""
    r = _payload(asyncio.run(tools.py12306_query_train_schedule(
        from_station="北京", to_station="上海", date="2026-09-04", earliest_time="25:99")))
    assert r.get("error_code") == "INVALID_INPUT"
    assert r.get("hint")
