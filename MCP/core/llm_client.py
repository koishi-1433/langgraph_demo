"""
大模型客户端工具类：
分离：（符合单一职责原则）
    '模型的连接'（LLMClient）和 '模型的配置'（LLMClientFactory）

全项目唯一的 LLM 出口：所有 Agent / 工作流都通过 LLMClientFactory 取得客户端，
底层统一使用 .env 中的 CHATGPT_FREE_API_KEY（chatanywhere，OpenAI 兼容协议）。
更换模型或服务商只需改 .env 的三个 CHATGPT_FREE_* 变量，无需改业务代码。
"""
from typing import Optional, List

from langchain_core.messages import BaseMessage
from langchain_openai import ChatOpenAI
from tenacity import retry, wait_random_exponential, stop_after_attempt

from MCP.config.settings import settings
from MCP.core.logger import get_logger

logger = get_logger(__name__)


def _normalize_base_url(url: str) -> str:
    """
    规范化 OpenAI 兼容端点。

    ChatOpenAI 会在 base_url 后直接拼接 /chat/completions，
    因此必须保证末尾带 /v1；兼容 .env 中只写域名根路径的情况。
    """
    base = (url or "").strip().rstrip("/")
    if not base:
        raise ValueError("CHATGPT_FREE_BASE_URL 为空，请在 .env 中配置")
    if not base.startswith(("http://", "https://")):
        raise ValueError(f"CHATGPT_FREE_BASE_URL 不是合法 URL: {base}")
    if not base.endswith("/v1"):
        base += "/v1"
    return base


class LLMClient:
    """大模型客户端统一接口（ChatGPT Free / OpenAI 兼容）"""

    # 历史代码曾传入 "openai" / "dashscope" / "myapi"，统一视为同一服务，避免旧调用点报错
    _PROVIDER_ALIASES = {"chatgpt_free", "chatgpt", "free", "openai", "dashscope", "myapi"}

    def __init__(self,
                 provider: str = "chatgpt_free",
                 model: Optional[str] = None,
                 temperature: float = 0.6,
                 max_tokens: Optional[int] = None
                 ):
        self.provider = provider.lower()
        self.temperature = temperature
        self.max_tokens = max_tokens

        if self.provider not in self._PROVIDER_ALIASES:
            raise ValueError(f"不支持的提供商：{provider}（当前仅使用 CHATGPT_FREE_API_KEY）")

        api_key = (settings.CHATGPT_FREE_API_KEY or "").strip()
        if not api_key or api_key.lower().startswith(("your_", "sk-xxx", "changeme")):
            raise RuntimeError(
                "CHATGPT_FREE_API_KEY 未配置或仍是占位值，请在 .env 中填入有效的 ChatGPT Free 密钥"
            )

        # base_url 与 model 均来自新配置；model 允许调用点临时覆盖
        self.base_url = _normalize_base_url(settings.CHATGPT_FREE_BASE_URL)
        self.model = model or settings.CHATGPT_FREE_MODEL_NAME

        self.llm = ChatOpenAI(
            model=self.model,
            temperature=temperature,
            max_tokens=max_tokens,
            api_key=api_key,
            base_url=self.base_url,
            timeout=settings.LLM_TIMEOUT,
        )
        # 只记录模型与端点，绝不打印密钥
        logger.info(f"初始化 ChatGPT Free 客户端，模型：{self.model}，端点：{self.base_url}")

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

    def stream(self, messages: List[BaseMessage], **kwargs):
        """同步流式调用LLM（兼容旧接口）"""
        try:
            logger.debug(f"流式调用{self.provider}模型{self.model}")
            for chunk in self.llm.stream(messages, **kwargs):
                if hasattr(chunk, 'content'):
                    yield chunk.content
        except Exception as e:
            logger.error(f"LLM流式调用失败：{str(e)}")
            raise


class LLMClientFactory:
    """LLM客户端工厂（全部基于 CHATGPT_FREE_API_KEY）"""

    @staticmethod
    def create_intent_recognizer() -> LLMClient:
        return LLMClient(temperature=0.3, max_tokens=500)

    @staticmethod
    def create_search() -> LLMClient:
        return LLMClient(temperature=0.7, max_tokens=5000)

    @staticmethod
    def create_chatter() -> LLMClient:
        return LLMClient(temperature=0.7)
