"""
Search Agent - 直接调用 Tavily（不走 MCP）
负责旅游攻略搜索和内容生成
"""
import json
from datetime import datetime
from typing import List, Dict, Any
from MCP.core.logger import get_logger
from MCP.core.llm_client import LLMClientFactory
from langchain_tavily import TavilySearch
from MCP.config.settings import settings

logger = get_logger(__name__)


def _coerce_text(value) -> str:
    """None / list / dict / str 统一成文本，避免把字面 "None" 写进攻略（P1-10）"""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return "\n".join(_coerce_text(v) for v in value if v is not None)
    if isinstance(value, dict):
        for key in ("answer", "content", "text", "summary"):
            if value.get(key):
                return _coerce_text(value[key])
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def _normalize_tavily(response) -> tuple:
    """兼容 Tavily 返回 dict / list 两种形态，返回 (answer, sources)"""
    if isinstance(response, (list, tuple)):
        answers, items = [], []
        for part in response:
            a, r = _normalize_tavily(part)
            answers.append(a)
            items.extend(r)
        return "\n".join(x for x in answers if x), items
    if not isinstance(response, dict):
        return _coerce_text(response), []

    answer = _coerce_text(response.get("answer"))
    raw_results = response.get("results") or []
    if isinstance(raw_results, dict):
        raw_results = list(raw_results.values())
    sources = []
    for item in raw_results[:5]:
        if not isinstance(item, dict):
            continue
        sources.append({
            "title": _coerce_text(item.get("title")),
            "url": _coerce_text(item.get("url")),
            "content": _coerce_text(item.get("content"))[:500],
            "score": item.get("score", 0),
        })
    return answer, sources


class AgentResponse:
    """统一返回格式"""
    def __init__(self, success: bool, content: str = "", metadata: dict = None, error: str = ""):
        self.success = success
        self.content = content
        self.metadata = metadata or {}
        self.error = error


class Search:
    """
    搜索智能体 - 直接调用 Tavily API，不走 MCP
    流程：搜索 → LLM 生成攻略 → 验证 → 返回
    """

    def __init__(self):
        logger.info("✅ 初始化 SearchAgent（直接调用 Tavily，不走 MCP）")
        self.tavily_client = TavilySearch(
            api_key=settings.TAVILY_API_KEY,
            max_results=settings.TAVILY_MAX_RESULTS,
        )
        self.llm = LLMClientFactory.create_chatter()

    async def search(self, destination: str) -> AgentResponse:
        """执行旅游搜索并生成攻略"""
        try:
            logger.info(f"🔍 开始生成 {destination} 的景点信息")

            # --- 步骤 A: 直接调用 Tavily 搜索 ---
            queries = [
                f"{destination}必去景点 门票价格 开放时间 2025 2026",
                f"{destination}特色美食 餐厅推荐 人均消费",
                f"{destination}住宿区域 酒店民宿 价格",
                f"{destination}最佳旅游季节 气候 穿衣建议",
                f"{destination}旅游注意事项 避坑 预约",
                f"{destination}经典行程 3日游 路线规划",
            ]

            all_results = []
            for query in queries:
                try:
                    response = self.tavily_client.invoke(query)
                    answer, sources = _normalize_tavily(response)
                    all_results.append({
                        "query": query,
                        "answer": answer,
                        "sources": sources,
                    })
                except Exception as e:
                    logger.warning(f"查询失败: {query}, 错误: {e}")

            # 组装搜索文本（时间动态生成，不再硬编码 2026年5月）
            search_month = datetime.now().strftime("%Y年%m月")
            raw_text = f"""【目的地】{destination}
【搜索时间】{search_month}
【信息来源】Tavily搜索引擎

"""
            for idx, result in enumerate(all_results, 1):
                raw_text += f"""--- 搜索结果 {idx} ---
查询: {result['query']}
摘要: {result['answer']}

详细来源:
"""
                for source in result['sources']:
                    raw_text += f"- [{source['title']}]({source['url']}): {source['content'][:200]}..."
                raw_text += ""

            # --- 步骤 B: LLM 生成攻略 ---
            prompt = f"""你是一个专业的旅游攻略(只查旅游地信息，不做路线规划)撰写专家。
根据以下从互联网搜索到的关于"{destination}"的原始信息，整理出一份完整、实用、结构清晰的旅游攻略。

【原始搜索数据】
{raw_text[:4000]}

【输出要求】
请直接输出以下格式的旅游攻略（不要添加"以下是攻略"等废话，直接开始正文）：

# {destination}旅游攻略

## 🎯 必去景点（Top 4-6个）
每个景点包含：
- 景点名称
- 50字特色描述
- 建议游玩时长
- 门票参考（如有）

## 💡 景点避坑
针对上述景点，给出3-5条实用的避坑建议

## 🍜 必吃美食（Top 4-6个）
每个美食包含：
- 美食名称
- 特色描述
- 推荐店铺/街区
- 人均消费参考

## 🏨 住宿推荐（3-4个区域/酒店）
包含：
- 区域名称
- 适合人群
- 价格区间
- 优势理由

## 🌤️ 最佳旅游时间
包含：
- 推荐月份及理由
- 需要避开的时段
- 当地气候特点

## 💡 实用贴士（3-5条）
- 省钱技巧
- 预约建议
- 当地礼仪/禁忌

## 📅 经典3日游路线（可选）
### Day 1：
- 上午：...
- 下午：...
- 晚上：...

### Day 2：...

*免责声明：以上信息来源于互联网公开数据，价格、营业时间等可能变动，出行前请再次确认。*
"""

            logger.info("📝 调用 LLM 生成旅游攻略...")
            article = await self.llm.ainvoke(prompt)
            logger.info(f"✅ LLM 生成完成，长度: {len(article)} 字符")

            # --- 步骤 C: 格式化来源 ---
            all_sources = []
            for result in all_results:
                for source in result.get("sources", []):
                    all_sources.append({
                        "title": source.get("title", ""),
                        "url": source.get("url", "")
                    })

            if all_sources:
                sources_text = "\n\n## 📚 参考来源\n"
                for idx, s in enumerate(all_sources[:10], 1):
                    sources_text += f"{idx}. [{s['title']}]({s['url']})\n"
                today = datetime.now().strftime("%Y-%m-%d")
                sources_text += f"*整理时间：{today} | 本攻略信息仅供参考，出行前请再次确认*"
                article += sources_text

            return AgentResponse(
                success=True,
                content=article,
                metadata={
                    "destination": destination,
                    "query_count": len(queries),
                    "article_length": len(article)
                }
            )

        except Exception as e:
            logger.error(f"搜索生成攻略失败: {str(e)}")
            return self._fallback_response(destination, str(e))

    def _fallback_response(self, destination: str, error: str = "") -> AgentResponse:
        """降级方案：明确标记为失败态 + 非实时数据，不再伪装成成功攻略（P1-10）"""
        msg = (
            f"# {destination} 旅游信息\n\n"
            f"⚠️ **以下为通用建议，非实时搜索数据**\n\n"
            f"💡 您可以尝试以下方向：\n"
            f"  - 🔍 **景点**：{destination}的必去景点、门票价格\n"
            f"  - 🍜 **美食**：{destination}的特色美食、推荐餐厅\n"
            f"  - 🏨 **住宿**：{destination}的住宿区域推荐\n"
            f"  - 🌤️ **最佳时间**：{destination}的最佳旅游季节\n\n"
        )
        if error:
            msg += f"⚠️ 提示：搜索服务暂时不可用（{error}），请稍后重试。\n"

        return AgentResponse(
            success=False,
            content=msg,
            metadata={"destination": destination, "fallback": True, "data_quality": "unavailable"},
            error=error or "搜索服务不可用，未获取到实时数据",
        )