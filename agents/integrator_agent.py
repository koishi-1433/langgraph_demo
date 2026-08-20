"""整合 Agent — 无工具，仅用 LLM 将攻略 + 路线 整合为自然语言旅行方案"""
from ..config.llm_client import LLMClient
from .base_agent import BaseAgent

INTEGRATOR_SYSTEM_PROMPT = """\
你是一位专业的旅行规划师助手。你的任务是将"旅游攻略"和"交通路线"两股信息
整合成一份完整、连贯、实用的旅行总方案。

【要求】
1. 按时序组织：先写交通方式（怎么到达目的地），再写景点游玩攻略
2. 如果交通信息中有具体车次和时间，嵌入行程描述中
3. 语气亲切自然，像朋友推荐
4. 纯文本格式，不要用 ```json 或 ```markdown 包裹
5. 控制在一千五百字以内
6. 如果路线信息标注了"仅供参考"，明确告知用户这是估算数据
7. 开头写一个简短的引导语（不超过一句），直接进入主题"""


class IntegratorAgent(BaseAgent):
    """整合 Agent：无工具，仅用 LLM 将攻略 + 路线整合为自然语言旅行方案。

    在 travel 节点中消费 search 与 route 两个子任务的输出，
    按时序（先交通后景点）合并为一份连贯、实用的总方案。
    """

    def __init__(self, llm_client: LLMClient):
        super().__init__(
            llm_client=llm_client,
            tools=[],
            system_prompt=INTEGRATOR_SYSTEM_PROMPT,
            name="integrator_agent",
        )
