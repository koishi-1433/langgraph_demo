"""高德地图 & 12306 工具封装（同步兼容层）。

注意：本模块是「同步兼容层」，仅供独立脚本调试 / 非异步环境调用；
MCP stdio 服务端（mcp_server_stdio.py）实际使用的是 async_wrappers.py
（直接 await 异步实现，避免 asyncio.run 每次新建事件循环）。

- 每个函数内部通过 asyncio.new_event_loop() 跑一次异步流程，返回 JSON 字符串；
- 会话保活（P0-V2 #2）后，独立脚本创建的客户端需在 finally 中显式关闭，
  避免 aiohttp 会话泄漏。
"""
import asyncio
import json
from datetime import datetime
from typing import Optional, Dict, Any

try:
    from config.settings import settings
except ImportError:
    import os

    class _FallbackSettings:
        AMAP_API_KEY = os.getenv("AMAP_API_KEY", "")

    settings = _FallbackSettings()

from .client import UnifiedAMapClient
from .route_planner import RoutePlanner
from .train_crawler import Py12306Client


def geocode(address: str) -> str:
    """将地址文本转换为经纬度坐标 lng,lat。

    Args:
        address: 地址文本，如"北京市东城区王府井大街"

    Returns:
        JSON: {"location": "经度,纬度", "status": "success"/"failed"}
    """
    amap = UnifiedAMapClient(api_key=settings.AMAP_API_KEY)
    try:
        location = asyncio.run(amap.geocode(address))
        if location:
            return json.dumps({"location": location, "status": "success"}, ensure_ascii=False)
        return json.dumps({"status": "failed", "reason": "无法解析地址"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


def regeocode(location: str) -> str:
    """将经纬度坐标转回地址信息。

    Args:
        location: 经纬度坐标，格式为"lng,lat"

    Returns:
        JSON: {"city": "...", "district": "...", "formatted_address": "...", ...}
    """
    amap = UnifiedAMapClient(api_key=settings.AMAP_API_KEY)
    try:
        data = asyncio.run(amap.regeocode(location))
        if data:
            return json.dumps(data, ensure_ascii=False)
        return json.dumps({"status": "failed", "reason": "无法解析坐标"}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error": str(e)}, ensure_ascii=False)


def route_planning(origin: str, destination: str, mode: str = "auto") -> str:
    """规划路线。支持驾车、公交、火车（自动判断）。

    Args:
        origin: 起点城市名或"当前定位"
        destination: 终点城市名
        mode: auto / driving / transit / train

    Returns:
        JSON: {route_info, segments, actual_mode, ...}
    """
    planner = RoutePlanner(
        amap_key=settings.AMAP_API_KEY,
        enable_train_info=True,
    )
    loop = None
    try:

        async def _do():
            result = await planner.plan_route(
                start=origin, end=destination, transport_mode=mode, user_real_location=None
            )
            d = result.to_dict()
            d["actual_mode"] = d.get("transport_mode", mode)
            return json.dumps(d, ensure_ascii=False)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_do())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "ROUTE_PLAN_FAILED",
                           "error": str(e)}, ensure_ascii=False)
    finally:
        if loop:
            loop.close()
        # 独立脚本创建的客户端需显式关闭，避免 aiohttp 会话泄漏
        try:
            if planner and planner.train_client:
                asyncio.run(planner.train_client.close())
            if planner and planner.amap:
                asyncio.run(planner.amap.close())
        except Exception:
            pass


def query_train_schedule(from_station: str, to_station: str, date: Optional[str] = None) -> str:
    """查询两个车站之间的火车班次信息。

    Args:
        from_station: 出发站城市名（会自动匹配车站）
        to_station: 到达站城市名
        date: 日期 YYYY-MM-DD，默认当天

    Returns:
        JSON: {"trains": [...], "total_trains": int, ...}
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    planner = RoutePlanner(
        amap_key=settings.AMAP_API_KEY,
        enable_train_info=True,
    )
    loop = None
    try:

        async def _do():
            # 显示用车站名：城市 → 主要车站（单一来源 Py12306Client.CITY_STATION_MAP）
            station_map = Py12306Client.CITY_STATION_MAP
            station_from = station_map.get(from_station, from_station) if from_station else from_station
            station_to = station_map.get(to_station, to_station) if to_station else to_station
            schedule = await planner._get_train_schedule(date, station_from, station_to)
            if not schedule or not schedule.get("trains"):
                err = schedule.get("error") if isinstance(schedule, dict) else None
                err_code = schedule.get("error_code") if isinstance(schedule, dict) else None
                return json.dumps(
                    {
                        "success": False,
                        "error": err or "未查询到班次",
                        "error_code": err_code or "NO_TRAINS",
                        "trains": [],
                        "total_trains": 0,
                    },
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

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop.run_until_complete(_do())
    except Exception as e:
        return json.dumps({"success": False, "error_code": "TRAIN_QUERY_FAILED",
                           "error": str(e)}, ensure_ascii=False)
    finally:
        if loop:
            loop.close()
        # 独立脚本创建的客户端需显式关闭，避免 aiohttp 会话泄漏
        try:
            if planner and planner.train_client:
                asyncio.run(planner.train_client.close())
            if planner and planner.amap:
                asyncio.run(planner.amap.close())
        except Exception:
            pass
