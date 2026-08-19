"""BaseAgent 单元测试：monkeypatch create_react_agent，验证 run() 与历史拼接。"""
import asyncio

from langgraph_demo.agents.base_agent import BaseAgent


class FakeLLM:
    pass


class FakeLLMClient:
    def __init__(self):
        self.llm = FakeLLM()


class FakeReactAgent:
    def __init__(self, fail: bool = False):
        self.last_input = None
        self.last_config = None
        self.fail = fail

    async def ainvoke(self, input, config):
        self.last_input = input
        self.last_config = config
        if self.fail:
            raise RuntimeError("boom")
        last = type("Msg", (), {"content": "final answer"})()
        return {"messages": [last]}


def _make_agent(monkeypatch, fail: bool = False):
    fake = FakeReactAgent(fail=fail)
    monkeypatch.setattr(
        "langgraph_demo.agents.base_agent.create_react_agent",
        lambda **kwargs: fake,
    )
    agent = BaseAgent(llm_client=FakeLLMClient(), tools=[], system_prompt="p", name="test_agent")
    return agent, fake


def test_run_success_with_history(monkeypatch):
    agent, fake = _make_agent(monkeypatch)
    resp = asyncio.run(agent.run("hi", history=[("human", "hello"), ("ai", "world")]))
    assert resp.success
    assert resp.content == "final answer"
    # 历史在前，当前问题在后
    assert fake.last_input["messages"] == [("human", "hello"), ("ai", "world"), ("human", "hi")]
    # recursion_limit 兜底存在
    assert fake.last_config["recursion_limit"] == 15


def test_run_without_history(monkeypatch):
    agent, fake = _make_agent(monkeypatch)
    resp = asyncio.run(agent.run("hi"))
    assert resp.success
    assert fake.last_input["messages"] == [("human", "hi")]


def test_run_failure_returns_response(monkeypatch):
    agent, _ = _make_agent(monkeypatch, fail=True)
    resp = asyncio.run(agent.run("hi"))
    assert not resp.success
    assert "boom" in resp.error
