"""
MCP 服务器（SSE 模式）- 使用新版 FastMCP API
提供 Prompts、Resources、Tools 通过 SSE 传输层
"""
import os
import sys
import asyncio
import math
import json
import re
from pathlib import Path
from typing import Any
from datetime import datetime

from fastmcp import FastMCP
from langchain_tavily import TavilySearch

# 路径注入
_current_file = Path(os.path.abspath(__file__))
_project_root = _current_file.parent.parent.parent
if (_project_root / ".env").exists() or (_project_root / "main.py").exists():
    project_root = str(_project_root)
else:
    project_root = _current_file
    for _ in range(5):
        project_root = project_root.parent
        if (project_root / ".env").exists() or (project_root / "main.py").exists():
            break
    project_root = str(project_root)

if project_root not in sys.path:
    sys.path.insert(0, project_root)

from MCP.config.settings import settings
from MCP.core.logger import get_logger

logger = get_logger(__name__)

# ==================== 创建 FastMCP 服务器 ====================
mcp = FastMCP(
    settings.MCP_SERVER_NAME,
    version="2.0.0-sse",
)

# 初始化 Tavily
tavily_client = TavilySearch(
    api_key=settings.TAVILY_API_KEY,
    max_results=settings.TAVILY_MAX_RESULTS,
)


# ==================== Tavily 返回值规范化（P1-10）====================

def _coerce_text(value) -> str:
    """把 None / list / dict / str 统一成可读文本，避免把字面 "None" 写进结果"""
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


def _normalize_tavily_response(response) -> tuple:
    """兼容 Tavily 返回 dict / list 两种形态，返回 (answer, sources)"""
    if isinstance(response, (list, tuple)):
        answers, items = [], []
        for part in response:
            a, r = _normalize_tavily_response(part)
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


# ==================== Prompts ====================

@mcp.prompt()
def search_prompt(destination: str, search_results: str) -> str:
    """旅游地信息整合提示词"""
    return f"""你是一个专业的旅游攻略(只查旅游地信息，不做路线规划)撰写专家。
根据以下从互联网搜索到的关于"{destination}"的原始信息，整理出一份完整、实用、结构清晰的旅游攻略。

【原始搜索数据】
{search_results}

【输出要求】
请直接输出以下格式的旅游攻略（不要添加"以下是攻略"等废话，直接开始正文）：

# {destination}旅游攻略

## 🎯 必去景点（Top 4-6个）
每个景点包含：
- 景点名称
- 50字特色描述
- 建议游玩时长
- 门票参考（如有）

##💡景点避坑**：
针对上述景点，给出3-5条实用的避坑建议（如最佳游玩时间、购票渠道、注意事项等）


## 🍜 必吃美食（Top 4-6个）
每个美食包含：
- 美食名称
- 特色描述（为什么好吃）
- 推荐店铺/街区
- 人均消费参考

## 🏨 住宿推荐（3-4个区域/酒店）
包含：
- 区域名称（如钟楼附近、大雁塔周边）
- 适合人群（情侣/家庭/背包客）
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
### Day 1：游玩路线主题（如：古都历史线、水上欢乐线）
- 上午：如：8:30 陕西历史博物馆（3小时，重点看唐三彩）
- 下午：如：14:00 大雁塔 + 大慈恩寺，16:00 大唐芙蓉园
- 晚上：如：19:00 大唐不夜城（免费，看音乐喷泉、不倒翁表演）
- 🍽️ 今日美食：如"午餐：小寨【长安大牌档】、晚餐：曲江【老陕饭庄】"

### Day 2：
...

*免责声明：以上信息来源于互联网公开数据，价格、营业时间等可能变动，出行前请再次确认。*

【整理规则】
1. 如果搜索数据中有冲突信息，选择最新、最权威的
2. 如果某维度信息不足，标注"建议进一步确认"
3. 所有价格信息标注"仅供参考，以实际为准"
4. 使用emoji让攻略更生动，但不要太花哨
5. 语言风格：亲切、实用、像朋友推荐"""


@mcp.prompt()
def route_prompt(origin: str, destination: str, mode: str) -> str:
    """路线规划提示词"""
    return f"""
你是专业的出行路线规划助手。根据以下信息生成清晰、实用的出行方案。

## 输入信息
- 起点：{origin}
- 终点：{destination}
- 交通方式：{mode}

## 输出要求
请按以下结构输出：

### 1. 推荐方案摘要
一句话概述最佳出行方式（如"建议乘坐高铁Gxxx次列车"）

### 2. 路线详情
- 预计总耗时
- 预估费用
- 换乘信息（如有）

### 3. 分步指引
每步包含：交通方式、站点/位置、预计时间、费用

### 4. 出行建议
- 最佳出发时间建议
- 注意事项（高峰期避让、购票渠道等）
- 备选方案

注意：如果不确定具体班次和价格，使用"约"字标注估算值。
"""


# ==================== Resources ====================

@mcp.resource("travel://standards", mime_type="text/plain")
def get_travel_standards() -> str:
    """旅游信息质量标准"""
    return """# 旅游信息质量标准

## 1. 信息准确性
- 景点名称必须使用官方标准名称（如"秦始皇兵马俑博物馆"而非"兵马俑"）
- 门票价格标注年份（如"2025年参考价：120元"）
- 开放时间区分淡旺季（如"旺季8:00-18:00，淡季8:30-17:30"）

## 2. 信息完整性
- 每个景点必须包含：名称、特色、时长、门票
- 每个美食必须包含：名称、特色、推荐店铺、人均
- 缺少的信息明确标注"信息待补充"，禁止编造

## 3. 实用性优先
- 推荐店铺优先选择：营业5年以上 / 大众点评4星以上 / 本地人推荐
- 价格区间用"约XX-XX元"，不用精确数字（价格波动）

## 4. 时效性标注
- 所有价格、时间信息末尾加注"（信息来源：XXX，202X年X月）"
- 涉及施工、闭馆、限流的信息必须标注确认时间

## 5. 安全与合规
- 不推荐未开发景区、私人景点
- 涉及宗教场所给出着装、礼仪提醒
- 高原/山区景点给出健康风险提示"""


# ==================== Tools ====================

@mcp.tool()
def web_search(destination: str) -> dict[str, Any]:
    """旅游信息深度搜索工具 - 获取目的地完整信息

    Args:
        destination: 目的地名称（如"西安"、"成都"）

    Returns:
        结构化搜索结果，包含景点、美食、住宿、气候、贴士等多维度信息
    """
    try:
        logger.info(f"[MCP工具] 深度搜索: {destination}")

        queries = [
            f"{destination}必去景点 门票价格 开放时间 2025 2026",
            f"{destination}特色美食 餐厅推荐 人均消费",
            f"{destination}住宿区域 酒店民宿 价格",
            f"{destination}最佳旅游季节 气候 穿衣建议",
            f"{destination}旅游注意事项 避坑 预约",
            f"{destination}经典行程 3日游 路线规划",
        ]

        all_results = []

        def _search_single(query):
            try:
                response = tavily_client.invoke(query)
                answer, sources = _normalize_tavily_response(response)
                return {
                    "query": query,
                    "answer": answer,
                    "sources": sources,
                }
            except Exception as e:
                logger.warning(f"查询失败: {query}, 错误: {e}")
                return None

        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=6) as executor:
            futures = {executor.submit(_search_single, q): q for q in queries}
            for future in as_completed(futures):
                result = future.result()
                if result:
                    all_results.append(result)

        query_order = {q: i for i, q in enumerate(queries)}
        all_results.sort(key=lambda r: query_order.get(r.get("query", ""), 999))

        search_month = datetime.now().strftime("%Y年%m月")
        combined_text = f"""【目的地】{destination}
【搜索时间】{search_month}
【信息来源】Tavily搜索引擎

"""
        for idx, result in enumerate(all_results, 1):
            combined_text += f"""--- 搜索结果 {idx} ---
查询: {result['query']}
摘要: {result['answer']}

详细来源:
"""
            for source in result['sources']:
                combined_text += f"- [{source['title']}]({source['url']}): {source['content'][:200]}..."
            combined_text += ""

        # P1-10：全部查询失败时返回明确错误态，不返回空壳假装成功
        if not all_results:
            logger.error("[MCP工具] 所有 Tavily 查询均失败，未获取到任何可核查数据")
            return {
                "success": False,
                "error": "所有搜索查询均失败，未获取到任何可核查数据",
                "destination": destination,
                "query_count": len(queries),
                "raw_text": "",
                "structured_data": [],
            }

        return {
            "success": True,
            "error": None,
            "destination": destination,
            "query_count": len(queries),
            "raw_text": combined_text,
            "structured_data": all_results
        }

    except Exception as e:
        logger.error(f"[MCP工具] 搜索失败: {str(e)}")
        return {
            "success": False,
            "destination": destination,
            "error": str(e),
            "raw_text": f"搜索 {destination} 失败: {str(e)}",
            "structured_data": []
        }


def _calculate_straight_distance(start_loc: str, end_loc: str) -> float:
    """计算两地直线距离（公里）"""
    try:
        start_lng, start_lat = map(float, start_loc.split(','))
        end_lng, end_lat = map(float, end_loc.split(','))
        start_lat_rad = math.radians(start_lat)
        end_lat_rad = math.radians(end_lat)
        delta_lat = math.radians(end_lat - start_lat)
        delta_lng = math.radians(end_lng - start_lng)
        a = (math.sin(delta_lat / 2) ** 2 +
             math.cos(start_lat_rad) * math.cos(end_lat_rad) * math.sin(delta_lng / 2) ** 2)
        c = 2 * math.asin(math.sqrt(a))
        return 6371.0 * c
    except Exception as e:
        logger.error(f"直线距离计算失败: {e}")
        return 0.0


@mcp.tool()
def route_planning(origin: str, destination: str, mode: str = "auto") -> dict[str, Any]:
    """✅【增强版】路线规划工具 - 支持驾车/公交/火车多模式，auto 智能选择

    Args:
        origin: 起点（城市名或地址）
        destination: 终点（城市名或地址）
        mode: 交通方式 auto / driving / transit / train
              auto: 根据距离智能选择（>120km 自动选火车）

    Returns:
        路线信息，包含距离、时长、费用、火车班次等
    """
    try:
        logger.info(f"[MCP工具] 路线规划: {origin} → {destination} (请求模式: {mode})")
        amap_key = settings.AMAP_API_KEY

        import asyncio
        from MCP.tool_amap.client import UnifiedAMapClient
        from MCP.tool_amap.route_planner import RoutePlanner, RoutePlanningResult

        # ✅【修复】actual_mode 在 _do_plan 外部定义，确保返回时变量存在
        actual_mode = mode
        # 持有 planner，便于在 asyncio.run 的同一事件循环内 finally 关闭所有 HTTP 会话
        planner_holder = {}

        async def _do_plan():

            nonlocal actual_mode  # ✅ 声明使用外部变量

            # ✅ 使用 RoutePlanner（包含 train_client 和 enable_train_info）
            planner = RoutePlanner(amap_key=amap_key, enable_train_info=True, enable_static_map=True)
            planner_holder["planner"] = planner

            start_loc = await planner.amap.geocode(origin)
            end_loc = await planner.amap.geocode(destination)

            if not start_loc or not end_loc:
                route_result = RoutePlanningResult()
                route_result.error = f"无法解析地址: {origin} 或 {destination}"
                return route_result

            # ✅ 检查 Py12306Client 状态（通过 RoutePlanner）
            logger.info(f"[MCP工具] Py12306Client 启用: {planner.enable_train_info}")
            logger.info(f"[MCP工具] Py12306Client 实例: {planner.train_client is not None}")

            # ✅ 如果 Py12306Client 未初始化，动态初始化
            if not planner.train_client and planner.enable_train_info:
                try:
                    from MCP.tool_amap.train_crawler import Py12306Client
                    planner.train_client = Py12306Client(cache_enabled=True, timeout=15)
                    logger.info("[MCP工具] Py12306Client 动态初始化成功")
                except Exception as e:
                    logger.warning(f"[MCP工具] Py12306Client 动态初始化失败: {e}")
                    planner.enable_train_info = False

            # ✅ auto 模式根据距离智能选择交通方式
            if mode == "auto":
                distance = _calculate_straight_distance(start_loc, end_loc)
                if distance > 120.0:
                    actual_mode = "train"
                    logger.info(f"[MCP工具] 距离 {distance:.1f}km > 120km，自动选择火车模式")
                else:
                    actual_mode = "driving"
                    logger.info(f"[MCP工具] 距离 {distance:.1f}km <= 120km，自动选择驾车模式")

            if actual_mode == "train":
                user_real_location = None
                if origin in ["当前定位", "", "我的位置"]:
                    user_real_location = start_loc

                result = await planner._plan_train_route(
                    start=origin if origin not in ["当前定位", "", "我的位置"] else "",
                    end=destination,
                    start_loc=start_loc,
                    end_loc=end_loc,
                    date=datetime.now().strftime("%Y-%m-%d"),
                    user_real_location=user_real_location
                )
                return result

            elif actual_mode == "transit":
                result = await planner._plan_transit_route(
                    start=origin, end=destination,
                    start_loc=start_loc, end_loc=end_loc,
                    strategy="0"
                )
                return result

            else:  # driving 或默认
                result = await planner.amap.driving_route(f"{start_loc}", f"{end_loc}")
                path = result.get("route", {}).get("paths", [{}])[0]
                route_result = RoutePlanningResult()
                route_result.total_distance = float(path.get("distance", 0)) / 1000
                route_result.total_duration = int(float(path.get("duration", 0)) / 60)
                route_result.total_cost = float(path.get("tolls", 0))
                route_result.transport_mode = "Driving"
                route_result.route_details = [
                    f"驾车从 {origin} 到 {destination}",
                    f"距离: {route_result.total_distance:.1f} 公里",
                    f"用时: {route_result.total_duration} 分钟",
                    f"费用: ¥{route_result.total_cost:.2f}"
                ]
                return route_result




        async def _runner():
            try:
                return await _do_plan()
            finally:
                # P1-12：aiohttp 会话必须在其所属事件循环关闭前释放
                planner = planner_holder.get("planner")
                if planner:
                    await planner.close()

        route_result = asyncio.run(_runner())

        # P1-11：把数据质量暴露给调用方，估算结果绝不伪装成成功
        quality = "failed" if route_result.error else getattr(route_result, "data_quality", "real")
        warnings = list(getattr(route_result, "warnings", []))
        if quality != "real":
            warnings.append("本次路线含估算/非实时数据，请勿据此购票")
        return {
            "success": quality == "real" and route_result.error is None,
            "route_available": route_result.error is None,
            "data_quality": quality,
            "is_realtime": quality == "real",
            "warnings": warnings,
            "origin": origin,
            "destination": destination,
            "mode": mode,
            "actual_mode": actual_mode,
            "route_info": route_result.to_dict(),
            "summary": str(route_result)
        }

    except Exception as e:
        logger.error(f"[MCP工具] 路线规划失败: {str(e)}")
        return {"success": False, "error": str(e)}


@mcp.tool()
def validate_travel_content(content: str) -> dict[str, Any]:
    """验证旅游攻略内容质量 - 基于 travel://standards

    Args:
        content: 生成的攻略内容

    Returns:
        验证结果，包含评分、问题列表、改进建议
    """
    try:
        logger.info(f"[MCP工具] 验证攻略内容，长度: {len(content)}")
        validations = []
        score = 100

        checks = {
            "has_title": ("有标题", "# " in content),
            "has_attractions": ("有景点章节", "景点" in content or "🎯" in content),
            "has_attraction_tips": ("有景点避坑", "避坑" in content),
            "has_foods": ("有美食章节", "美食" in content or "🍜" in content),
            "has_hotels": ("有住宿章节", "住宿" in content or "🏨" in content),
            "has_weather": ("有时间/气候章节", "时间" in content or "季节" in content or "🌤️" in content),
            "has_tips": ("有实用贴士", "贴士" in content or "💡" in content),
            "has_itinerary": ("有行程路线", "路线" in content or "Day" in content or "📅" in content),
            "has_disclaimer": ("有免责声明", "免责声明" in content or "仅供参考" in content),
        }

        for name, (desc, passed) in checks.items():
            validations.append({"item": desc, "passed": passed, "deduct": 0 if passed else 10})
            if not passed:
                score -= 10

        # P1-9：删除恒真的 no_fake_info（"禁止编造" not in content 几乎总为真），
        # 改为可验证的「伪造/占位痕迹检测」并加硬门槛，杜绝 90 字短文拿 95 分。
        placeholder_markers = (
            "Check 12306", "信息待补充", "4-6 hours", "300-600 CNY",
            "08:00 → 14:00", "车次: 暂无", "时间: --:-- → --:--",
        )
        section_count = len(re.findall(r"^#{1,6}\s", content, re.M))
        quality_checks = {
            "price_year": ("价格标注年份", any(year in content for year in ["2025", "2026", "年参考"])),
            "no_placeholder": ("无占位/伪造数据", not any(m in content for m in placeholder_markers)),
            "has_sources": (
                "有可核查来源",
                ("http://" in content or "https://" in content)
                and ("参考来源" in content or "来源" in content),
            ),
            "has_sections": ("章节结构完整（≥5 个标题）", section_count >= 5),
            "length_ok": ("内容长度充足（>800 字）", len(content) > 800),
            "emoji_usage": ("使用emoji", any(e in content for e in ["🎯", "🍜", "🏨", "🌤️", "💡", "📅"])),
        }

        for name, (desc, passed) in quality_checks.items():
            validations.append({"item": desc, "passed": passed, "deduct": 0 if passed else 5})
            if not passed:
                score -= 5

        score = max(0, score)
        # 长度与章节是硬门槛：短文即使凑齐关键词也不得判为通过
        hard_gates = {
            "length_ok": len(content) > 800,
            "has_sections": section_count >= 5,
            # 检出伪造/占位痕迹时一票否决，不允许靠凑关键词通过
            "no_placeholder": not any(m in content for m in placeholder_markers),
        }
        passed = score >= 70 and all(hard_gates.values())

        return {
            "score": score,
            "passed": passed,
            "validations": validations,
            "content_length": len(content),
            "section_count": section_count,
            "hard_gates": hard_gates,
            "suggestions": [] if passed else [v["item"] for v in validations if not v["passed"]],
            "standards_reference": "travel://standards"
        }

    except Exception as e:
        logger.error(f"[MCP工具] 验证失败: {str(e)}")
        return {"score": 0, "passed": False, "error": str(e), "suggestions": ["验证过程出错，建议人工检查"]}


@mcp.tool()
def format_sources(sources: list[dict]) -> str:
    """格式化参考来源 - 为攻略添加规范的来源引用

    Args:
        sources: 来源列表，每个包含 title, url

    Returns:
        格式化后的来源文本
    """
    today = datetime.now().strftime("%Y-%m-%d")
    if not sources:
        return f"*信息来源：互联网公开数据 | 整理时间：{today}*"

    lines = ["## 📚 参考来源"]
    for idx, s in enumerate(sources[:10], 1):
        title = s.get("title", "未知来源")
        url = s.get("url", "")
        lines.append(f"{idx}. [{title}]({url})")

    lines.append(f"*整理时间：{today} | 本攻略信息仅供参考，出行前请再次确认*")
    return "\n".join(lines)


# ==================== RAG 知识库工具（tool_rag 接入主程序）====================


def _rag_service():
    """延迟导入：避免 MCP Server 启动时就加载 5GB 级本地嵌入模型。"""
    from MCP.tool_rag.rag_service import get_rag_service
    return get_rag_service()


@mcp.tool()
def rag_add_document(text: str, source: str = "", title: str = "") -> dict[str, Any]:
    """把一段文本切块写入本地旅游知识库（离线 bge-m3 嵌入 + Chroma 持久化）。

    同一 source 下相同内容重复写入会自动覆盖，不会产生重复块。

    Args:
        text: 待入库文本（如一次搜索生成的旅游攻略）
        source: 来源标识，如 "tavily:西安"，用于去重与展示
        title: 展示用标题，缺省时取 source

    Returns:
        入库结果，含 chunks_added / total_chunks / persist_directory
    """
    try:
        logger.info(f"[MCP工具] RAG 入库: source={source or 'workflow'}, 长度={len(text)}")
        return _rag_service().add_document(text=text, source=source, title=title)
    except Exception as e:
        logger.error(f"[MCP工具] RAG 入库失败: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool()
def rag_query(question: str, k: int = 0) -> dict[str, Any]:
    """从本地旅游知识库做语义检索，返回相关片段与可拼接文本 rag_text。

    Args:
        question: 用户问题（用原始输入，而非城市名，检索更贴合意图）
        k: 返回片段数，0 表示使用配置 RAG_TOP_K

    Returns:
        success / hits（含 content、source、title、distance）/ rag_text / total_chunks
    """
    try:
        logger.info(f"[MCP工具] RAG 检索: {question[:80]}")
        return _rag_service().query(question=question, k=k or None)
    except Exception as e:
        logger.error(f"[MCP工具] RAG 检索失败: {e}")
        return {"success": False, "error": str(e), "hits": [], "rag_text": ""}


@mcp.tool()
def rag_stats() -> dict[str, Any]:
    """查询本地 RAG 知识库状态（是否启用、模型、向量块数量、持久化目录）。"""
    try:
        return _rag_service().stats()
    except Exception as e:
        logger.error(f"[MCP工具] RAG 状态查询失败: {e}")
        return {"success": False, "error": str(e)}


@mcp.tool()
def rag_clear(confirm: bool = False) -> dict[str, Any]:
    """清空本地 RAG 知识库（危险操作，必须显式传 confirm=true）。

    Args:
        confirm: 确认标记，False 时仅返回拒绝信息，不执行任何删除
    """
    try:
        return _rag_service().clear(confirm=confirm)
    except Exception as e:
        logger.error(f"[MCP工具] RAG 清空失败: {e}")
        return {"success": False, "error": str(e)}


# ==================== 远程 MCP 服务器（Streamable HTTP）====================

async def run_mcp_http(host: str = "0.0.0.0", port: int = 9000,
                       transport: str = "streamable-http"):
    """以 Streamable HTTP 模式运行 MCP 服务器（FastMCP 3.x）。

    默认 transport="streamable-http"，端点 /mcp，与 client.py 的
    StreamableHttpTransport 配套。旧 SSE 传输在 FastMCP 3.x 与客户端端点
    推断不兼容（初始化超时），已不再作为默认（DEF-03）。
    """
    endpoint = "/mcp" if transport == "streamable-http" else "/sse"
    logger.info("=" * 60)
    logger.info(f"🚀 MCP 服务器 ({transport}) v2.1")
    logger.info(f"   名称: {settings.MCP_SERVER_NAME}")
    logger.info(f"   MCP 端点: http://{host}:{port}{endpoint}")
    logger.info("=" * 60)
    logger.info("已注册: 8 tools, 2 prompts, 1 resource")
    logger.info("  tools: web_search, route_planning, validate_travel_content, format_sources,")
    logger.info("         rag_add_document, rag_query, rag_stats, rag_clear")
    logger.info("  prompts: search_prompt, route_prompt")
    logger.info("  resources: travel://standards")
    logger.info("=" * 60)

    await mcp.run_async(
        transport=transport,
        host=host,
        port=port,
        log_level="info",
    )


# 兼容旧入口名（start_mcp.bat 与历史调用）
run_mcp_sse = run_mcp_http


if __name__ == "__main__":
    asyncio.run(run_mcp_http())