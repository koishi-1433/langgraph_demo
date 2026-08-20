"""路线规划 Agent — ReAct，只授权地图和火车班次 MCP 工具"""
from ..config.llm_client import LLMClient
from .base_agent import BaseAgent

ROUTE_SYSTEM_PROMPT = """\
你是路线规划专家。你的职责：

1. 首先分析用户需求，提取出发地(origin)和目的地(destination)。
   - 如果只有一个地点 → origin 为空，destination 为该地点（表示规划前往该地的路线）
   - "从A到B" → origin=A, destination=B
   - "去西安旅游" → origin 可能为空或当前城市, destination=西安
2. 使用 amap_geocode 将起点终点转换为坐标（如有需要）。
3. 使用 amap_route_planning 规划路线。mode 选择逻辑：
   - 距离 > 120km → mode=train（火车优先）
   - 短途 → mode=auto 或 mode=driving
4. 如果是跨城火车出行，额外用 py12306_query_train_schedule 查询车次信息。
5. 使用 amap_regeocode 可将起点坐标逆地理编码为城市名（当起点是坐标时）。

【输出格式要求】
- 按三段式组织结果：市内→出发站 / 火车段 / 到达站→目的地
- 每段标注：icon、模式、起点、终点、距离（km）、用时（分钟）、费用
- 总览给出总距离、总耗时、总费用
- 如果有火车数据，列出车次编号、发车/到达时间、票价

示例输出：
🚄 第1段：市内交通 → 出发站
  从 XX 到 XX火车站
  距离: 15.2km, 用时: 25分钟

🚄 第2段：火车
  从 XX火车站 → XX火车站
  车次: G1234, 时间: 08:00→12:00, 历时: 4h, 二等座: ¥500

🚄 第3段：市内交通 → 目的地
  从 XX火车站 到 XX市中心
  距离: 10.0km, 用时: 20分钟

📊 总统计
  总距离: 500km, 总耗时: 280分钟, 总费用: ¥550

如果路线规划失败，返回错误信息和备选建议。"""


class RouteAgent(BaseAgent):
    """路线规划 Agent：处理 route / travel 的路线子任务。

    授权工具（由 graph 初始化时按名注入，缺失会触发 _validate_tools 快速失败）：
    - amap_geocode：地址 → 坐标
    - amap_regeocode：坐标 → 城市（起点为 GPS 时反解城市名）
    - amap_route_planning：驾车/公交/火车路线规划（mode=auto 按距离择优）
    - py12306_query_train_schedule：跨城火车班次查询
    """

    def __init__(self, llm_client: LLMClient, tools=None):
        super().__init__(
            llm_client=llm_client,
            tools=tools or [],
            system_prompt=ROUTE_SYSTEM_PROMPT,
            name="route_agent",
        )
