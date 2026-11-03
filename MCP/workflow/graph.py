"""
workflow - 多智能体（统一 MCPAdapter 版）
Search: 直接调用 Tavily（不走 MCP）
Route / Travel / RAG: 经同一个 MCPAdapter 调用 MCP 工具，连接由 workflow 生命周期管理
RAG 已接入 MCP/tool_rag：本地 bge-m3 嵌入 + Chroma 持久化知识库，
搜索攻略先入库，再用用户原始问题检索并把片段追加到最终输出。
"""
import re
import asyncio
import json
from typing import Literal

from langgraph.graph import StateGraph, END
from contextlib import AsyncExitStack

from MCP.agents.specialized.intent_recognizer import IntentRecognition
from MCP.agents.specialized.search_agent import Search
from MCP.agents.specialized.route_agent import RouteAgent
from MCP.agents.specialized.travel_agent import TravelAgent
from MCP.agents.specialized.rag_agent import RAGAgent

from MCP.core.llm_client import LLMClientFactory
from MCP.core.logger import get_logger
from MCP.integration.tool_adapter import get_mcp_adapter
from MCP.workflow.state import WorkflowState

logger = get_logger(__name__)


# ==================== 地名合法性校验（P1-13）====================
# 旧实现用 ["店","铺","园","苑","广场","中式"] 等单字做「包含即清空」，
# 会把「颐和园」「人民广场」「驻马店」等真实地名误杀。这里改为只排除
# 「查询语句」与「明确的商业 POI 后缀」，其余一律放行交给地理编码判断。
_QUERY_WORDS = (
    "好玩", "景点", "美食", "推荐", "攻略", "怎么", "多少", "天气",
    "多少钱", "几天", "安排", "路线", "哪里", "哪些", "附近",
)
_POI_SUFFIXES = (
    "体验店", "生活馆", "便利店", "旗舰店", "专卖店", "连锁店",
    "小区", "大厦", "商场", "写字楼", "超市", "咖啡厅", "餐厅", "饭店",
)


def _looks_like_place(name: str) -> bool:
    """判断抽取结果是否像一个可用于地理编码的地名。

    原则：宁可放行让地理编码去判断，也不要因为一个汉字误杀真实地名。
    """
    if not name:
        return False
    text = name.strip()
    if not 2 <= len(text) <= 12:
        return False
    if any(w in text for w in _QUERY_WORDS):
        return False
    if text.endswith(_POI_SUFFIXES):
        return False
    return True


class TravelPlanWorkflow:
    """
    旅游规划工作流 v3.1
    - Search: 直接调用 Tavily，不走 MCP
    - Route: 通过统一 MCPAdapter 调用 route_planning 工具
    - RAG:   通过统一 MCPAdapter 调用 rag_* 工具，检索结果追加到最终输出
    - Travel: 协调 Search + Route，整合输出
    """

    def __init__(self):
        logger.info("=" * 60)
        logger.info(" 初始化旅游规划工作流 v3.0")
        logger.info("   SearchAgent: 直接调用 Tavily")
        logger.info("   RouteAgent: 统一 MCPAdapter 调用")
        logger.info("=" * 60)

        self.mcp_adapter = get_mcp_adapter()
        self.chatter_llm = LLMClientFactory.create_chatter()
        self._exit_stack = AsyncExitStack()
        self.mcp_tools = []

    async def __aenter__(self):
        logger.info("正在启动工作流环境")

        try:
            await self._exit_stack.enter_async_context(self.mcp_adapter)
            logger.info("MCP连接已建立")

            self.mcp_tools = await self.mcp_adapter.get_langgraph_tools()
            logger.info(f"已获取{len(self.mcp_tools)}个MCP工具")

            # ✅ 修复：新版 Search 不需要 mcp_tools/mcp_adapter
            self.intent_recognition = IntentRecognition()
            self.search_agent = Search()  # 直接调用 Tavily

            # 统一注入同一个 MCPAdapter：Agent 不再各自持有服务端 URL，
            # 消除「主入口同进程 / Agent 走 SSE」的传输模式分裂（P0-4）。
            self.route_agent = RouteAgent(mcp_adapter=self.mcp_adapter)
            self.travel_agent = TravelAgent(mcp_adapter=self.mcp_adapter)

            self.rag_agent = RAGAgent(mcp_adapter=self.mcp_adapter)

            self.graph = self._build_graph()
            return self

        except Exception as e:
            logger.error(f"初始化失败: {e}")
            await self._exit_stack.aclose()
            raise e

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        try:
            logger.info("正在关闭工作流环境...")
            # Agent 已无自建连接（close 为兼容空实现），连接由下方 exit_stack 统一释放
            if hasattr(self, 'route_agent'):
                await self.route_agent.close()
            if hasattr(self, 'travel_agent'):
                await self.travel_agent.close()
            await self._exit_stack.aclose()
        finally:
            self._session = None
            logger.info("...资源已释放...")

    def _build_graph(self) -> StateGraph:
        workflow = StateGraph(WorkflowState)

        workflow.add_node("intent_recognition", self._intent_recognition_node)
        workflow.add_node("chat_response", self._chat_response_node)
        workflow.add_node("search", self._search_node)
        workflow.add_node("rag", self._rag_node)
        workflow.add_node("route", self._find_route_node)
        workflow.add_node("travel", self._travel_node)

        workflow.set_entry_point("intent_recognition")

        workflow.add_conditional_edges(
            "intent_recognition",
            self._route_by_intent,
            {
                "chat": "chat_response",
                "search": "search",
                "route": "route",
                "travel": "travel"
            }
        )

        workflow.add_edge("chat_response", END)
        workflow.add_edge("search", "rag")
        workflow.add_edge("rag", END)
        workflow.add_edge("route", END)
        workflow.add_edge("travel", END)

        return workflow.compile()

    async def _intent_recognition_node(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 50)
        logger.info("进入节点: 意图识别")

        response = await self.intent_recognition.recognize(state["user_input"])

        if response.success:
            result = response.content
            state["intent"] = result["intent"]
            state["intent_confidence"] = result["confidence"]
            logger.info(f"识别结果:{result['intent']}(置信度：{result['confidence']})")
        else:
            state["intent"] = "chat"
            state["intent_confidence"] = 0.5
            logger.info("意图识别失败，默认为普通对话")
        return state

    async def _chat_response_node(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 50)
        logger.info("进入节点: 普通对话")

        response = await self.chatter_llm.ainvoke(
            "你是一个友好的AI助手,请自然地回应用户。" + state["user_input"]
        )
        state["final_output"] = response
        state["execution_path"].append("chat_response")
        logger.info("普通对话响应完成")
        return state

    async def _search_node(self, state: WorkflowState) -> WorkflowState:
        logger.info("=" * 50)
        logger.info("【节点】旅游搜索+生成")

        try:
            user_input = state["user_input"].strip()
            destination = await self._extract_destination(user_input)

            if not destination:
                state["error"] = "无法从输入中提取目的地，请直接输入城市名（如：西安）"
                state["final_output"] = state["error"]
                state["execution_path"].append("search")
                return state

            logger.info(f"提取目的地: {destination}")
            response = await self.search_agent.search(destination)

            if response.success:
                state["search_results"] = {"destination": destination, "article": response.content}
                state["final_output"] = response.content
                logger.info(f"✓ 攻略生成完成，长度: {len(response.content)}字符")
            else:
                error_msg = f"攻略生成失败: {response.error}"
                state["error"] = error_msg
                state["final_output"] = error_msg
                logger.error(error_msg)

        except Exception as e:
            error_msg = f"搜索节点异常: {str(e)}"
            state["error"] = error_msg
            state["final_output"] = error_msg
            logger.error(error_msg)

        state["execution_path"].append("search")
        return state

    async def _rag_node(self, state: WorkflowState) -> WorkflowState:
        """RAG 节点：先检索历史知识 → 再把本篇攻略入库 → 把命中片段追加到最终输出。

        设计原则（与数据真实性一致）：
        - 检索片段是原文直出，不经过 LLM 改写，避免编造；
        - 先检索后入库，避免刚入库的本篇攻略被自己检索出来造成重复回显（OBS-01）；
          本篇仍会入库，供后续会话 / 跨目的地复用；
        - 入库/检索任何失败都只记日志并降级，不覆盖、不破坏已有攻略；
        - 无相关片段时保持原输出，不追加空章节。
        """
        logger.info("=" * 50)
        logger.info("执行节点：RAG 知识库检索（tool_rag / 本地 bge-m3 + Chroma）")

        state["rag_text"] = None
        state["rag_results"] = None

        if state.get("error"):
            logger.info("上游搜索失败，跳过 RAG 检索")
            state["execution_path"].append("rag:skipped")
            return state

        search_info = state.get("search_results") or {}
        if not isinstance(search_info, dict):
            search_info = {}
        search_article = search_info.get("article")
        destination = search_info.get("destination", "") or ""

        # 1) 先用用户原始问题检索「已有」知识（本篇尚未入库，不会自命中）
        response = await self.rag_agent.retrieve_rag(state["user_input"])

        # 2) 检索后再把本篇攻略入库（确定性 ID，重复写入不产生重复块）
        if search_article:
            index_res = await self.rag_agent.load_search_to_temp(
                search_article, source=destination
            )
            if not index_res.success:
                logger.warning(f"知识库入库失败（不影响主流程）: {index_res.error}")

        # 3) 处理检索结果

        if not response.success:
            logger.warning(f"RAG 检索不可用（不影响主流程）: {response.error}")
            state["rag_results"] = f"RAG 不可用：{response.error}"
            state["execution_path"].append("rag:unavailable")
            return state

        content = response.content or {}
        rag_text = content.get("rag_text") or ""
        hits = content.get("hits") or []
        state["rag_text"] = rag_text or None
        state["rag_results"] = rag_text or "（无相关片段）"

        if rag_text:
            base = (state.get("final_output") or "").rstrip()
            state["final_output"] = (base + "\n\n" + rag_text).strip() if base else rag_text
            logger.info(f"RAG 命中 {len(hits)} 条片段，已追加到最终输出")
            state["execution_path"].append(f"rag:hits={len(hits)}")
        else:
            logger.info("RAG 未命中相关片段，保持原攻略不变")
            state["execution_path"].append("rag:no_hits")

        return state

    # ✅自动解析路线，没有起点 → 调用GPS
    async def _parse_route_info(self, user_input: str, gps_coords: str = None):
        origin = ""
        destination = ""
        mode = "auto"  # 改为 auto，让 MCP Server 自动判断最佳方式

        # 强制规则：只认城市，绝对禁止解析商店、小区
        user_input = user_input.replace("的路线", "").replace("怎么走", "").strip()

        # 清洗开头的方向词
        for prefix in ["从", "到", "去", "往"]:
            if user_input.startswith(prefix):
                user_input = user_input[len(prefix):].strip()

        if "到" in user_input:
            parts = user_input.split("到")
            origin = parts[0].strip()
            destination = parts[1].strip()
        else:
            destination = user_input.strip()

        # 地名合法性校验（P1-13）：不再用单字黑名单误杀真实地名。
        # 只排除查询语句与明确的商业 POI；不合法时置空，由上层明确追问用户。
        if origin and not _looks_like_place(origin):
            logger.warning(f"出发地不像有效地名，已忽略: {origin!r}")
            origin = ""
        if destination and not _looks_like_place(destination):
            logger.warning(f"目的地不像有效地名，已忽略: {destination!r}")
            destination = ""

        # GPS 兜底城市
        if not origin and gps_coords:
            try:
                from MCP.tool_amap.client import UnifiedAMapClient
                tool = UnifiedAMapClient()
                parts = gps_coords.split(",")
                if len(parts) == 2:
                    loc = await tool.regeocode(f"{parts[1]},{parts[0]}")
                    if loc and loc.get("status") == "success":
                        origin = loc.get("district", "") or loc.get("city", "")
            except:
                origin = ""

        return origin, destination, mode

    def _generate_train_schemes(self, route_dict: dict) -> str:
        """
        从 MCP 返回的路线字典中生成火车方案文本
        适配新版 RoutePlanner 返回格式（三段式）
        """
        try:
            # ✅ 优先使用 segment_2（火车段）信息
            segment_2 = route_dict.get("segment_2", {})
            train_info = route_dict.get("train_info")

            if not train_info and not segment_2:
                return "高铁/火车方案：暂无实时车次信息"

            # 从 train_info 或 segment_2 提取火车信息
            if train_info:
                trains = train_info if isinstance(train_info, list) else [train_info]
            else:
                # 无实时车次时明确标注不可用；不要把「不可用提示」当成车次编号展示（P1-11）
                trains = [{
                    "train_number": "暂不可用（请查询铁路 12306 官方）",
                    "departure_station": segment_2.get("from", "出发站"),
                    "arrival_station": segment_2.get("to", "到达站"),
                    "duration": f"{segment_2.get('duration', '--')} 分钟（按距离估算，非实时）",
                    "second_class": "暂不可用（费用见路线详情，为估算）",
                }]

            trains = trains[:3]
            lines = ["推荐高铁/火车出行方案："]
            labels = ["最快方案", "最便宜方案", "适中方案"]

            for i, train in enumerate(trains):
                label = labels[i] if i < len(labels) else "方案"

                if isinstance(train, dict):
                    no = train.get("train_number", "未知")
                    dep = train.get("departure_station", "")
                    arr = train.get("arrival_station", "")
                    dur = train.get("duration", "")
                    price = train.get("second_class", "")
                    pass_by = train.get("pass_stations", "无")
                else:
                    no = getattr(train, 'train_number', '未知')
                    dep = getattr(train, 'departure_station', '')
                    arr = getattr(train, 'arrival_station', '')
                    dur = getattr(train, 'duration', '')
                    price = getattr(train, 'second_class', '')
                    pass_by = getattr(train, 'pass_stations', '无')

                lines.append(f"{label}：")
                lines.append(f"  车次：{no}")
                lines.append(f"  路线：{dep} → {arr}")
                lines.append(f"  时长：{dur}")
                lines.append(f"  票价：{price}")
                lines.append(f"  途经：{pass_by}")

            return "\n".join(lines)
        except Exception as e:
            logger.warning(f"生成火车方案失败: {e}")
            return "高铁/火车方案：获取失败"

    async def _find_route_node(self, state: WorkflowState) -> WorkflowState:
        """路线规划节点 - 通过 统一 MCPAdapter 调用"""
        ori, dst, mode = await self._parse_route_info(state["user_input"], state.get("gps_coords"))

        # 目的地缺失时明确追问用户，不要把空字符串丢给路线工具（P1-13）
        if not dst:
            logger.warning("未能识别目的地，跳过路线规划并提示用户补充")
            state["final_output"] = (
                "⚠️ 未能识别目的地。请说明具体要去的城市或地点，"
                "例如「到上海的路线」「从北京到颐和园怎么走」。"
            )
            state["error"] = "destination_not_recognized"
            state["execution_path"] = (state.get("execution_path") or []) + ["route:missing_destination"]
            return state

        # 从 gps_coords 拆分出 lat / lng
        lat, lng = None, None
        if state.get("gps_coords"):
            parts = state["gps_coords"].split(",")
            if len(parts) == 2:
                try:
                    lat = float(parts[0].strip())
                    lng = float(parts[1].strip())
                except:
                    lat, lng = None, None

        # ✅ 新版 RouteAgent 调用方式
        res = await self.route_agent.plan_route(
            start=ori,
            end=dst,
            mode=mode,
            lat=lat,
            lng=lng
        )

        if res.success:
            # ✅ 新版返回格式：res.content 是字典，route_result 也是字典
            res_content = res.content
            route_dict = res_content.get("route_result", {})
            segments = res_content.get("segments", [])
            actual_mode = res_content.get("actual_mode", route_dict.get("transport_mode", ""))

            # 生成火车方案文本
            train_text = self._generate_train_schemes(route_dict)

            from datetime import datetime
            now = datetime.now()
            lines = [f"🕐 当前时间: {now.strftime('%Y-%m-%d %H:%M')}"]
            lines.append(f"🗺️ 路线规划: {ori or '当前定位'} → {dst}")

            # ✅ 从字典取字段（三段式）
            total_distance = route_dict.get("total_distance")
            total_duration = route_dict.get("total_duration")
            total_cost = route_dict.get("total_cost")

            # ✅ 显示三段式信息
            segment_1 = route_dict.get("segment_1", {})
            segment_2 = route_dict.get("segment_2", {})
            segment_3 = route_dict.get("segment_3", {})

            if segment_1:
                lines.append(f"📍 第1段：市内交通 → 出发站")
                lines.append(f"  从 {segment_1.get('from', ori)} 到 {segment_1.get('to', '出发站')}")
                lines.append(f"  距离: {segment_1.get('distance', 0):.1f}km")
                lines.append(f"  用时: {segment_1.get('duration', 0)}分钟")

            if segment_2:
                lines.append(f"🚄 第2段：火车")
                lines.append(f"  从 {segment_2.get('from', '出发站')} 到 {segment_2.get('to', '到达站')}")
                lines.append(f"  距离: {segment_2.get('distance', 0):.1f}km")
                lines.append(f"  用时: {segment_2.get('duration', 0)}分钟")
                detail = segment_2.get("detail", "")
                if detail:
                    for dl in detail.split("\n"):
                        if dl.strip():
                            lines.append(f"  {dl.strip()}")

            if segment_3:
                lines.append(f"📍 第3段：市内交通 → 目的地")
                lines.append(f"  从 {segment_3.get('from', '到达站')} 到 {segment_3.get('to', dst)}")
                lines.append(f"  距离: {segment_3.get('distance', 0):.1f}km")
                lines.append(f"  用时: {segment_3.get('duration', 0)}分钟")

            # 总统计
            lines.append(f"📊 总统计")
            if total_distance:
                lines.append(f"  总距离: {total_distance:.1f}km")
            if total_duration:
                lines.append(f"  总耗时: {total_duration}分钟")
            if total_cost:
                lines.append(f"  总费用: ¥{total_cost:.2f}")

            route_info = "\n".join(lines)
            state["final_output"] = route_info + train_text
            state["route_results"] = route_info + train_text
        else:
            error_msg = f"❌ 路线规划失败：{res.error}"
            state["final_output"] = error_msg
            state["route_results"] = error_msg

        state["execution_path"].append("route")
        return state

    async def _travel_node(self, state: WorkflowState) -> WorkflowState:
        """综合旅游规划节点"""
        res = await self.travel_agent.comprehensive_travel(
            state["user_input"],
            gps_coords=state.get("gps_coords")
        )
        if res.success:
            plan = res.content.get("travel_plan", "")
            state["final_output"] = self._clean_final_output(plan)
            # 把 MCP 层的数据质量标注透传到工作流状态，供 /chat 响应使用（DEF-04）
            state["data_quality"] = res.content.get("data_quality", "unknown")
            state["is_realtime"] = bool(res.content.get("is_realtime", False))
            state["warnings"] = list(res.content.get("warnings") or [])
        else:
            error = res.error or "综合规划失败"
            state["final_output"] = f"❌ 综合规划失败：{error}"
            state["error"] = error
            state["data_quality"] = "failed"
            state["is_realtime"] = False
            state["warnings"] = ["综合规划失败，未生成有效行程"]
        state["execution_path"].append("travel")
        return state

    async def _extract_destination(self, user_input: str) -> str:
        try:
            prompt = f"""从以下用户输入中提取旅游目的地（城市名）。
用户输入：{user_input}
要求：只返回城市名
格式：直接输出
"""
            result = await self.chatter_llm.ainvoke(prompt)
            destination = result.strip().strip()
            if destination in ["未知", ""]:
                return ""
            return destination
        except:
            return ""

    def _route_by_intent(self, state: WorkflowState) -> Literal["chat", "route", "search", "travel"]:
        intent = state.get("intent", "chat")
        logger.info(f"路由决策: {intent}")
        return intent

    def _clean_final_output(self, raw: str) -> str:
        if not raw:
            return ""
        # 去掉 # ### 标题
        raw = re.sub(r'#{1,6}\s*', '', raw)
        # 去掉 **加粗**
        raw = re.sub(r'\*\*(.*?)\*\*', r'\1', raw)
        # 去掉代码块
        raw = re.sub(r'```[\s\S]*?```', '', raw)
        # 压缩多余空行
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()

    async def run_stream(self, user_input: str, on_message, gps_coords: str = None):
        """
        流式执行工作流，通过 on_message 回调推送阶段性消息
        """
        logger.info("=" * 50)
        logger.info("启动流式工作流")
        logger.info(f"用户输入: {user_input}")

        await on_message("step", "🔍 正在分析您的需求...")

        initial_state: WorkflowState = {
            "user_input": user_input,
            "intent": "chat",
            "intent_confidence": 0.0,
            "gps_coords": gps_coords,
            "search_results": None,
            "route_results": None,
            "rag_text": None,
            "rag_results": None,
            "data_quality": None,
            "is_realtime": None,
            "warnings": None,
            "final_output": "",
            "error": None,
            "execution_path": []
        }

        try:
            await on_message("step", "🧠 意图识别中...")
            final_state = await self.graph.ainvoke(initial_state)

            intent = final_state.get("intent", "chat")
            path = final_state.get("execution_path", [])
            final_text = ""
            await on_message("step", f"✅ 意图识别: {intent}")

            if intent == "chat":
                prompt = "你是一个友好的AI助手,请自然地回应用户。" + user_input
                async for chunk in self.chatter_llm.astream(prompt):
                    if chunk:
                        final_text += chunk
                        await on_message("partial", chunk)
                        await asyncio.sleep(0.01)
            elif final_state.get("final_output"):
                raw = final_state["final_output"]
                cleaned = self._clean_final_output(raw)
                final_text = cleaned
                chunk_size = 50
                for i in range(0, len(cleaned), chunk_size):
                    chunk = cleaned[i:i + chunk_size]
                    await on_message("partial", chunk)
                    await asyncio.sleep(0.02)

            await on_message("done", {
                "intent": intent,
                "path": path,
                "error": final_state.get("error"),
                "final_text": final_text,
                "data_quality": final_state.get("data_quality"),
                "is_realtime": final_state.get("is_realtime"),
                "warnings": final_state.get("warnings"),
            })

        except Exception as e:
            logger.error(f"工作流流式执行失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            await on_message("error", str(e))

    async def run(self, user_input: str, gps_coords: str = None) -> WorkflowState:
        logger.info("=" * 50)
        logger.info("启动内容生成工作流（完整异步 + MCP 协作）")
        logger.info(f"用户输入: {user_input}")
        logger.info("=" * 50)

        initial_state: WorkflowState = {
            "user_input": user_input,
            "intent": "chat",
            "intent_confidence": 0.0,
            "gps_coords": gps_coords,
            "search_results": None,
            "route_results": None,
            "rag_text": None,
            "rag_results": None,
            "data_quality": None,
            "is_realtime": None,
            "warnings": None,
            "final_output": "",
            "error": None,
            "execution_path": []
        }

        try:
            final_state = await self.graph.ainvoke(initial_state)
            logger.info("=" * 50)
            logger.info("工作流执行完成")
            logger.info(f"执行路径: {' -> '.join(final_state['execution_path'])}")
            logger.info("=" * 50)
            # 格式清洗
            if final_state.get("final_output"):
                final_state["final_output"] = self._clean_final_output(final_state["final_output"])
            return final_state

        except Exception as e:
            logger.error(f"工作流执行失败: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            initial_state["error"] = str(e)
            initial_state["final_output"] = f"执行失败: {str(e)}"
            return initial_state