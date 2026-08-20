"""工作流状态定义。

WorkflowState 是 LangGraph StateGraph 的共享状态（TypedDict）：
- 每个节点从 state 读取所需字段，返回 dict 做增量更新（未返回的字段保持不变）；
- 字段按"谁写入"划分：intent 相关由 intent_recognition 节点写，
  search_results / route_results 由对应节点写，final_output / error 由叶子节点统一写。
"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    """工作流共享状态"""

    # ── 输入（由 graph.run 初始化） ─────────────────────────────────
    user_input: str                                    # 用户原始输入（可能带 [当前位置:xx] 前缀）
    chat_history: Optional[List[Dict[str, str]]]       # 会话历史 [{role: user|assistant, content}]，供多轮记忆
    gps_coords: Optional[str]                          # 前端定位坐标 "lng,lat"（未定位为 None）

    # ── 意图识别（intent_recognition 节点写入） ────────────────────
    intent: Literal["chat", "route", "search", "travel"]  # 分类结果，驱动条件路由
    intent_confidence: float                           # 识别置信度 0~1（供日志/兜底参考）

    # ── 中间结果（search / route / travel 节点写入） ───────────────
    gps_city: Optional[str]                            # GPS 逆地理编码解析出的城市（出行起点候选）
    search_results: Optional[Dict[str, Any]]           # 攻略搜索结果 {"destination": str, "article": str}
    route_results: Optional[str]                       # 路线规划结果文本
    rag_results: Optional[str]                         # RAG 检索结果（预留字段，当前未使用）

    # ── 输出（叶子节点统一写入） ───────────────────────────────────
    final_output: str                                  # 最终回复文本（展示给用户的内容）
    error: Optional[str]                               # 失败原因（成功时为 None）
    execution_path: List[str]                          # 实际执行节点序列（如 ["intent_recognition", "travel"]）


@dataclass
class AgentResponse:
    """统一的智能体响应协议。

    BaseAgent.run 及 IntentAgent.recognize 都返回该结构，
    上层（graph 节点）据此判断成功与否并提取内容，无需感知各 Agent 内部实现。
    """
    success: bool                                      # 是否成功
    content: Any = None                                # 成功时的内容（文本或结构化 dict）
    error: Optional[str] = None                        # 失败原因（success=False 时非空）
    metadata: Dict[str, Any] = field(default_factory=dict)  # 附加信息（如 thread_id、agent 名）
