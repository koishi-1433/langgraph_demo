"""graph.py 辅助方法单元测试（无需外部依赖与网络）。"""
import asyncio

import pytest

from langgraph_demo.workflow.graph import TravelPlanWorkflow


def _parse(user_input: str):
    wf = TravelPlanWorkflow()
    return asyncio.run(wf._parse_location_info(user_input))


class TestParseLocationInfo:
    def test_from_a_to_b(self):
        assert _parse("从北京到上海怎么走") == ("北京", "上海")

    def test_go_to_city(self):
        assert _parse("去西安") == ("", "西安")

    def test_dash_separated(self):
        assert _parse("北京-上海") == ("北京", "上海")

    def test_plain_city(self):
        assert _parse("上海") == ("", "上海")

    def test_gps_prefix_as_origin_fallback(self):
        assert _parse("[当前位置:北京] 去西安怎么走") == ("北京", "西安")

    def test_gps_prefix_keeps_explicit_origin(self):
        assert _parse("[当前位置:北京] 从天津到上海") == ("天津", "上海")

    def test_app_location_prefix_fallback(self):
        # app.py 生成的 [位于xx] 前缀：无明确起点时作为起点兜底
        assert _parse("[位于北京市东城区] 去天安门怎么走") == ("北京市东城区", "天安门")

    def test_app_location_prefix_does_not_override_explicit_origin(self):
        # 用户明确起点（北京/天津）优先，定位前缀不得混入起点
        assert _parse("[位于张家口市下花园区] 从北京到上海怎么走") == ("北京", "上海")
        assert _parse("[位于北京市东城区] 从天津到上海") == ("天津", "上海")

    def test_destination_truncated_at_comma(self):
        # 城市名后逗号是补充说明 → 截断（无分隔符输入的语义提炼由 LLM 负责，见 TestExtractDestination）
        assert _parse("北京到上海的路线，顺便推荐旅游攻略") == ("北京", "上海")
        assert _parse("去西安旅游，帮我规划一下") == ("", "西安旅游")


class TestCleanFinalOutput:
    def test_removes_markdown_noise(self):
        raw = "## 标题\n\n**加粗**内容\n\n```python\ncode\n```\n\n\n\n尾部"
        out = TravelPlanWorkflow._clean_final_output(raw)
        assert "#" not in out
        assert "**" not in out
        assert "```" not in out
        assert "\n\n\n" not in out
        assert "标题" in out and "加粗" in out and "尾部" in out

    def test_keeps_plain_text(self):
        assert TravelPlanWorkflow._clean_final_output("你好，世界") == "你好，世界"


class TestToAgentHistory:
    def test_converts_roles(self):
        hist = [{"role": "user", "content": "a"}, {"role": "assistant", "content": "b"}]
        out = TravelPlanWorkflow._to_agent_history(hist)
        assert out == [("human", "a"), ("ai", "b")]

    def test_empty_inputs(self):
        assert TravelPlanWorkflow._to_agent_history(None) == []
        assert TravelPlanWorkflow._to_agent_history([]) == []

    def test_limits_to_recent(self):
        hist = [{"role": "user", "content": str(i)} for i in range(5)]
        out = TravelPlanWorkflow._to_agent_history(hist, limit=2)
        assert out == [("human", "3"), ("human", "4")]


class TestValidateTools:
    def test_all_present_passes(self):
        tool_map = {
            "tavily_web_search": object(),
            "amap_geocode": object(),
            "amap_regeocode": object(),
            "amap_route_planning": object(),
            "py12306_query_train_schedule": object(),
        }
        TravelPlanWorkflow._validate_tools(tool_map)  # 不应抛异常

    def test_missing_raises(self):
        tool_map = {"tavily_web_search": object()}
        with pytest.raises(RuntimeError, match="MCP 工具加载不完整"):
            TravelPlanWorkflow._validate_tools(tool_map)


class TestFailureOutput:
    def test_unified_format(self):
        wf = TravelPlanWorkflow()
        state = {"execution_path": ["intent_recognition"]}
        out = wf._failure_output(state, "search", "超时")
        assert out["error"] == "抱歉，攻略搜索失败：超时"
        assert out["final_output"] == out["error"]
        assert out["execution_path"] == ["intent_recognition", "search"]


class TestCleanDestination:
    def test_multiple_cities_takes_first(self):
        assert TravelPlanWorkflow._clean_destination("北京, 上海") == "北京"
        assert TravelPlanWorkflow._clean_destination("北京、上海") == "北京"
        assert TravelPlanWorkflow._clean_destination("北京 上海") == "北京"

    def test_strips_quotes_and_whitespace(self):
        assert TravelPlanWorkflow._clean_destination('"西安"') == "西安"
        assert TravelPlanWorkflow._clean_destination("  成都  ") == "成都"

    def test_unknown_values_return_empty(self):
        assert TravelPlanWorkflow._clean_destination("未知") == ""
        assert TravelPlanWorkflow._clean_destination("null") == ""
        assert TravelPlanWorkflow._clean_destination("None") == ""

    def test_single_char_or_empty(self):
        assert TravelPlanWorkflow._clean_destination("") == ""
        assert TravelPlanWorkflow._clean_destination(None) == ""
        assert TravelPlanWorkflow._clean_destination("北") == ""


class _FakeChatterLLM:
    """mock chatter_llm：记录是否被调用，返回预设响应或抛异常。"""

    def __init__(self, response: str = None, fail: bool = False):
        self._response = response
        self._fail = fail
        self.called = False

    async def ainvoke(self, prompt: str) -> str:
        self.called = True
        if self._fail:
            raise RuntimeError("mock llm failure")
        return self._response


class TestExtractDestination:
    def _wf(self, llm):
        wf = TravelPlanWorkflow()
        wf.chatter_llm = llm
        return wf

    def test_rule_hit_takes_endpoint_without_llm(self):
        # "从北京到上海怎么走"：规则命中，直接取终点，不调 LLM
        llm = _FakeChatterLLM(response="上海")
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("从北京到上海怎么走")) == "上海"
        assert not llm.called

    def test_dash_rule_hit(self):
        llm = _FakeChatterLLM(response="上海")
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("北京-上海")) == "上海"
        assert not llm.called

    def test_llm_extracts_free_text(self):
        # 无分隔符表述：走 LLM
        llm = _FakeChatterLLM(response="西安")
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("去西安旅游帮我规划一下")) == "西安"
        assert llm.called

    def test_llm_multiple_cities_cleaned(self):
        llm = _FakeChatterLLM(response="北京, 上海")
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("去北京上海旅游")) == "北京"

    def test_llm_failure_falls_back_to_rule(self):
        # LLM 异常 → 规则兜底（"去西安" 规则解析出 西安）
        llm = _FakeChatterLLM(fail=True)
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("去西安")) == "西安"

    def test_llm_unknown_falls_back_to_rule(self):
        llm = _FakeChatterLLM(response="未知")
        wf = self._wf(llm)
        assert asyncio.run(wf._extract_destination("去西安")) == "西安"
