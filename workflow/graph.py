"""
旅游规划工作流 — LangGraph StateGraph 编排

数据流：
    用户输入 → intent_recognition → conditional_edges → [chat|search|route|travel] → END

Agent 职责：
    - IntentAgent: prompt 约束 JSON 输出 + 正则解析的意图分类（不走 ReAct，见 intent_agent.py）
    - ChatAgent:   ReAct，无工具，闲聊
    - SearchAgent: ReAct，授权 tavily_web_search
    - RouteAgent:  ReAct，授权 geocode/regeocode/route_planning/py12306_query_train_schedule
    - Integrator:  ReAct，无工具，整合攻略 + 路线为最终方案
"""
import asyncio
import json
import re
from typing import Literal

from langchain_core.tools import BaseTool
from langgraph.graph import StateGraph, END

from ..agents.intent_agent import IntentAgent
from ..agents.chat_agent import ChatAgent
from ..agents.search_agent import SearchAgent
from ..agents.route_agent import RouteAgent
from ..agents.integrator_agent import IntegratorAgent
from ..config.llm_client import LLMClientFactory
from ..config.logger import get_logger
from ..workflow.state import WorkflowState

logger = get_logger(__name__)


class TravelPlanWorkflow:
    """旅游规划工作流 — 通过 MCP stdio 统一获取所有外部工具。"""

    def __init__(self):
        logger.info("=" * 60)
        logger.info("初始化旅游规划工作流")
        logger.info("=" * 60)
        self.mcp_client = None
        self.mcp_tools: list[BaseTool] = []

    async def __aenter__(self):
        from ..config.settings import settings

        try:
            # === 建立 MCP 连接并加载工具 ===
            # settings.MCP_CLIENT 已经是 MultiServerMCPClient 实例
            self.mcp_client = settings.MCP_CLIENT
            self.mcp_tools = await self.mcp_client.get_tools()
            logger.info(f"已加载 {len(self.mcp_tools)} 个 MCP 工具: {[t.name for t in self.mcp_tools]}")

            # === 按名分发工具子集给各 Agent ===
            tool_map = {t.name: t for t in self.mcp_tools}
            self._validate_tools(tool_map)

            self.chatter_llm = LLMClientFactory.create_chatter()

            self.intent_agent = IntentAgent(llm_client=LLMClientFactory.create_intent_recognizer())

            search_tools = [tool_map[name] for name in ["tavily_web_search"] if name in tool_map]
            self.search_agent = SearchAgent(
                llm_client=self.chatter_llm, tools=search_tools
            )

            route_tool_names = [
                "amap_geocode", "amap_regeocode",
                "amap_route_planning", "py12306_query_train_schedule",
            ]
            route_tools = [tool_map[n] for n in route_tool_names if n in tool_map]
            self.route_agent = RouteAgent(
                llm_client=self.chatter_llm, tools=route_tools
            )

            self.chat_agent = ChatAgent(llm_client=self.chatter_llm)

            self.integrator_agent = IntegratorAgent(llm_client=self.chatter_llm)

            # === 构建图 ===
            self.graph = self._build_graph()

        except Exception as e:
            logger.error(f"初始化失败: {e}")
            raise

        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        logger.info("正在关闭工作流环境...")
        # 仅清空工具引用；MCP 客户端（settings.MCP_CLIENT）为模块级共享单例，
        # 由整个进程生命周期管理（不在此关闭），避免与其他工作流实例互相影响
        self.mcp_tools.clear()
        logger.info("工作流资源已释放")

    # ────────────── 图构建 ──────────────

    def _build_graph(self) -> StateGraph:
        """构建并编译 StateGraph：intent_recognition 为入口，按意图条件路由到 4 个叶子节点。"""
        workflow = StateGraph(WorkflowState)

        # 节点
        workflow.add_node("intent_recognition", self._intent_recognition_node)
        workflow.add_node("chat_response", self._chat_response_node)
        workflow.add_node("search", self._search_node)
        workflow.add_node("route", self._find_route_node)
        workflow.add_node("travel", self._travel_node)

        # 入口
        workflow.set_entry_point("intent_recognition")

        # 条件路由
        workflow.add_conditional_edges(
            "intent_recognition",
            self._route_by_intent,
            {"chat": "chat_response", "search": "search", "route": "route", "travel": "travel"},
        )

        # 叶子节点直连 END
        for node in ("chat_response", "search", "route", "travel"):
            workflow.add_edge(node, END)

        return workflow.compile()

    # ────────────── 节点实现 ──────────────

    async def _intent_recognition_node(self, state: WorkflowState) -> dict:
        """意图识别节点：调用 IntentAgent，把结果写回 state（intent / intent_confidence）。

        识别失败时兜底为 chat，保证后续条件路由始终有合法分支可走。
        """
        response = await self.intent_agent.recognize(state["user_input"])
        if response.success:
            content = response.content or {}
            intent = content.get("intent", "chat")
            confidence = content.get("confidence", 0.5)
        else:
            intent = "chat"
            confidence = 0.5
        logger.info(f"[Intent] {intent} (conf={confidence})")
        return {"intent": intent, "intent_confidence": confidence}

    async def _chat_response_node(self, state: WorkflowState) -> dict:
        """闲聊节点：ChatAgent 直接回复，不调用外部工具。"""
        history = self._to_agent_history(state.get("chat_history"))
        response = await self.chat_agent.run(state["user_input"], history=history)
        if not response.success:
            return self._failure_output(state, "chat_response", response.error or "未知错误")
        output = response.content
        logger.info(f"[Chat] 回复完成 ({len(output)}字符)")
        return {"final_output": output, "execution_path": [*state.get("execution_path", []), "chat_response"]}

    async def _search_node(self, state: WorkflowState) -> dict:
        """攻略搜索节点：提取目的地 → SearchAgent 生成攻略。"""
        destination = await self._extract_destination(state["user_input"])
        if not destination:
            return self._failure_output(state, "search", "无法从输入中提取目的地，请指明城市名（如：西安）")
        logger.info(f"[Search] 搜索目的地: {destination}")
        response = await self.search_agent.run(
            f"生成{destination}的旅游攻略",
            history=self._to_agent_history(state.get("chat_history"), limit=4),
        )
        if response.success:
            article = response.content
            return {
                "search_results": {"destination": destination, "article": article},
                "final_output": article,
                "execution_path": [*state.get("execution_path", []), "search"],
            }
        else:
            return self._failure_output(state, "search", response.error or "未知错误")

    async def _find_route_node(self, state: WorkflowState) -> dict:
        """路线规划节点：解析起终点 → RouteAgent 规划路线。"""
        origin, destination = await self._parse_location_info(state["user_input"])
        query = ""
        if origin:
            query = f"从 {origin} 到 {destination}"
        else:
            query = f"去 {destination}"
        logger.info(f"[Route] 规划路线: {query}")
        response = await self.route_agent.run(
            query,
            history=self._to_agent_history(state.get("chat_history"), limit=4),
        )
        if not response.success:
            return self._failure_output(state, "route", response.error or "未知错误")
        output = response.content
        return {
            "route_results": output,
            "final_output": output,
            "execution_path": [*state.get("execution_path", []), "route"],
        }

    async def _travel_node(self, state: WorkflowState) -> dict:
        """综合旅游规划：并发调用搜索+路线，再交由 integrator 整合。"""
        try:
            # 抽取目的地
            destination = await self._extract_destination(state["user_input"])
            if not destination:
                return self._failure_output(state, "travel", "无法识别目的地，请重新输入")
            logger.info(f"[Travel] 综合规划目的地: {destination}")

            # 并发：攻略搜索 + 路线规划（路线含 GPS 定位起点）
            agent_history = self._to_agent_history(state.get("chat_history"), limit=4)
            gps_city = state.get("gps_city") or ""
            route_query = (
                f"从 {gps_city} 前往 {destination} 的交通路线"
                if gps_city else f"规划前往{destination}的交通路线"
            )
            search_task = self.search_agent.run(
                f"生成{destination}的旅游攻略", thread_id="travel_search", history=agent_history
            )
            route_task = self.route_agent.run(
                route_query, thread_id="travel_route", history=agent_history
            )
            search_res, route_res = await asyncio.gather(search_task, route_task)

            search_text = search_res.content if search_res.success else "暂无攻略信息"
            route_text = route_res.content if route_res.success else "暂无路线信息"

            # 整合
            integrator_query = f"【景点攻略】\n{search_text}\n\n【交通路线】\n{route_text}\n\n请整合为完整旅行方案。"
            intg_res = await self.integrator_agent.run(integrator_query, thread_id="travel_integrate")

            if not intg_res.success:
                return self._failure_output(state, "travel", intg_res.error or "整合失败，部分结果可能不完整")
            final = self._clean_final_output(intg_res.content)

            return {
                "search_results": {"destination": destination, "article": search_text},
                "route_results": route_text,
                "final_output": final,
                "execution_path": [*state.get("execution_path", []), "travel"],
            }
        except Exception as e:
            logger.error(f"[Travel] 综合规划失败: {e}")
            return self._failure_output(state, "travel", str(e))

    # ────────────── 辅助方法 ──────────────

    _STEP_LABELS = {
        "chat_response": "回复",
        "search": "攻略搜索",
        "route": "路线规划",
        "travel": "综合规划",
    }

    def _failure_output(self, state: WorkflowState, node: str, reason: str) -> dict:
        """统一的失败输出协议：error 与 final_output 一致，格式固定。"""
        label = self._STEP_LABELS.get(node, node)
        msg = f"抱歉，{label}失败：{reason}"
        logger.error(f"[{node}] {msg}")
        return {
            "error": msg,
            "final_output": msg,
            "execution_path": [*state.get("execution_path", []), node],
        }

    @staticmethod
    def _validate_tools(tool_map: dict) -> None:
        """校验各 Agent 所需的 MCP 工具是否齐全，缺失即快速失败，避免静默降级。"""
        required = {
            "search_agent": ["tavily_web_search"],
            "route_agent": [
                "amap_geocode", "amap_regeocode",
                "amap_route_planning", "py12306_query_train_schedule",
            ],
        }
        missing = {
            name: [t for t in names if t not in tool_map]
            for name, names in required.items()
        }
        missing = {k: v for k, v in missing.items() if v}
        if missing:
            raise RuntimeError(
                f"MCP 工具加载不完整，缺失: {missing}，请检查 mcp_server/mcp_server_stdio.py 的注册"
            )

    @staticmethod
    def _to_agent_history(chat_history, limit: int = 20) -> list:
        """把会话历史 [{role: user|assistant, content}] 转为 [(human|ai, content)]。

        只取最近 limit 条，避免 ReAct 上下文膨胀拖慢工具调用。
        """
        if not chat_history:
            return []
        converted = []
        for m in chat_history[-limit:]:
            role = m.get("role", "user")
            role = "human" if role == "user" else ("ai" if role == "assistant" else role)
            converted.append((role, m.get("content", "")))
        return converted

    def _route_by_intent(self, state: WorkflowState) -> Literal["chat", "route", "search", "travel"]:
        """条件路由函数：按 intent 字段返回下一个节点名（供 conditional_edges 使用）。"""
        intent = state.get("intent", "chat")
        logger.info(f"[Router] {intent}")
        return intent

    async def _extract_destination(self, user_input: str) -> str:
        """提取目的地城市名。

        策略（确定性优先）：
        1. 规则解析：输入含明确的 "从X到Y"/"X到Y"/"X-Y" 结构 → 直接取终点（不调 LLM）
        2. LLM 提炼：无分隔符的自由表述（如"去西安旅游帮我规划一下"）→ 低置信度由 LLM 判断
        3. 规则兜底：LLM 失败/空时，回退规则解析结果，保证不返回空
        """
        origin, destination = await self._parse_location_info(user_input)
        rule_dest = self._clean_destination(destination)
        rule_hit = bool(origin)  # 规则能拆出起点，说明是明确的 "X到Y" 结构

        # 1) 明确结构 → 直接取终点，稳定且不耗 LLM 额度
        if rule_hit and rule_dest:
            return rule_dest

        # 2) LLM 提炼（temperature 由 create_chatter 决定，此处仅做解析清洗）
        llm_dest = ""
        try:
            prompt = f"只返回城市名。如果以下输入中没有地名就回答“未知”：\n{user_input}"
            result = await self.chatter_llm.ainvoke(prompt)
            llm_dest = self._clean_destination(result)
        except Exception as e:
            logger.warning(f"[Travel] LLM 目的地提取失败: {e}")

        # 3) 规则结果兜底（即使不完美，也优于直接报错）
        return llm_dest or rule_dest

    @staticmethod
    def _clean_destination(raw: str) -> str:
        """清洗目的地：去引号/空白、并列多城市（逗号/顿号/空格分隔）取第一个为主目的地、非法值返回空。"""
        if not raw or not isinstance(raw, str):
            return ""
        text = raw.strip().strip('"').strip("'").strip("`").strip()
        parts = [p.strip() for p in re.split(r'[,，、\s]+', text) if p.strip()]
        dest = parts[0] if parts else text
        if dest in ("未知", "无", "没有", "null", "none", "None"):
            return ""
        return dest if len(dest) >= 2 else ""

    async def _resolve_origin_from_gps(self, gps_coords: str) -> str:
        """通过逆地理编码把 GPS 坐标转为城市名，失败返回空串（不阻断主流程）。"""
        try:
            from ..mcp_server.tool_amap.async_wrappers import regeocode

            raw = await regeocode(gps_coords)
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if not parsed or parsed.get("status") == "failed":
                return ""
            city = parsed.get("city", "")
            if isinstance(city, list):
                city = city[0] if city else parsed.get("province", "")
            return city or ""
        except Exception as e:
            logger.warning(f"[GPS] 定位城市解析失败: {e}")
            return ""

    async def _parse_location_info(self, user_input: str) -> tuple[str, str]:
        """解析出行规划的起点和终点。

        支持两种定位前缀（均作为起点兜底，仅在用户未明确起点时生效）：
        - [当前位置:北京]   （graph.run 内 GPS 逆地理编码生成）
        - [位于北京市东城区] （app.py 根据前端 address 生成）
        """
        gps_city = ""
        m = re.match(r'^\[(?:当前位置[:：]|位于)(.+?)\]', user_input)
        if m:
            gps_city = m.group(1).strip()
            user_input = user_input[m.end():].strip()

        text = user_input.replace("的路线", "").replace("怎么走", "").replace("怎么去", "").strip()
        for prefix in ["从", "到", "去", "往"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if "到" in text:
            parts = text.split("到")
            origin, destination = parts[0].strip(), parts[1].strip()
        elif "-" in text:
            parts = text.split("-")
            origin, destination = parts[0].strip(), parts[1].strip()
        else:
            origin, destination = "", text.strip()

        # 城市名后出现逗号/顿号通常是补充说明（如"上海，顺便推荐攻略"）→ 截断
        origin = re.split(r'[,，、]', origin)[0].strip()
        destination = re.split(r'[,，、]', destination)[0].strip()

        # 定位前缀仅作兜底：用户明确起点（origin 非空）时优先尊重用户输入
        if not origin and gps_city:
            origin = gps_city
        return origin, destination

    @staticmethod
    def _clean_final_output(raw: str) -> str:
        """清理 markdown 格式噪音。"""
        raw = re.sub(r'#{1,6}\s*', '', raw)
        raw = re.sub(r'\*\*(.*?)\*\*', r'\1', raw)
        raw = re.sub(r'```[\s\S]*?```', '', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()

    # ────────────── 运行接口 ──────────────

    async def run(self, user_input: str, gps_coords: str = None, history: list = None) -> WorkflowState:
        """执行一次完整的工作流，返回最终 state。

        Args:
            user_input: 用户输入。可为原始文本，或已带 [位于xx] 前缀（app.py 生成）。
            gps_coords: 前端定位坐标 "lng,lat"；非空且输入未含定位前缀时，
                会先逆地理编码为城市并注入 [当前位置:城市] 前缀作为出行起点候选。
            history: 会话历史 [{role, content}, ...]，用于多轮记忆。

        Returns:
            最终 WorkflowState；异常时 error/final_output 携带失败信息，不向上抛。
        """
        # GPS 定位城市解析：app 层已带 [位于xx] 前缀时跳过，避免重复逆地理编码
        gps_city = ""
        if gps_coords and "[位于" not in user_input and "[当前位置" not in user_input:
            gps_city = await self._resolve_origin_from_gps(gps_coords)
            if gps_city:
                user_input = f"[当前位置:{gps_city}] {user_input}"

        initial_state: WorkflowState = {
            "user_input": user_input,
            "intent": "chat",
            "intent_confidence": 0.0,
            "gps_coords": gps_coords,
            "gps_city": gps_city,
            "search_results": None,
            "route_results": None,
            "rag_results": None,
            "chat_history": history,
            "final_output": "",
            "error": None,
            "execution_path": [],
        }
        try:
            final_state = await self.graph.ainvoke(initial_state)
            logger.info(f"执行路径: {' -> '.join(final_state.get('execution_path', []))}")
            if final_state.get("final_output"):
                final_state["final_output"] = self._clean_final_output(final_state["final_output"])
            return final_state
        except Exception as e:
            logger.error(f"工作流执行失败: {e}")
            import traceback
            logger.error(traceback.format_exc())
            initial_state["error"] = str(e)
            initial_state["final_output"] = f"执行失败: {str(e)}"
            return initial_state
