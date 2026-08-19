"""高德地图 & 12306 异步工具封装 — 供 MCP stdio async tool 直接使用。

与 wrappers.py（同步兼容层，仅供独立脚本调试）返回格式保持一致，
但内部直接 await 异步实现，避免 asyncio.run 黑洞（每次调用新建事件循环）。
"""
import json
from datetime import datetime
from typing import Optional

try:
    from config.settings import settings
except ImportError:
    import os

    class _FallbackSettings:
        AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

    settings = _FallbackSettings()

from .client import UnifiedAMapClient
from .route_planner import RoutePlanner

# 城市 → 主要火车站映射（与 wrappers.py 保持一致）
_CITY_STATION_MAP = {
    "北京": "北京南",
    "上海": "上海虹桥",
    "天津": "天津",
    "重庆": "重庆北",
    "广州": "广州南",
    "深圳": "深圳北",
    "杭州": "杭州东",
    "南京": "南京南",
    "武汉": "武汉",
    "成都": "成都东",
    "西安": "西安北",
    "济南": "济南西",
    "长沙": "长沙南",
    "郑州": "郑州东",
}


async def geocode(address: str) -> str:
    """将地址文本转换为经纬度坐标 lng,lat。"""
    amap = UnifiedAMapClient(api_key=settings.AMAP_API_KEY)
    try:
        location = await amap.geocode(address)
        if location:
            return json.dumps({"location": location, "status": "success"}, ensure_ascii=False)
        return json.dumps({"status": "failed", "reason": "无法解析地址"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


async def regeocode(location: str) -> str:
    """将经纬度坐标转回地址信息（城市、区县等）。"""
    amap = UnifiedAMapClient(api_key=settings.AMAP_API_KEY)
    try:
        data = await amap.regeocode(location)
        if data:
            return json.dumps(data, ensure_ascii=False)
        return json.dumps({"status": "failed", "reason": "无法解析坐标"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


async def route_planning(origin: str, destination: str, mode: str = "auto") -> str:
    """规划路线（驾车/公交/火车），mode=auto 时自动选择最佳交通方式。"""
    planner = RoutePlanner(
        amap_key=settings.AMAP_API_KEY,
        enable_train_info=True,
        enable_static_map=True,
    )
    try:
        result = await planner.plan_route(
            start=origin, end=destination, transport_mode=mode, user_real_location=None
        )
        d = result.to_dict()
        d["actual_mode"] = d.get("transport_mode", mode)
        return json.dumps(d, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


async def query_train_schedule(from_station: str, to_station: str, date: Optional[str] = None) -> str:
    """查询两个车站之间的火车班次信息（车次、时间、票价）。"""
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    planner = RoutePlanner(
        amap_key=settings.AMAP_API_KEY,
        enable_train_info=True,
        enable_static_map=True,
    )
    try:
        station_from = _CITY_STATION_MAP.get(from_station, from_station) if from_station else from_station
        station_to = _CITY_STATION_MAP.get(to_station, to_station) if to_station else to_station
        schedule = await planner._get_train_schedule(date, station_from, station_to)
        if not schedule or not schedule.get("trains"):
            return json.dumps(
                {"success": False, "error": "未查询到班次", "trains": [], "total_trains": 0},
                ensure_ascii=False,
            )
        result = {
            "success": True,
            "trains": schedule.get("trains", [])[:5],
            "total_trains": len(schedule.get("trains", [])),
            "from_station": station_from,
            "to_station": station_to,
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
