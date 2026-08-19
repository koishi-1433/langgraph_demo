"""
通用 ReAct Agent 基类

每个 Agent = 专门调教的 LLM + 被授权的工具子集 + system prompt。
BaseAgent 负责把这些东西塞进 create_react_agent，并提供统一的 run 接口。
"""
import uuid
from typing import List, Optional

from langchain_core.tools import BaseTool
from langgraph.prebuilt import create_react_agent

from ..config.llm_client import LLMClient
from ..config.logger import get_logger
from ..workflow.state import AgentResponse

logger = get_logger(__name__)


class BaseAgent:
    """通用 ReAct Agent 封装"""

    def __init__(
        self,
        llm_client: LLMClient,
        tools: List[BaseTool],
        system_prompt: str,
        name: str = "agent",
    ):
        self.name = name
        self.llm_client = llm_client
        self.tools = tools
        self.agent = create_react_agent(
            model=llm_client.llm,
            tools=tools,
            prompt=system_prompt,
        )
        logger.info(f"[{name}] 已初始化，授权工具: {[t.name for t in tools]}")

    async def run(self, query: str, thread_id: Optional[str] = None, history: Optional[list] = None) -> AgentResponse:
        """
        执行一次 Agent 任务。

        Args:
            query: 用户请求 / 任务描述
            thread_id: 会话 ID（用于日志关联与追踪，不承担持久化记忆），不传则自动生成
            history: 历史消息列表 [(role, content), ...]，role 为 human/ai，按时间正序

        Returns:
            AgentResponse(success, content, error, metadata)
        """
        if thread_id is None:
            thread_id = f"{self.name}_{uuid.uuid4().hex[:8]}"

        # 拼接历史 + 当前问题（history 在前）
        messages = [tuple(m) if isinstance(m, (list, tuple)) else (m.get("role", "human"), m.get("content", ""))
                    for m in (history or [])]
        messages.append(("human", query))

        try:
            logger.info(f"[{self.name}] run -> {query[:60]}...")
            result = await self.agent.ainvoke(
                {"messages": messages},
                config={
                    "configurable": {"thread_id": thread_id},
                    "recursion_limit": 15,
                },
            )
            content = result["messages"][-1].content
            return AgentResponse(
                success=True,
                content=content,
                metadata={"thread_id": thread_id, "agent": self.name},
            )
        except Exception as e:
            logger.error(f"[{self.name}] 执行失败: {e}")
            return AgentResponse(
                success=False,
                error=str(e),
                metadata={"thread_id": thread_id, "agent": self.name},
            )
