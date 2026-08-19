"""工作流状态定义"""
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Literal
from typing_extensions import TypedDict


class WorkflowState(TypedDict):
    """工作流共享状态"""
    user_input: str
    intent: Literal["chat", "route", "search", "travel"]
    intent_confidence: float
    gps_coords: Optional[str]
    gps_city: Optional[str]  # GPS 逆地理编码解析出的城市（出行起点候选）
    search_results: Optional[Dict[str, Any]]
    route_results: Optional[str]
    rag_results: Optional[str]
    chat_history: Optional[List[Dict[str, str]]]  # [{role: user|assistant, content}]
    final_output: str
    error: Optional[str]
    execution_path: List[str]


@dataclass
class AgentResponse:
    """统一的智能体响应协议"""
    success: bool
    content: Any = None
    error: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)
