"""
意图识别智能体
分析user_input,输出WorkflowState中定义的intent（chat or workflow_1 or workflow_2）
"""
import json
import re

from MCP.core.llm_client import LLMClientFactory
from MCP.core.logger import get_logger
from MCP.workflow.state import AgentResponse

logger = get_logger(__name__)

# 只匹配「标签行」，不再对整段响应做子串匹配（P1-8）。
# 否则 LLM 解释里出现 "该输入不含 travel 意图" 也会被判成 travel。
# 兼容：中文/英文冒号、Markdown 加粗、intent/confidence 英文标签、JSON 输出。
_VALID_INTENTS = ("chat", "route", "search", "travel")
_INTENT_RE = re.compile(
    r"(?:^|\n)\s*\**\s*(?:意图|intent)\s*\**\s*[:：]\s*\**\s*(chat|route|search|travel)\b",
    re.IGNORECASE,
)
_CONFIDENCE_RE = re.compile(
    r"(?:置信度|confidence)\s*[:：]\s*\**\s*([0-9]*\.?[0-9]+)",
    re.IGNORECASE,
)


def _parse_intent_response(response_text: str):
    """从 LLM 响应中解析 (intent, confidence)。

    解析失败一律保守降级为 ("chat", 0.5)：宁可走简单对话，也不要因为
    响应里的解释文字误启动复杂工作流。
    """
    raw = response_text or ""
    intent = None
    confidence = None

    m = _INTENT_RE.search(raw)
    if m:
        intent = m.group(1).lower()

    # 兼容 LLM 直接返回 JSON 的情况
    if intent is None:
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                cand = str(data.get("intent", "")).strip().lower()
                if cand in _VALID_INTENTS:
                    intent = cand
                raw_conf = data.get("confidence")
                if raw_conf is not None:
                    confidence = float(raw_conf)
        except (ValueError, TypeError):
            pass

    cm = _CONFIDENCE_RE.search(raw)
    if cm:
        try:
            confidence = float(cm.group(1))
        except ValueError:
            confidence = None

    if confidence is not None:
        if confidence > 1:  # 兼容 "95" 表示 95%
            confidence /= 100
        confidence = min(1.0, max(0.0, confidence))

    if intent not in _VALID_INTENTS:
        logger.warning(f"意图解析失败，保守降级为 chat；LLM 原始响应: {raw[:120]!r}")
        return "chat", 0.5

    if confidence is None:
        confidence = 0.7 if intent in ("travel", "route") else 0.6 if intent == "search" else 0.5
    return intent, confidence


class IntentRecognition:
    """
    意图识别智能体：
        负责判断用户意图是普通对话（chat）、route（纯出行）、search（纯景点查询）还是 travel（旅游规划）
    """

    def __init__(self):
        """"初始化意图识别器"""
        # 使用工厂模式创建专门用于意图识别的客户端（低温度，保证稳定性，即：尽量把保证每次AI回答一致）
        self.llm = LLMClientFactory.create_intent_recognizer()
        logger.info("初始化意图识别智能体")

    async def recognize(self, user_input: str) -> AgentResponse:
        """
        识别用户意图
        Args：
            user_input:str 用户输入
        Returns:
            意图识别结果
        """
        try:
            logger.info(f"识别用户意图：{user_input[:50]}...")

            prompt = """请分析用户输入，判断用户的意图类型。

            【意图定义】
            1. chat: 普通对话、闲聊、知识问答、简单信息查询（不涉及具体地点间的出行或旅游）
               - 示例："你好"、"今天天气怎么样"、"什么是量子计算"

            2. route: 纯出行规划（从A地到B地的交通路线，不涉及景点、游玩）
               - 特征：明确提到从A到B，或"怎么去"、"怎么走"、"导航"、"距离"、"多远"、"多少公里"、"路程"
               - 交通方式：短距离（公交/地铁/打车），长距离（高铁/火车/飞机）
               - 关键：无旅游、游玩、景点等词汇
               - 示例："从北京到上海怎么去"、"从公司到机场怎么走"、"去天津的高铁有哪些"、"到上海的距离"、"北京多远"
               - 注意："到北京的路线"、"去上海的路线"等纯问路的均属于route，不是travel

            3. search: 纯景点/旅游地信息查询（只查信息，不做路线规划）
               - 特征：询问某地有什么好玩的、著名景点、美食推荐
               - 关键：无"怎么去"、"从A到B"等出行词汇，或出行不是重点
               - 示例："西安有什么著名景点"、"成都美食推荐"、"杭州好玩的地方"

            4. travel: 旅游规划（需要路线+景点的完整方案）
               - 特征：明确表达旅游、游玩意图，包含目的地，需要制定旅游规划
               - 关键：必须同时有出行需求 和 目的地游玩需求
               - 示例："我想去西安旅游帮我规划一下"、"去成都玩三天怎么安排"、"春节去云南旅游"

            【判断规则（严格按顺序判断）】
            1️⃣ 优先检查是否包含"旅游、游玩、玩、攻略、行程安排"等明确旅游词
               - 有旅游词 + 有目的地 → travel
               - ⚠️ 仅有"路线"、"怎么去"等出行词，没有旅游词 → 不是travel
            2️⃣ 含"怎么去、从A到B、路线、导航" + 无旅游词 → route
            3️⃣ 含"有什么好玩的、景点、美食、推荐" + 无出行需求 → search
            4️⃣ 仅问候、知识问答、无地点 → chat

            【边界情况】
            - "到北京的路线" → route（纯问路，没有旅游/游玩意图）
            - "北京到上海的路线，顺便推荐旅游攻略" → travel（既有路线又有攻略）
            - "去北京" → 仅看上下文，优先 chat（信息不足时不猜travel）

            用户输入: {user_input}

            格式：
            意图: [intent]
            置信度: [confidence]"""

            # 获取llm响应
            response_text = await self.llm.ainvoke(prompt.format(user_input=user_input))

            # 解析响应：只取标签行的 intent / confidence，不再全文子串匹配（P1-8）
            intent, confidence = _parse_intent_response(response_text)

            logger.info(f"✓ 意图识别完成: {intent} (置信度: {confidence})")

            # 封装 Agent 响应
            return AgentResponse(
                success=True,
                content={
                    "intent": intent,
                    "confidence": confidence
                },
                metadata={"user_input": user_input[:50]}
            )


        except Exception as e:
            logger.error(f"意图识别失败：{str(e)}")
            return AgentResponse(
                success=False,
                error=str(e),
                content={"intent": "chat", "intent_confidence": 0.3}
            )
