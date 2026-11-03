"""
manager.py
管理器层 — 对话的载入、追加、查询、清空
"""
import uuid
from datetime import datetime
from typing import Optional, List, Dict

from .store import SessionStore


class MemoryManager:
    """对话记忆管理器"""

    def __init__(self):
        self.store = SessionStore()

    # ── 会话生命周期 ──────────────────────────

    def get_or_create_session(self, session_id: Optional[str] = None) -> str:
        """获取已有会话或创建新会话，返回 session_id"""
        if session_id and self.store.load_session(session_id):
            return session_id
        return self.store.create_session()

    def save_user_message(self, session_id: str, content: str):
        """保存用户消息"""
        self.store.append_message(session_id, "user", content)

    def save_assistant_message(self, session_id: str, content: str,
                                intent: Optional[str] = None):
        """保存助手回复"""
        self.store.append_message(session_id, "assistant", content, intent)

    def save_exchange(self, session_id: str, user_msg: str,
                      assistant_msg: str, intent: Optional[str] = None):
        """一次对话往返：用户消息 + 助手回复"""
        self.store.append_message(session_id, "user", user_msg)
        self.store.append_message(session_id, "assistant", assistant_msg, intent)

    # ── 查询 ──────────────────────────────────

    def get_session(self, session_id: str) -> Optional[dict]:
        """获取完整会话"""
        return self.store.load_session(session_id)

    def get_session_messages(self, session_id: str) -> List[Dict]:
        """获取会话的消息列表"""
        s = self.store.load_session(session_id)
        return s.get("messages", []) if s else []

    def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出最近会话"""
        return self.store.list_sessions(limit)

    def get_recent_context(self, session_id: str, max_rounds: int = 5) -> str:
        """
        获取最近几轮对话的摘要文本（用于 LLM 上下文）
        返回格式化的对话历史字符串
        """
        messages = self.get_session_messages(session_id)
        recent = messages[-(max_rounds * 2):]  # 每轮 2 条消息
        lines = ["【对话历史】"]
        for msg in recent:
            prefix = "用户" if msg["role"] == "user" else "助手"
            content = msg["content"][:100]
            lines.append(f"{prefix}: {content}")
        return "\n".join(lines)

    # ── 管理 ──────────────────────────────────

    def delete_session(self, session_id: str) -> bool:
        return self.store.delete_session(session_id)

    def clear_all(self):
        self.store.clear_all()


# 全局实例（单例）
_memory_manager: Optional[MemoryManager] = None


def get_memory_manager() -> MemoryManager:
    global _memory_manager
    if _memory_manager is None:
        _memory_manager = MemoryManager()
    return _memory_manager
