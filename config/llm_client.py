"""
大模型客户端工具类：
====================

职责分离（符合单一职责原则）：
- '模型的连接'（LLMClient）：封装 ChatOpenAI 的同步/异步/流式调用，统一接口；
- '模型的配置'（LLMClientFactory）：按用途创建带不同参数（temperature、
  max_tokens）的 LLMClient，各 Agent 的"模型调教参数"集中在这里维护。

注意：provider="openai" 与 "nvidia" 目前都读取 NVIDIA_* 配置（兼容命名），
CHATGPT_FREE_* / DEEPSEEK_* 为预留，未在此接入。
"""
from typing import Optional

from langchain_openai import ChatOpenAI
from tenacity import retry, wait_random_exponential, stop_after_attempt

from .settings import settings
from .logger import get_logger

logger = get_logger(__name__)


class LLMClient:
    """大模型客户端统一接口：屏蔽底层 ChatOpenAI 细节，提供 invoke/ainvoke/astream。"""

    def __init__(self,
                 provider: str = "openai",
                 temperature: float = 0.6,
                 max_tokens: Optional[int] = None
                 ):
        """初始化 LLM 客户端。

        Args:
            provider: 服务商标识，当前支持 "openai" / "nvidia"（两者均读取 NVIDIA_* 配置，
                保留两个名字是为了向前兼容切换服务商的场景）。
            temperature: 采样温度。越低越确定（意图识别 0.3），越高越发散（生成 0.7）。
            max_tokens: 单次生成的最大 token 数；None 表示使用模型默认上限。

        Raises:
            ValueError: provider 不在支持列表中。
        """
        self.provider = provider.lower()
        self.temperature = temperature
        self.max_tokens = max_tokens

        # openai 与 nvidia 都走 ChatOpenAI 协议，且共用 NVIDIA_* 配置（见模块 docstring）
        if self.provider in ("openai", "nvidia"):
            self.model = settings.NVIDIA_MODEL_NAME
            self.llm = ChatOpenAI(
                model=self.model,
                temperature=temperature,
                max_tokens=max_tokens,
                api_key=settings.NVIDIA_API_KEY,
                base_url=settings.NVIDIA_BASE_URL,
            )
        else:
            raise ValueError(f"不支持的提供商：{provider}")

    def invoke(self, prompt: str) -> str:
        """同步调用 LLM，返回模型生成的文本。失败抛异常（由调用方决定兜底策略）。"""
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
        """异步调用 LLM（带指数退避重试：1~60s 随机等待，最多 3 次）。

        重试适用于临时性故障（限流/网络抖动）；业务性错误由上层语义判断。
        """
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
        异步流式调用 LLM，逐片段产出文本。

        Args:
            prompt: 提示词

        Yields:
            生成的文本片段（可能包含空白，调用方按需过滤）
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
    """LLM 客户端工厂：集中定义各用途的模型参数（temperature / max_tokens）。"""

    @staticmethod
    def create_intent_recognizer() -> LLMClient:
        """创建意图识别模型：低温（0.3）保证分类稳定，token 上限小（500）足够输出 JSON。"""
        return LLMClient(provider="openai", temperature=0.3, max_tokens=500)

    @staticmethod
    def create_search() -> LLMClient:
        """创建搜索模型：中高温（0.7）兼顾信息整理质量与多样性，长输出（5000）。"""
        return LLMClient(provider="openai", temperature=0.7, max_tokens=5000)

    @staticmethod
    def create_chatter() -> LLMClient:
        """创建聊天/规划模型：中高温（0.7）使回复自然，长输出（5000）承载完整方案。"""
        return LLMClient(provider="openai", temperature=0.7, max_tokens=5000)
