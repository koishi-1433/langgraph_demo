"""
api.py
记忆模块 FastAPI 路由 — 供前端调用的 REST 接口
"""
import json
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional, List

from .manager import get_memory_manager

router = APIRouter(prefix="/memory", tags=["memory"])


# ── 响应模型 ────────────────────────────────

class SessionItem(BaseModel):
    id: str
    title: str
    created_at: str
    updated_at: str
    msg_count: int


class SessionListResponse(BaseModel):
    success: bool
    sessions: List[SessionItem]


class MessageItem(BaseModel):
    role: str
    content: str
    timestamp: str
    intent: Optional[str] = None


class SessionDetailResponse(BaseModel):
    success: bool
    session_id: str
    title: str
    created_at: str
    updated_at: str
    messages: List[MessageItem]


class DeleteResponse(BaseModel):
    success: bool
    message: str


# ── 路由 ────────────────────────────────────

@router.get("/sessions", response_model=SessionListResponse)
async def list_sessions(limit: int = 50):
    """获取历史会话列表"""
    mgr = get_memory_manager()
    sessions = mgr.list_sessions(limit)
    items = [
        SessionItem(
            id=s["id"],
            title=s.get("title", "新对话"),
            created_at=s.get("created_at", ""),
            updated_at=s.get("updated_at", ""),
            msg_count=s.get("msg_count", 0),
        )
        for s in sessions
    ]
    return SessionListResponse(success=True, sessions=items)


@router.get("/sessions/{session_id}", response_model=SessionDetailResponse)
async def get_session(session_id: str):
    """获取某个会话的完整内容"""
    mgr = get_memory_manager()
    session = mgr.get_session(session_id)
    if not session:
        raise HTTPException(status_code=404, detail="会话不存在")
    messages = [
        MessageItem(
            role=m["role"],
            content=m["content"],
            timestamp=m.get("timestamp", ""),
            intent=m.get("intent"),
        )
        for m in session.get("messages", [])
    ]
    return SessionDetailResponse(
        success=True,
        session_id=session["session_id"],
        title=session.get("title", "新对话"),
        created_at=session.get("created_at", ""),
        updated_at=session.get("updated_at", ""),
        messages=messages,
    )


@router.delete("/sessions/{session_id}", response_model=DeleteResponse)
async def delete_session(session_id: str):
    """删除某个会话"""
    mgr = get_memory_manager()
    ok = mgr.delete_session(session_id)
    if not ok:
        raise HTTPException(status_code=404, detail="会话不存在")
    return DeleteResponse(success=True, message="已删除")


@router.post("/sessions", response_model=dict)
async def create_session():
    """创建一个新会话，返回 session_id"""
    mgr = get_memory_manager()
    session_id = mgr.store.create_session()
    return {"success": True, "session_id": session_id}


@router.delete("/sessions", response_model=DeleteResponse)
async def clear_all_sessions():
    """清空所有会话"""
    mgr = get_memory_manager()
    mgr.clear_all()
    return DeleteResponse(success=True, message="已清空所有会话")
