"""意图识别智能体 — prompt 约束 JSON 输出 + 严格解析（不依赖 with_structured_output）

背景：`with_structured_output` 通过 OpenAI API 的 response_format 参数实现，
部分中转/兼容 API（如 deepseek-v4-pro via chatanywhere）不支持该参数（400
"This response_format type is unavailable now"），导致意图识别每次异常兜底为
chat，route / search / travel 分支全部失效。
改为普通文本调用 + 正则提取 JSON + 校验兜底，兼容任意 OpenAI 兼容 API。
"""
import json
import re

from ..config.llm_client import LLMClient
from ..config.logger import get_logger
from ..workflow.state import AgentResponse

logger = get_logger(__name__)

_INTENTS = {"chat", "route", "search", "travel"}


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
    """意图识别 Agent：普通 LLM 调用 + JSON 解析，不依赖结构化输出。"""

    def __init__(self, llm_client: LLMClient):
        self.llm_client = llm_client
        logger.info("[IntentAgent] 已初始化（prompt JSON 解析模式）")

    async def recognize(self, user_input: str) -> AgentResponse:
        try:
            prompt = _INTENT_PROMPT.format(user_input=user_input)
            raw = await self.llm_client.ainvoke(prompt)
            intent, confidence = self._parse_json_response(raw)
            logger.info(f"[IntentAgent] 识别结果: {intent} (conf={confidence})")
            return AgentResponse(
                success=True,
                content={"intent": intent, "confidence": confidence},
            )
        except Exception as e:
            logger.error(f"[IntentAgent] 识别失败: {e}")
            # 兜底：返回 chat，中置信度，保证任何输入都不中断
            return AgentResponse(
                success=False,
                error=str(e),
                content={"intent": "chat", "confidence": 0.5},
            )

    @staticmethod
    def _parse_json_response(raw: str) -> tuple[str, float]:
        """从模型输出中提取 {intent, confidence}。

        容忍 ```json 代码块包裹、前后噪音文本、多余逗号等常见偏差。
        解析失败或意图非法时抛出异常，由 recognize 统一兜底。
        """
        text = raw.strip()
        # 去掉可能的 ```json ... ``` 包裹
        text = re.sub(r'^```(?:json)?\s*', '', text, flags=re.MULTILINE)
        text = re.sub(r'\s*```$', '', text, flags=re.MULTILINE)
        # 提取第一个 {...} JSON 对象（容忍前后说明文字）
        m = re.search(r'\{.*\}', text, re.DOTALL)
        if not m:
            raise ValueError(f"模型输出中未找到 JSON 对象: {raw[:200]}")
        data = json.loads(m.group(0))

        intent = str(data.get("intent", "chat")).strip().lower()
        if intent not in _INTENTS:
            raise ValueError(f"非法意图值: {intent!r}")

        try:
            confidence = float(data.get("confidence", 0.5))
        except (TypeError, ValueError):
            confidence = 0.5
        confidence = max(0.0, min(1.0, confidence))
        return intent, confidence
