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
    """通用 ReAct Agent 封装。

    每个 Agent = 专门调教的 LLM + 被授权的工具子集 + system prompt。
    子类只需在 __init__ 里传入三要素并给 name，即可获得统一的 run() 接口。
    """

    def __init__(
        self,
        llm_client: LLMClient,
        tools: List[BaseTool],
        system_prompt: str,
        name: str = "agent",
    ):
        """初始化 Agent。

        Args:
            llm_client: 已配置的 LLM 客户端（取其 .llm 供 create_react_agent 使用）。
            tools: 授权给该 Agent 的工具列表（来自 MCP 工具分发；聊天/整合 Agent 传空）。
            system_prompt: 系统提示词，定义该 Agent 的角色、职责与输出格式。
            name: Agent 名称，用于日志关联与 thread_id 生成。
        """
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
            AgentResponse(success, content, error, metadata)：
            - success=True 时 content 为最终回复文本；
            - success=False 时 error 携带失败原因，content 为 None；
            - metadata 含 thread_id 与 agent 名，便于追踪。

        Notes:
            - recursion_limit=15：限制 ReAct 单次任务的最大推理步数（工具调用），
              防止模型陷入死循环耗尽上下文；
            - 方法内部兜底所有异常，调用方无需 try/except 即可安全消费返回值。
        """
        if thread_id is None:
            thread_id = f"{self.name}_{uuid.uuid4().hex[:8]}"

        # 拼接历史 + 当前问题（history 在前），统一转成 (role, content) 二元组格式
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
            # ReAct 结果取最后一条消息（即 Agent 的最终回复）
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
