"""IntentAgent 单元测试：mock LLMClient.ainvoke 返回 JSON 字符串，无网络依赖。"""
import asyncio

from langgraph_demo.agents.intent_agent import IntentAgent


class FakeLLMClient:
    def __init__(self, response: str = None, fail: bool = False):
        self._response = response
        self._fail = fail
        self.last_prompt = None

    async def ainvoke(self, prompt: str) -> str:
        self.last_prompt = prompt
        if self._fail:
            raise RuntimeError("mock failure")
        return self._response


def test_recognize_success():
    agent = IntentAgent(llm_client=FakeLLMClient(response='{"intent": "travel", "confidence": 0.92}'))
    resp = asyncio.run(agent.recognize("我想去西安旅游"))
    assert resp.success
    assert resp.content == {"intent": "travel", "confidence": 0.92}


def test_recognize_with_code_fence():
    agent = IntentAgent(llm_client=FakeLLMClient(response='```json\n{"intent": "route", "confidence": 0.85}\n```'))
    resp = asyncio.run(agent.recognize("北京到上海怎么走"))
    assert resp.success
    assert resp.content["intent"] == "route"
    assert resp.content["confidence"] == 0.85


def test_recognize_noise_around_json():
    agent = IntentAgent(llm_client=FakeLLMClient(response='好的，分析如下：{"intent": "search", "confidence": 0.9} 完毕'))
    resp = asyncio.run(agent.recognize("西安有什么好玩的"))
    assert resp.success
    assert resp.content["intent"] == "search"


def test_recognize_fallback_on_error():
    agent = IntentAgent(llm_client=FakeLLMClient(fail=True))
    resp = asyncio.run(agent.recognize("你好"))
    assert not resp.success
    assert resp.content == {"intent": "chat", "confidence": 0.5}


def test_invalid_intent_falls_back():
    agent = IntentAgent(llm_client=FakeLLMClient(response='{"intent": "hack", "confidence": 0.9}'))
    resp = asyncio.run(agent.recognize("hi"))
    assert not resp.success
    assert resp.content == {"intent": "chat", "confidence": 0.5}


def test_no_json_falls_back():
    agent = IntentAgent(llm_client=FakeLLMClient(response="完全不是 JSON"))
    resp = asyncio.run(agent.recognize("hi"))
    assert not resp.success
    assert resp.content == {"intent": "chat", "confidence": 0.5}


def test_confidence_clamped():
    agent = IntentAgent(llm_client=FakeLLMClient(response='{"intent": "search", "confidence": 2.5}'))
    resp = asyncio.run(agent.recognize("西安美食推荐"))
    assert resp.success
    assert resp.content["confidence"] == 1.0


def test_prompt_contains_user_input():
    llm = FakeLLMClient(response='{"intent": "chat", "confidence": 0.5}')
    agent = IntentAgent(llm_client=llm)
    asyncio.run(agent.recognize("今天天气怎么样"))
    assert "今天天气怎么样" in llm.last_prompt
