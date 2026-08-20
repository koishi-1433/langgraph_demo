"""
大模型客户端工具类：
分离：（符合单一职责原则）
    '模型的连接'（LLMClient）和 '模型的配置'（LLMClientFactory）
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from tenacity import retry, wait_random_exponential, stop_after_attempt

from .settings import settings
from .logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """大模型客户端统一接口"""

    def __init__(self,
                 provider: str = "openai",
                 model: Optional[str] = None,
                 temperature: float = 0.6,
                 max_tokens: Optional[int] = None
                 ):
        self.provider = provider.lower()
        self.temperature = temperature
        self.max_tokens = max_tokens

        if self.provider == "openai":
            self.model = settings.NVIDIA_MODEL_NAME
            self.llm = ChatOpenAI(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=settings.NVIDIA_API_KEY,
                base_url=settings.NVIDIA_BASE_URL,
            )
        elif self.provider == "nvidia":
            self.model = settings.NVIDIA_MODEL_NAME
            self.llm = ChatOpenAI(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=settings.NVIDIA_API_KEY,
                base_url=settings.NVIDIA_BASE_URL,
            )
            logger.info(f"初始化NVIDIA客户端，模型：{self.model}")
        else:
            raise ValueError(f"不支持的提供商：{provider}")

    def invoke(self, prompt: str) -> str:
        """同步调用LLM"""
        try:
            logger.debug(f"调用{self.provider}模型：{self.model}")
            response = self.llm.invoke(prompt)
            content = response.content
            logger.debug(f"模型响应长度: {len(content)}字符")
            return content
        except Exception as e:
            logger.error(f"LLM调用失败：{str(e)}")
            raise

    @retry(wait=wait_random_exponential(min=1, max=60), stop=stop_after_attempt(3))
    async def ainvoke(self, prompt: str) -> str:
        """异步调用LLM"""
        try:
            logger.debug(f"异步调用{self.provider}模型：{self.model}")
            response = await self.llm.ainvoke(prompt)
            content = response.content
            logger.debug(f"模型响应长度: {len(content)}字符")
            return content
        except Exception as e:
            logger.error(f"LLM调用失败：{str(e)}")
            raise

    async def astream(self, prompt: str):
        """
        异步流式调用LLM

        Args:
            prompt: 提示词

        Yields:
            生成的文本片段
        """
        try:
            logger.debug(f"流式调用{self.provider}模型{self.model}")
            async for chunk in self.llm.astream(prompt):
                if hasattr(chunk, 'content') and chunk.content:
                    yield chunk.content
        except Exception as e:
            logger.error(f"LLM流式调用失败：{str(e)}")
            raise

class LLMClientFactory:
    """LLM客户端工厂"""

    @staticmethod
    def create_intent_recognizer() -> LLMClient:
        """创建意图识别模型"""
        return LLMClient(provider="openai", temperature=0.3, max_tokens=500)

    @staticmethod
    def create_search() -> LLMClient:
        """创建搜索模型"""
        return LLMClient(provider="openai", temperature=0.7, max_tokens=5000)

    @staticmethod
    def create_chatter() -> LLMClient:
        """创建聊天模型"""
        return LLMClient(provider="openai", temperature=0.7, max_tokens=5000)
