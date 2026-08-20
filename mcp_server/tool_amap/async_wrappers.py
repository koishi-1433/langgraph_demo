"""高德地图 & 12306 异步工具封装 — 供 MCP stdio async tool 直接使用。

与 wrappers.py（同步兼容层，仅供独立脚本调试）返回格式保持一致，
但内部直接 await 异步实现，避免 asyncio.run 黑洞（每次调用新建事件循环）。

资源生命周期（P0-2 / P0-V2 #2）：
- 高德客户端在模块级共享单例，进程退出时统一关闭，避免每次调用泄漏 aiohttp 会话；
- 12306 会话进程内保活（RoutePlanner._get_train_schedule 不再每次关闭），
  同样由 close_shared_clients（atexit 注册）在进程退出时统一关闭。
"""
import asyncio
import atexit
import json
import re
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
from .train_crawler import Py12306Client

# ==================== 错误 hint（P2-V2 #7）：帮助 LLM 自我纠错 ====================
_ERROR_HINTS = {
    "GEOCODE_FAILED": "请检查地址拼写，或改用更完整的地址（如「北京市朝阳区建国门外大街1号」）",
    "REGEOCODE_FAILED": "坐标格式应为 lng,lat，如「116.397428,39.90923」",
    "AMAP_REQUEST_FAILED": "高德服务暂不可用，请稍后重试",
    "ROUTE_PLAN_FAILED": "请检查起终点名称后重试，如「北京」「上海」",
    "TRAIN_QUERY_FAILED": "12306 服务暂不可用，请稍后重试",
    "INVALID_INPUT": "请检查参数格式后重试",
    "STATION_NOT_FOUND": "请使用城市名（如「北京」）或标准站名（如「北京南站」）",
    "NO_TRAINS": "该线路暂无班次，可尝试调整出发日期或更换车站",
    "UPSTREAM_UNREACHABLE": "12306 暂时无法连接，请稍后重试",
    "UPSTREAM_ERROR": "12306 接口返回错误，请稍后重试或更换查询参数",
    "QUERY_FAILED": "查询失败，请稍后重试",
}

_TIME_RE = re.compile(r"^\d{2}:\d{2}$")


def _valid_time(value: str) -> bool:
    """HH:MM 且为真实时刻（拒绝 25:99 之类）。"""
    if not _TIME_RE.match(value):
        return False
    try:
        datetime.strptime(value, "%H:%M")
        return True
    except ValueError:
        return False

# ==================== 共享客户端（单例，防会话泄漏） ====================
_shared_amap: Optional[UnifiedAMapClient] = None
_shared_planner: Optional[RoutePlanner] = None


def _get_shared_amap() -> UnifiedAMapClient:
    global _shared_amap
    if _shared_amap is None:
        _shared_amap = UnifiedAMapClient(api_key=settings.AMAP_API_KEY)
    return _shared_amap


def _get_shared_planner() -> RoutePlanner:
    global _shared_planner
    if _shared_planner is None:
        _shared_planner = RoutePlanner(
            amap_key=settings.AMAP_API_KEY,
            enable_train_info=True,
            amap_client=_get_shared_amap(),
        )
    return _shared_planner


def close_shared_clients():
    """关闭共享客户端（进程退出 / 测试收尾时调用）。"""
    global _shared_amap, _shared_planner
    try:
        if _shared_amap is not None:
            asyncio.run(_shared_amap.close())
    except Exception:
        pass
    try:
        if _shared_planner is not None and _shared_planner.train_client is not None:
            asyncio.run(_shared_planner.train_client.close())
    except Exception:
        pass
    _shared_amap = None
    _shared_planner = None


atexit.register(close_shared_clients)


async def geocode(address: str) -> str:
    """将地址文本转换为经纬度坐标 lng,lat。"""
    amap = _get_shared_amap()
    try:
        location = await amap.geocode(address)
        if location:
            return json.dumps({"location": location, "status": "success"}, ensure_ascii=False)
        return json.dumps({"status": "failed", "error_code": "GEOCODE_FAILED",
                           "reason": "无法解析地址",
                           "hint": _ERROR_HINTS["GEOCODE_FAILED"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error_code": "AMAP_REQUEST_FAILED",
                           "error": str(e),
                           "hint": _ERROR_HINTS["AMAP_REQUEST_FAILED"]}, ensure_ascii=False)


async def regeocode(location: str) -> str:
    """将经纬度坐标转回地址信息（城市、区县等）。"""
    amap = _get_shared_amap()
    try:
        data = await amap.regeocode(location)
        if data:
            return json.dumps(data, ensure_ascii=False)
        return json.dumps({"status": "failed", "error_code": "REGEOCODE_FAILED",
                           "reason": "无法解析坐标",
                           "hint": _ERROR_HINTS["REGEOCODE_FAILED"]}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"status": "failed", "error_code": "AMAP_REQUEST_FAILED",
                           "error": str(e),
                           "hint": _ERROR_HINTS["AMAP_REQUEST_FAILED"]}, ensure_ascii=False)


async def route_planning(origin: str, destination: str, mode: str = "auto") -> str:
    """规划路线（驾车/公交/火车），mode=auto 时按距离自动选择最佳交通方式。"""
    planner = _get_shared_planner()
    try:
        result = await planner.plan_route(
            start=origin, end=destination, transport_mode=mode, user_real_location=None
        )
        d = result.to_dict()
        d["actual_mode"] = d.get("transport_mode", mode)
        return json.dumps(d, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error_code": "ROUTE_PLAN_FAILED",
                           "error": str(e),
                           "hint": _ERROR_HINTS["ROUTE_PLAN_FAILED"]}, ensure_ascii=False)


def _has_available_seat(train: dict) -> bool:
    """班次是否还有余票（任一席别不是 无/空）。seats 值为余票状态：'有' / 数量 / '无'。"""
    seats = train.get("seats")
    if not isinstance(seats, dict):
        return False
    return any(
        v not in (None, "", "无") for v in seats.values()
    )


async def query_train_schedule(from_station: str, to_station: str, date: Optional[str] = None,
                               only_available: bool = False,
                               earliest_time: Optional[str] = None,
                               latest_time: Optional[str] = None) -> str:
    """查询两个车站之间的火车班次信息（车次、时间、票价）。

    Args:
        from_station: 出发城市或车站名
        to_station: 到达城市或车站名
        date: 出发日期 YYYY-MM-DD，默认当天
        only_available: 仅返回有余票的班次（P2-V2 #8）
        earliest_time: 最早发车时间 HH:MM（含），过滤早于该时段的班次
        latest_time: 最晚发车时间 HH:MM（含），过滤晚于该时段的班次
    """
    if date is None:
        date = datetime.now().strftime("%Y-%m-%d")

    for label, value in (("earliest_time", earliest_time), ("latest_time", latest_time)):
        if value is not None and not _valid_time(value):
            return json.dumps(
                {"success": False, "error_code": "INVALID_INPUT",
                 "error": f"{label} 需为 HH:MM 格式（00:00~23:59），收到 {value!r}",
                 "hint": _ERROR_HINTS["INVALID_INPUT"]},
                ensure_ascii=False,
            )

    planner = _get_shared_planner()
    try:
        # 显示用车站名：城市 → 主要车站（单一来源 Py12306Client.CITY_STATION_MAP）
        station_map = Py12306Client.CITY_STATION_MAP
        station_from = station_map.get(from_station, from_station) if from_station else from_station
        station_to = station_map.get(to_station, to_station) if to_station else to_station
        schedule = await planner._get_train_schedule(date, station_from, station_to)
        if not schedule or not schedule.get("trains"):
            err = schedule.get("error") if isinstance(schedule, dict) else None
            err_code = schedule.get("error_code") if isinstance(schedule, dict) else None
            err_code = err_code or "NO_TRAINS"
            return json.dumps(
                {
                    "success": False,
                    "error": err or "未查询到班次",
                    "error_code": err_code,
                    "hint": _ERROR_HINTS.get(err_code, _ERROR_HINTS["QUERY_FAILED"]),
                    "trains": [],
                    "total_trains": 0,
                },
                ensure_ascii=False,
            )

        all_trains = schedule.get("trains", [])
        filtered = all_trains
        if earliest_time:
            filtered = [t for t in filtered if (t.get("departure_time") or "") >= earliest_time]
        if latest_time:
            filtered = [t for t in filtered if (t.get("departure_time") or "") <= latest_time]
        if only_available:
            filtered = [t for t in filtered if _has_available_seat(t)]

        result = {
            "success": True,
            "trains": filtered[:5],
            "total_trains": len(all_trains),
            "total_matched": len(filtered),
            "from_station": station_from,
            "to_station": station_to,
            "filter": {
                "only_available": only_available,
                "earliest_time": earliest_time,
                "latest_time": latest_time,
            },
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"success": False, "error_code": "TRAIN_QUERY_FAILED",
                           "error": str(e),
                           "hint": _ERROR_HINTS["TRAIN_QUERY_FAILED"]}, ensure_ascii=False)
