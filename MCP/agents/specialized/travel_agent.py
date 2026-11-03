"""
TravelAgent v3.0 - 架构清晰版
- Search Agent: 直接调用 Tavily（不走 MCP）
- Route Agent: 经统一 MCPAdapter 调用（与全项目共用同一连接 / 同一传输模式）
- TravelAgent: 协调两者，整合输出
"""
import json
import re
from typing import Dict, Any
from datetime import datetime
from pydantic import BaseModel
from MCP.core.logger import get_logger
from MCP.core.llm_client import LLMClientFactory
from MCP.agents.specialized.search_agent import Search
from MCP.agents.specialized.route_agent import RouteAgent

logger = get_logger(__name__)


class TravelResponse(BaseModel):
    success: bool
    content: Dict[str, Any] = None
    error: str = ""


class TravelAgent:
    def __init__(self, mcp_adapter=None):
        logger.info("✅ 初始化 TravelAgent v3.0")
        logger.info("   SearchAgent: 直接调用 Tavily")
        logger.info("   RouteAgent: 统一 MCPAdapter 模式")

        # Search Agent: 直接调用，不走 MCP
        self.search_agent = Search()  # 不传 mcp_tools/mcp_adapter，内部直接调 Tavily

        # Route Agent: 与 RouteAgent / 主入口共用同一个 MCPAdapter（P0-4）
        if mcp_adapter is None:
            from MCP.integration.tool_adapter import get_mcp_adapter
            mcp_adapter = get_mcp_adapter()
        self.mcp_adapter = mcp_adapter
        self.route_agent = RouteAgent(mcp_adapter=mcp_adapter)

        self.llm = LLMClientFactory.create_chatter()

    # ============================
    # 起点/终点解析
    # ============================
    async def _parse_start_end(self, user_input: str, gps_coords: str = None):
        """
        三层兜底解析：LLM → split → GPS坐标逆地理编码
        返回 (origin, destination)
        """
        user_input = user_input.strip()

        # 第1层：LLM 智能解析
        try:
            prompt = f"""你是一个精确的地址解析器。从输入中提取出发地(origin)和目的地(destination)，只输出 JSON。

规则：
- "A到B" → origin="A", destination="B"
- "从A到B怎么走" → origin="A", destination="B"
- "A怎么去B" → origin="A", destination="B"
- 只有一个地点名 → origin="", destination="该地点"
- 不含地点 → origin="", destination=""

示例：
"北京到上海" -> {{"origin":"北京","destination":"上海"}}
"从南京去杭州怎么坐车" -> {{"origin":"南京","destination":"杭州"}}
"西安有什么好玩的" -> {{"origin":"","destination":"西安"}}
"你好" -> {{"origin":"","destination":""}}

输入：{user_input}
输出："""
            res = await self.llm.ainvoke(prompt)
            cleaned = res.strip()
            if cleaned.startswith("```"):
                cleaned = re.sub(r'```\w*\n?', '', cleaned).strip()
            data = json.loads(cleaned)
            origin = data.get("origin", "").strip()
            destination = data.get("destination", "").strip()
            if origin and destination:
                logger.info(f"✅ LLM解析成功: {origin} → {destination}")
                return origin, destination
        except Exception as e:
            logger.debug(f"LLM解析失败，降级到split: {e}")

        # 第2层：按"到"拆分
        if "到" in user_input:
            parts = user_input.split("到")
            origin = parts[0].strip()
            destination = parts[1].strip()
            if origin and destination:
                logger.info(f"✅ Split解析成功: {origin} → {destination}")
                return origin, destination

        # 第3层：没有起点 → 用GPS坐标逆地理编码
        destination = user_input
        origin = ""
        if gps_coords:
            try:
                from MCP.tool_amap.client import UnifiedAMapClient
                tool = UnifiedAMapClient()
                parts = gps_coords.split(",")
                if len(parts) == 2:
                    regeo_coords = f"{parts[1]},{parts[0]}"
                    loc = await tool.regeocode(regeo_coords)
                    if loc and loc.get("status") == "success":
                        city = loc.get("city", "")
                        district = loc.get("district", "")
                        origin = f"{city}{district}" if district else city
                        logger.info(f"✅ GPS坐标逆地理编码: {gps_coords} → {origin}")
            except Exception as e:
                logger.warning(f"GPS逆地理编码失败: {e}")

        return origin, destination

    # ============================
    # 格式清洗
    # ============================
    async def _format_final_output(self, raw_output: str) -> str:
        """清理 LLM 输出中的代码块标记和无关内容"""
        if not raw_output:
            return ""

        cleaned = re.sub(r'```\w*\n?', '', raw_output)
        cleaned = cleaned.replace('```', '')

        patterns = [
            r'^(好的[，,。]|好的吧[，,。]|没问题[，,。]|根据|基于|以下是|为您).*?[：:\n]',
            r'^(当然可以|当然|没问题|好嘞|好的)[！!。.]?\s*',
        ]
        for pat in patterns:
            cleaned = re.sub(pat, '', cleaned, count=1)

        cleaned = re.sub(r'\n{3,}', '\n\n', cleaned)
        cleaned = cleaned.strip()

        return cleaned

    # ============================
    # 时间信息
    # ============================
    def _get_current_time_info(self) -> dict:
        now = datetime.now()
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        return {
            "datetime": now.strftime("%Y-%m-%d %H:%M"),
            "date": now.strftime("%Y-%m-%d"),
            "time": now.strftime("%H:%M"),
            "weekday": weekdays[now.weekday()],
            "hour": now.hour,
            "is_morning": 6 <= now.hour < 12,
            "is_afternoon": 12 <= now.hour < 18,
            "is_evening": now.hour >= 18 or now.hour < 6,
        }

    # ============================
    # 主入口：综合旅游规划
    # ============================
    async def comprehensive_travel(self, user_input: str, gps_coords: str = None) -> TravelResponse:
        try:
            logger.info("===== TravelAgent 开始综合旅游规划 =====")

            # ---- 0. 获取当前时间 ----
            time_info = self._get_current_time_info()
            logger.info(f"⏱ 当前时间: {time_info['datetime']} {time_info['weekday']}")

            # ---- 1. 解析起点和终点 ----
            origin, destination = await self._parse_start_end(user_input, gps_coords=gps_coords)
            if not destination:
                return TravelResponse(success=False, error="无法识别目的地，请重新输入")

            # ---- 2. 调用 Search Agent（直接调用 Tavily，不走 MCP）----
            logger.info(f"🔍 SearchAgent 搜索攻略: {destination}")
            search_res = await self.search_agent.search(destination)
            # 降级响应 success=False 但 content 里有「非实时数据」提示，
            # 仍需把它交给 LLM，避免下游误以为拿到了真实攻略（P1-10）
            article = search_res.content or "暂无攻略信息"
            logger.info(f"✅ 攻略获取完成，长度: {len(article)} 字符")

            # ---- 3. 调用 Route Agent（统一 MCPAdapter）----
            logger.info(f"🚗 RouteAgent 规划路线: {origin or '当前定位'} → {destination}")
            route_result = await self.route_agent.plan_route(
                start=origin,
                end=destination,
                mode="auto"
            )

            route_str = ""
            route_success = False
            # 数据质量字段（DEF-04）：route_agent 已把 MCP 层标注透传进 content，
            # 这里必须接住，否则整合提示词会把估算数据说成「实时精确」。
            data_quality = "unknown"
            is_realtime = False
            warnings: list = []

            if route_result.success and route_result.content:
                route_success = True
                res_content = route_result.content
                segments = res_content.get("segments", [])
                route_dict = res_content.get("route_result", {})
                data_quality = res_content.get("data_quality", "unknown")
                is_realtime = bool(res_content.get("is_realtime", False))
                warnings = list(res_content.get("warnings") or [])

                # 组装分段路线文本 - 适配 Py12306Client 三段式输出
                route_lines = [f"## 🚗 交通路线: {origin or '当前定位'} → {destination}\n"]
                route_lines.append(f"⏱ 当前时间: {time_info['datetime']} {time_info['weekday']}\n")

                for seg in segments:
                    route_lines.append(f"### 第{seg['step']}段: {seg['icon']} {seg['mode']}")
                    route_lines.append(f"  📍 {seg['from']} → {seg['to']}")
                    # ✅ 显示距离和时长（如果有）
                    if seg.get('distance'):
                        route_lines.append(f"  📏 距离: {seg['distance']:.1f} km")
                    if seg.get('duration'):
                        from MCP.tool_amap.utils import format_duration
                        route_lines.append(f"  ⏱ 用时: {format_duration(seg['duration'])}")
                    route_lines.append(f"  {seg['detail']}")
                    route_lines.append("")

                # 总耗时/费用统计
                total_duration = route_dict.get("total_duration")
                total_cost = route_dict.get("total_cost")
                total_distance = route_dict.get("total_distance")

                if any([total_duration, total_cost, total_distance]):
                    route_lines.append("---")
                    if total_distance:
                        route_lines.append(f"📏 总距离: {total_distance:.1f} 公里")
                    if total_duration:
                        from MCP.tool_amap.utils import format_duration
                        label = "⏱ 总耗时" if is_realtime else "⏱ 总耗时（含估算，非实时）"
                        route_lines.append(f"{label}: {format_duration(total_duration)}")
                    if total_cost:
                        from MCP.tool_amap.utils import format_cost
                        route_lines.append(f"💰 预估总费用（按距离估算，非实时票价）: {format_cost(total_cost)}")

                route_str = "\n".join(route_lines)
                logger.info("✅ 路线规划完成")
            else:
                error_detail = route_result.error or "路线规划服务暂不可用"
                logger.warning(f"⚠️ 路线规划失败: {error_detail}")
                data_quality = "failed"
                is_realtime = False
                warnings = ["路线规划服务不可用，以下为静态参考建议（非实时）"]
                route_str = (
                    f"## 🚗 交通路线: {origin or '当前定位'} → {destination}\n\n"
                    f"⏱ 当前时间: {time_info['datetime']} {time_info['weekday']}\n\n"
                    f"⚠️ 实时路线规划服务暂不可用（{error_detail}）\n\n"
                    f"💡 **参考出行建议**（非实时数据，请以实际信息为准）：\n"
                    f"- 🚄 **高铁**：约4.5小时，二等座约¥550\n"
                    f"- ✈️ **飞机**：约2小时，经济舱约¥600-1200\n"
                    f"- 🚗 **自驾**：约1200公里，走高速，约12-13小时\n\n"
                    f"📌 建议在「铁路12306」APP 或航空公司官网查询实时班次和票价。"
                )

            # ---- 4. 整合最终回答 ----
            quality_label = "实时数据" if (is_realtime and data_quality == "real") else "含估算成分、非实时数据"
            quality_block = (
                "【数据质量声明】\n"
                f"- 数据质量等级：{data_quality}；是否实时：{'是' if is_realtime else '否'}\n"
                + "".join(f"- ⚠️ {w}\n" for w in warnings)
                + "- 含估算数据时必须明确告知用户：本次行程含估算信息，请勿据此购票，请以铁路12306或航司官网为准。"
            )
            final_prompt = f"""你是一位专业的旅行助手，请整合以下【旅游攻略】和【交通路线】，生成一份完整、自然、流畅的旅行方案。

当前时间：{time_info['datetime']} {time_info['weekday']}

要求：
1. 按**出行时序**组织内容：先交通路线（含车次/时间/票价），再景点游玩攻略
2. 如果有火车数据，请嵌入车次编号、发车/到达时间、票价信息
3. 输出纯文本格式，不要用 ```json 或 ```markdown 包裹
4. 语言风格：亲切、实用、像朋友推荐
5. 不得把标注为估算的数据描述成确定信息，也不得删改下方的数据质量声明

【交通路线】
{route_str}

【景点攻略】
{article}

【数据质量声明】（必须原样保留在回答末尾，不得删改或弱化）
{quality_block}

请输出最终旅行总攻略。

## 格式约束
- 交通部分放前面，攻略部分放后面
- 交通数据来源：MCP Server（{quality_label}）
- 如果路线数据标注了"仅供参考"或数据质量非 real，请明确告知用户这是估算
- 攻略数据来自 Tavily 搜索
- 输出控制在 1500 字以内
- 不要使用 ```json 或 ``` 代码块包裹"""

            final_answer = await self.llm.ainvoke(final_prompt)
            final_answer = await self._format_final_output(final_answer)

            # 免责声明由代码强制追加，不依赖 LLM 自觉（DEF-04）
            if not is_realtime or data_quality != "real":
                final_answer += (
                    "\n\n⚠️ 本次方案含估算数据（票价/耗时可能非实时），"
                    "请勿据此购票，请以铁路12306或航司官网为准。"
                )

            return TravelResponse(
                success=True,
                content={
                    "search_data": article,
                    "route_data": route_str,
                    "travel_plan": final_answer,
                    "time_info": time_info,
                    "route_success": route_success,
                    "data_quality": data_quality,
                    "is_realtime": is_realtime,
                    "warnings": warnings,
                }
            )

        except Exception as e:
            logger.error(f"TravelAgent 综合规划失败：{str(e)}")
            return TravelResponse(success=False, error=str(e))

    async def close(self):
        """关闭资源"""
        await self.route_agent.close()
        logger.info("🔌 TravelAgent 资源已释放")