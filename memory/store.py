"""
store.py
文件存储层 — 按 session 分文件读写 JSON 格式的对话历史
"""
import json
import os
import uuid
from datetime import datetime
from pathlib import Path
from typing import Optional, List


class SessionStore:
    """会话文件存储"""

    def __init__(self, base_dir: Optional[str] = None):
        if base_dir is None:
            base_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "data", "memory"
            )
        self.base_dir = base_dir
        os.makedirs(self.base_dir, exist_ok=True)

    # ── 路径 ──────────────────────────────────

    def _session_path(self, session_id: str) -> str:
        return os.path.join(self.base_dir, f"{session_id}.json")

    def _index_path(self) -> str:
        return os.path.join(self.base_dir, "_index.json")

    # ── 索引（快速列出所有会话） ──────────────

    def _load_index(self) -> dict:
        path = self._index_path()
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    return json.load(f)
            except (json.JSONDecodeError, OSError):
                return {"sessions": []}
        return {"sessions": []}

    def _save_index(self, index: dict):
        with open(self._index_path(), "w", encoding="utf-8") as f:
            json.dump(index, f, ensure_ascii=False, indent=2)

    def _update_index(self, session_id: str, title: str, msg_count: int):
        index = self._load_index()
        now = datetime.now().isoformat()
        # 去重更新
        for s in index["sessions"]:
            if s["id"] == session_id:
                s["title"] = title or s["title"]
                s["updated_at"] = now
                s["msg_count"] = msg_count
                self._save_index(index)
                return
        # 新增
        index["sessions"].insert(0, {
            "id": session_id,
            "title": title or "新对话",
            "created_at": now,
            "updated_at": now,
            "msg_count": msg_count,
        })
        self._save_index(index)

    # ── CRUD ──────────────────────────────────

    def create_session(self) -> str:
        """创建一个新的空会话，返回 session_id"""
        session_id = uuid.uuid4().hex[:16]
        data = {
            "session_id": session_id,
            "title": "新对话",
            "created_at": datetime.now().isoformat(),
            "updated_at": datetime.now().isoformat(),
            "messages": [],
        }
        with open(self._session_path(session_id), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        self._update_index(session_id, "新对话", 0)
        return session_id

    def load_session(self, session_id: str) -> Optional[dict]:
        """加载一个会话的全部数据"""
        path = self._session_path(session_id)
        if not os.path.exists(path):
            return None
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None

    def append_message(self, session_id: str, role: str, content: str,
                       intent: Optional[str] = None):
        """追加一条消息到会话"""
        session = self.load_session(session_id)
        if session is None:
            session = {
                "session_id": session_id,
                "title": "新对话",
                "created_at": datetime.now().isoformat(),
                "messages": [],
            }

        msg = {
            "role": role,
            "content": content,
            "timestamp": datetime.now().isoformat(),
        }
        if intent:
            msg["intent"] = intent

        session["messages"].append(msg)
        session["updated_at"] = datetime.now().isoformat()

        # 第一条用户消息作为标题
        if len(session["messages"]) == 1 and role == "user":
            session["title"] = content[:30]

        with open(self._session_path(session_id), "w", encoding="utf-8") as f:
            json.dump(session, f, ensure_ascii=False, indent=2)

        self._update_index(
            session_id,
            session["title"],
            len(session["messages"]),
        )

    def list_sessions(self, limit: int = 50) -> List[dict]:
        """列出最近的会话摘要（按更新时间倒序）"""
        index = self._load_index()
        sessions = sorted(
            index["sessions"],
            key=lambda s: s.get("updated_at", ""),
            reverse=True,
        )
        return sessions[:limit]

    def delete_session(self, session_id: str) -> bool:
        """删除一个会话"""
        path = self._session_path(session_id)
        if os.path.exists(path):
            os.remove(path)
            # 从索引中移除
            index = self._load_index()
            index["sessions"] = [s for s in index["sessions"] if s["id"] != session_id]
            self._save_index(index)
            return True
        return False

    def clear_all(self):
        """清空所有会话（保留目录）"""
        for fname in os.listdir(self.base_dir):
            if fname.endswith(".json"):
                os.remove(os.path.join(self.base_dir, fname))
