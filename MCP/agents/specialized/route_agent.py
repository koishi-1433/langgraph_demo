"""
RouteAgent - 通过统一 MCPAdapter 调用路线规划工具
只负责路线规划，所有高德调用都走同一个 MCP 连接（不再自建 SSE 会话）
"""
import json
from typing import Dict, Any
from pydantic import BaseModel
from datetime import datetime, timedelta

from MCP.core.logger import get_logger

logger = get_logger(__name__)


class RouteAgentResponse(BaseModel):
    content: Dict[str, Any]
    success: bool
    error: str = ""


class RouteAgent:
    """
    路线规划智能体 - 100% 经统一 MCPAdapter 调用
    不直接调用任何高德 API，也不自建 SSE 会话；连接生命周期由 MCPAdapter 管理（P0-3/P0-4）。
    """

    def __init__(self, mcp_adapter=None):
        if mcp_adapter is None:
            from MCP.integration.tool_adapter import get_mcp_adapter
            mcp_adapter = get_mcp_adapter()
        self.mcp_adapter = mcp_adapter
        logger.info("✅ 初始化 RouteAgent（统一 MCPAdapter 模式）")

    async def _call_mcp_tool(self, tool_name: str, arguments: dict) -> dict:
        """统一调用 MCP 工具（经 MCPAdapter，复用同一连接与传输模式）"""
        try:
            logger.info(f"📡 MCP 调用: {tool_name}({arguments})")

            result = await self.mcp_adapter.call_tool(tool_name, arguments)

            # 解析返回内容
            if getattr(result, 'content', None):
                text_content = result.content[0].text
                try:
                    return json.loads(text_content)
                except json.JSONDecodeError:
                    return {"success": False, "error": f"无法解析 MCP 返回: {text_content[:200]}"}

            return {"success": False, "error": "MCP 工具返回空结果"}

        except Exception as e:
            logger.error(f"❌ MCP 工具调用失败 [{tool_name}]: {e}")
            raise

    async def plan_route(
        self,
        start: str,
        end: str,
        mode: str = "auto",
        lat: float = None,
        lng: float = None,
        full_address: dict = None
    ) -> RouteAgentResponse:
        """
        规划路线 - 通过 MCP 调用 route_planning 工具
        mode: auto/driving/transit/train，auto 由 MCP Server 智能选择
        """
        def clean_loc(s: str) -> str:
            if not s:
                return ""
            s = s.strip()
            for prefix in ["从", "到", "去", "往"]:
                if s.startswith(prefix):
                    s = s[len(prefix):].strip()
            return s

        start = clean_loc(start) if start else ""
        end = clean_loc(end)

        logger.info(f"RouteAgent 输入: start={start or '当前定位'}, end={end}, mode={mode}")

        # 处理 GPS 坐标 → 传给 MCP 作为 origin
        origin = start
        if not origin and lat and lng:
            origin = f"{lng},{lat}"  # 高德格式: lng,lat
            logger.info(f"📍 使用 GPS 坐标作为起点: {origin}")

        if not origin:
            origin = "当前定位"

        try:
            # ✅ 核心：通过 MCP 调用 route_planning 工具
            mcp_result = await self._call_mcp_tool(
                "route_planning",
                {
                    "origin": origin,
                    "destination": end,
                    "mode": mode  # auto/driving/transit/train
                }
            )

            if mcp_result.get("success") or mcp_result.get("route_info"):
                route_info = mcp_result.get("route_info", {})
                route_dict = route_info if isinstance(route_info, dict) else route_info.__dict__ if hasattr(route_info, '__dict__') else {}

                # 获取实际使用的交通模式（auto 时 Server 会返回 actual_mode）
                actual_mode = mcp_result.get("actual_mode", route_dict.get("transport_mode", mode))
                logger.info(f"✅ MCP 返回实际模式: {actual_mode}")

                # 组装时序分段
                segments = self._build_segments(route_dict, origin, end, actual_mode)

                now = datetime.now()
                current_time = now.strftime("%Y-%m-%d %H:%M:%S")
                default_date = now.strftime("%Y-%m-%d") if now.hour >= 6 else (now + timedelta(days=1)).strftime("%Y-%m-%d")

                return RouteAgentResponse(
                    content={
                        "route_result": route_dict,
                        "segments": segments,
                        "origin": origin,
                        "destination": end,
                        "mode": mode,
                        "actual_mode": actual_mode,
                        "current_time": current_time,
                        "departure_date": default_date,
                        "mcp_called": True,
                        "data_quality": mcp_result.get("data_quality", "unknown"),
                        "is_realtime": mcp_result.get("is_realtime", False),
                        "warnings": mcp_result.get("warnings", []),
                        "raw_mcp_result": mcp_result,
                    },
                    success=True,
                    error=""
                )
            else:
                error_msg = mcp_result.get("error", "MCP 路线规划返回异常")
                logger.warning(f"MCP 路线规划未成功: {error_msg}")
                return RouteAgentResponse(
                    content={"raw_mcp_result": mcp_result},
                    success=False,
                    error=error_msg
                )

        except Exception as e:
            logger.error(f"路线规划异常: {str(e)}")
            return RouteAgentResponse(
                content={},
                success=False,
                error=f"路线规划失败：{str(e)}"
            )

    def _build_segments(self, route_dict: dict, origin: str, destination: str, actual_mode: str = "") -> list:
        """将路线结果组装为时序分段"""
        segments = []
        mode = actual_mode or route_dict.get("transport_mode", "")
        details = route_dict.get("route_details", [])
        train_info = route_dict.get("train_info")

        if mode == "Train Travel" or train_info:
            # 火车模式三段式
            station_from = train_info.get("departure_station", "出发站") if train_info else "出发站"
            station_to = train_info.get("arrival_station", "到达站") if train_info else "到达站"

            segments.append({
                "step": 1,
                "icon": "🚶",
                "mode": "市内交通",
                "from": origin,
                "to": station_from,
                "detail": f"从 {origin} 前往 {station_from}"
            })

            train_detail = ""
            if train_info:
                train_detail = (
                    f"车次: {train_info.get('train_number', '待查询')}\n"
                    f"时间: {train_info.get('departure_time', '--:--')} → {train_info.get('arrival_time', '--:--')}\n"
                    f"历时: {train_info.get('duration', '--')}\n"
                    f"二等座: {train_info.get('second_class', '--')}"
                )
            else:
                # 无实时车次时不编造，明确告知用户（P1-11）
                train_detail = (
                    "车次 / 发车时间 / 票价：暂不可用（12306 实时查询失败）\n"
                    "请在铁路 12306 官方查询；本段时长为按距离估算，非实时数据"
                )

            segments.append({
                "step": 2,
                "icon": "🚄",
                "mode": "高铁",
                "from": station_from,
                "to": station_to,
                "detail": train_detail or "\n".join(details) if isinstance(details, list) else str(details)
            })

            segments.append({
                "step": 3,
                "icon": "🚶",
                "mode": "市内交通",
                "from": station_to,
                "to": destination,
                "detail": f"从 {station_to} 前往 {destination}（推荐地铁/打车）"
            })
        else:
            # 驾车/公交模式 - 单段
            segments.append({
                "step": 1,
                "icon": "🚗" if mode == "Driving" else "🚌",
                "mode": mode or "交通",
                "from": origin,
                "to": destination,
                "detail": "\n".join(details) if isinstance(details, list) else str(details)
            })

        return segments

    async def get_train_schedule(self, from_station: str, to_station: str, date: str = None) -> RouteAgentResponse:
        """查询火车班次 - 通过 MCP 调用"""
        logger.info(f"火车班次查询: {from_station} -> {to_station}")
        try:
            mcp_result = await self._call_mcp_tool(
                "route_planning",
                {
                    "origin": from_station,
                    "destination": to_station,
                    "mode": "train"
                }
            )

            route_info = mcp_result.get("route_info", {})
            train_info = route_info.get("train_info") if isinstance(route_info, dict) else None

            return RouteAgentResponse(
                content={
                    "train_result": train_info,
                    "from_station": from_station,
                    "to_station": to_station,
                    "mcp_called": True,
                    "raw_result": mcp_result
                },
                success=mcp_result.get("success", False)
            )
        except Exception as e:
            logger.error(f"火车查询失败: {str(e)}")
            return RouteAgentResponse(
                content={},
                success=False,
                error=str(e)
            )

    async def close(self):
        """
        兼容旧调用点（graph.py / TravelAgent）保留空实现。

        连接由 MCPAdapter 的 async with 统一建立与释放，Agent 不再手动
        __aenter__/__aexit__ SSE 会话，避免 anyio cancel scope 跨 task 异常（P0-3）。
        """
        return None