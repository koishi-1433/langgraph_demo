from dataclasses import dataclass, field
from typing import Literal, Optional, Any, Dict, List
from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    """工作流状态"""
    # 用户输入
    user_input: str

    # 意图识别
    intent: Literal["chat", "route", "search", "travel"]
    # 记录"信心分"，在生产实践中用于决定是否需要人工接入。
    intent_confidence: float

    # GPS坐标（来自浏览器前端，不含在user_input中）
    gps_coords: Optional[str]

    # 搜索结果：存放search_tool.py返回的结果
    search_results: Optional[Dict[str, Any]]

    # 路线结果：存放plan_route返回的结果
    route_results: Optional[str]

    # RAG 检索结果：rag_text 是可直接拼接的补充文本，rag_results 为结果快照
    rag_text: Optional[str]
    rag_results: Optional[str]

    # 数据质量标注（DEF-04）：travel 链路透传，供 /chat 响应与前端展示
    data_quality: Optional[str]
    is_realtime: Optional[bool]
    warnings: Optional[List[str]]

    # 最终输出
    final_output: str

    # 错误信息
    error: Optional[str]

    # 执行路径
    # 记录任务流经了哪些节点（如：intent -> researcher -> route），方便复盘和生成执行日志。
    execution_path: List[str]


# 定义 Agent 之间传递的标准化通信协议，确保不同 Agent 之间能够正确理解和处理彼此的输入输出。
# 要求每个 Agent 响应返回一个统一的格式：
# success（是否成功）、content（具体结果）、error（报错信息）、元数据。
@dataclass
class AgentResponse:
    """统一的智能体响应"""
    success: bool
    content: Any
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
