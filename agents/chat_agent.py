"""闲聊 Agent — 通用对话，不授权任何工具"""
from ..config.llm_client import LLMClient
from .base_agent import BaseAgent

CHAT_SYSTEM_PROMPT = """\
你是一个友好、实用的 AI 助手。请用自然的语言回应用户，保持友善和专业。
不要假设用户有任何特定任务，根据上下文自由回答。"""


class ChatAgent(BaseAgent):
    def __init__(self, llm_client: LLMClient):
        super().__init__(
            llm_client=llm_client,
            tools=[],
            system_prompt=CHAT_SYSTEM_PROMPT,
            name="chat_agent",
        )
