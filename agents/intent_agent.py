"""意图识别智能体 — 使用结构化输出返回 {intent, confidence}"""
from pydantic import BaseModel, Field

from ..config.llm_client import LLMClient
from ..config.logger import get_logger
from ..workflow.state import AgentResponse

logger = get_logger(__name__)


class IntentSchema(BaseModel):
    """意图输出 schema"""
    intent: str = Field(description="意图类型，只能是 chat / route / search / travel 之一")
    confidence: float = Field(ge=0.0, le=1.0, description="0-1 的置信度")


_INTENT_PROMPT = """\
你是一个意图分类器。分析用户输入，判断其意图属于以下四类之一：

1. **chat**: 普通对话、闲聊、知识问答、简单信息查询（不涉及具体地点间的出行或旅游规划）
   - 示例："你好"、"今天天气怎么样"、"什么是量子计算"

2. **route**: 纯出行规划（从 A 地到 B 地的交通路线，不涉及景点和游玩）
   - 特征：明确提到从 A 到 B，或"怎么去"、"怎么走"、"导航"
   - 注意：仅有"路线""怎么去"等出行词，没有旅游/游玩意图 → route

3. **search**: 纯景点/旅游地信息查询（只查信息，不做路线规划）
   - 特征：询问某地有什么好玩的、著名景点、美食推荐
   - 示例："西安有什么著名景点"、"成都美食推荐"

4. **travel**: 旅游规划（需要路线 + 景点的完整方案）
   - 特征：明确表达旅游、游玩意图，包含目的地，需要制定旅游规划
   - 示例："我想去西安旅游帮我规划一下"、"去成都玩三天怎么安排"

【优先级规则】
1. 有"旅游/游玩/行程安排/攻略"等词 + 目的地 → travel
2. 有"从A到B/怎么去/怎么走" + 无旅游词 → route
3. 有"有什么好玩的/景点/美食" + 无出行需求 → search
4. 仅问候、知识问答、无具体地点 → chat
5. 信息不足时保守归类为 chat

请严格按 JSON 格式输出，不要添加任何额外文字：
{{"intent": "xxx", "confidence": 0.xx}}

用户输入: {user_input}
"""


class IntentAgent:
    """意图识别 Agent，直接调用结构化模型，不走 ReAct。"""

    def __init__(self, llm_client: LLMClient):
        # 低温度保证稳定性，用结构化输出强制约束
        self.structured_llm = llm_client.llm.with_structured_output(IntentSchema)
        self.llm_client = llm_client
        logger.info("[IntentAgent] 已初始化（结构化输出模式）")

    async def recognize(self, user_input: str) -> AgentResponse:
        try:
            prompt = _INTENT_PROMPT.format(user_input=user_input)
            result = await self.structured_llm.ainvoke(prompt)
            return AgentResponse(
                success=True,
                content={"intent": result.intent, "confidence": result.confidence},
            )
        except Exception as e:
            logger.error(f"[IntentAgent] 识别失败: {e}")
            # 兜底：返回 chat，中置信度
            return AgentResponse(
                success=False,
                error=str(e),
                content={"intent": "chat", "confidence": 0.5},
            )
