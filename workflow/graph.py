"""
旅游规划工作流 — LangGraph StateGraph 编排

数据流：
    用户输入 → intent_recognition → conditional_edges → [chat|search|route|travel] → END

Agent 职责：
    - IntentAgent: 结构化输出意图分类（不走 ReAct）
    - ChatAgent:   ReAct，无工具，闲聊
    - SearchAgent: ReAct，授权 tavily_web_search
    - RouteAgent:  ReAct，授权 geocode/regeocode/route_planning/py12306_query_train_schedule
    - Integrator:  ReAct，无工具，整合攻略 + 路线为最终方案
"""
import asyncio
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
        from langchain_mcp_adapters.client import MultiServerMCPClient
        from ..config.settings import settings

        try:
            # === 建立 MCP 连接并加载工具 ===
            # settings.MCP_CLIENT 已经是 MultiServerMCPClient 实例
            self.mcp_client = settings.MCP_CLIENT
            self.mcp_tools = await self.mcp_client.get_tools()
            logger.info(f"已加载 {len(self.mcp_tools)} 个 MCP 工具: {[t.name for t in self.mcp_tools]}")

            # === 按名分发工具子集给各 Agent ===
            tool_map = {t.name: t for t in self.mcp_tools}

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
        self.mcp_tools.clear()
        logger.info("工作流资源已释放")

    # ────────────── 图构建 ──────────────

    def _build_graph(self) -> StateGraph:
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
        response = await self.chat_agent.run(state["user_input"])
        output = response.content if response.success else f"抱歉: {response.error}"
        logger.info(f"[Chat] 回复完成 ({len(output)}字符)")
        return {"final_output": output, "execution_path": [*state.get("execution_path", []), "chat_response"]}

    async def _search_node(self, state: WorkflowState) -> dict:
        destination = await self._extract_destination(state["user_input"])
        if not destination:
            error_msg = "无法从输入中提取目的地，请指明城市名（如：西安）。"
            return {
                "error": error_msg,
                "final_output": error_msg,
                "execution_path": [*state.get("execution_path", []), "search"],
            }
        logger.info(f"[Search] 搜索目的地: {destination}")
        response = await self.search_agent.run(f"生成{destination}的旅游攻略")
        if response.success:
            article = response.content
            return {
                "search_results": {"destination": destination, "article": article},
                "final_output": article,
                "execution_path": [*state.get("execution_path", []), "search"],
            }
        else:
            error_msg = f"攻略生成失败: {response.error}"
            return {
                "error": error_msg,
                "final_output": error_msg,
                "execution_path": [*state.get("execution_path", []), "search"],
            }

    async def _find_route_node(self, state: WorkflowState) -> dict:
        origin, destination = await self._parse_location_info(state["user_input"])
        query = ""
        if origin:
            query = f"从 {origin} 到 {destination}"
        else:
            query = f"去 {destination}"
        logger.info(f"[Route] 规划路线: {query}")
        response = await self.route_agent.run(query)
        output = response.content if response.success else f"路线规划失败: {response.error}"
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
                error_msg = "无法识别目的地，请重新输入。"
                return {
                    "error": error_msg,
                    "final_output": error_msg,
                    "execution_path": [*state.get("execution_path", []), "travel"],
                }
            logger.info(f"[Travel] 综合规划目的地: {destination}")

            # 并发：攻略搜索 + 路线规划
            search_task = self.search_agent.run(f"生成{destination}的旅游攻略", thread_id="travel_search")
            route_task = self.route_agent.run(f"规划前往{destination}的交通路线", thread_id="travel_route")
            search_res, route_res = await asyncio.gather(search_task, route_task)

            search_text = search_res.content if search_res.success else "暂无攻略信息"
            route_text = route_res.content if route_res.success else "暂无路线信息"

            # 整合
            integrator_query = f"【景点攻略】\n{search_text}\n\n【交通路线】\n{route_text}\n\n请整合为完整旅行方案。"
            intg_res = await self.integrator_agent.run(integrator_query, thread_id="travel_integrate")

            final = intg_res.content if intg_res.success else "整合失败，部分结果可能不完整。"
            final = self._clean_final_output(final)

            return {
                "search_results": {"destination": destination, "article": search_text},
                "route_results": route_text,
                "final_output": final,
                "execution_path": [*state.get("execution_path", []), "travel"],
            }
        except Exception as e:
            error_msg = f"综合规划失败: {str(e)}"
            logger.error(f"[Travel] {error_msg}")
            return {
                "error": error_msg,
                "final_output": error_msg,
                "execution_path": [*state.get("execution_path", []), "travel"],
            }

    # ────────────── 辅助方法 ──────────────

    def _route_by_intent(self, state: WorkflowState) -> Literal["chat", "route", "search", "travel"]:
        intent = state.get("intent", "chat")
        logger.info(f"[Router] {intent}")
        return intent

    async def _extract_destination(self, user_input: str) -> str:
        """用 LLM 快速提取目的地城市名。"""
        prompt = f"只返回城市名。如果以下输入中没有地名就回答“未知”：\n{user_input}"
        result = await self.chatter_llm.ainvoke(prompt)
        dest = result.strip().strip('"').strip("'").strip()
        return dest if dest != "未知" and len(dest) >= 2 else ""

    async def _parse_location_info(self, user_input: str) -> tuple[str, str]:
        """解析出行规划的起点和终点。"""
        text = user_input.replace("的路线", "").replace("怎么走", "").replace("怎么去", "").strip()
        for prefix in ["从", "到", "去", "往"]:
            if text.startswith(prefix):
                text = text[len(prefix):].strip()
        if "到" in text:
            parts = text.split("到")
            return parts[0].strip(), parts[1].strip()
        elif "-" in text:
            parts = text.split("-")
            return parts[0].strip(), parts[1].strip()
        else:
            return "", text.strip()

    @staticmethod
    def _clean_final_output(raw: str) -> str:
        """清理 markdown 格式噪音。"""
        raw = re.sub(r'#{1,6}\s*', '', raw)
        raw = re.sub(r'\*\*(.*?)\*\*', r'\1', raw)
        raw = re.sub(r'```[\s\S]*?```', '', raw)
        raw = re.sub(r'\n{3,}', '\n\n', raw)
        return raw.strip()

    # ────────────── 运行接口 ──────────────

    async def run(self, user_input: str, gps_coords: str = None) -> WorkflowState:
        initial_state: WorkflowState = {
            "user_input": user_input,
            "intent": "chat",
            "intent_confidence": 0.0,
            "gps_coords": gps_coords,
            "search_results": None,
            "route_results": None,
            "rag_results": None,
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
