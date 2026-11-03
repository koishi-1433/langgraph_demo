"""
rag_service.py
==============
RAG 工具包（MCP/tool_rag）接入主程序的适配层。

职责：
- 把「本地 bge-m3 嵌入 + Chroma 持久化向量库 + 分块/去重/检索」封装成一个
  进程内单例服务，供 MCP Server 的 rag_* 工具调用。
- 懒加载：只有真正发生入库/检索时才加载 5GB 级嵌入模型，不影响主程序启动。
- 优雅降级：模型缺失、向量库异常、功能被禁用都返回结构化失败，绝不抛异常
  打断工作流主链路（数据真实性优先，检索失败时主攻略原样输出）。

配置见 MCP/config/settings.py 的 RAG_* 段与 .env。
"""

from __future__ import annotations

import os

# 离线保证：本地模型目录已完整，禁止 huggingface 在运行时联网探测/下载。
# 放在模块顶层，确保包内 langchain_huggingface 相关导入之后仍然生效。
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")

import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

from MCP.config.settings import settings
from MCP.core.logger import get_logger

logger = get_logger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[2]


def _resolve_persist_dir() -> str:
    """解析向量库目录：相对路径按项目根解析，避免受当前工作目录影响。"""
    raw = (settings.RAG_PERSIST_DIR or "MCP/tool_rag/rag_db").strip()
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return str(path.resolve())


class RAGService:
    """本地向量知识库服务（单例，懒加载，线程安全）"""

    def __init__(self):
        self._lock = threading.Lock()
        self._rag = None                       # TravelRAG 实例
        self._embedding = None                # HuggingFaceEmbeddings 实例
        self._init_error: Optional[str] = None
        self.persist_dir = _resolve_persist_dir()

    @property
    def enabled(self) -> bool:
        return bool(settings.RAG_ENABLED)

    # ------------------------------------------------------------------
    # 初始化
    # ------------------------------------------------------------------
    def _ensure(self):
        """懒加载嵌入模型与向量库；失败原因会被缓存，避免每次请求反复重试。"""
        if not self.enabled:
            raise RuntimeError("RAG 已禁用（RAG_ENABLED=false）")
        if self._rag is not None:
            return self._rag

        with self._lock:
            if self._rag is not None:
                return self._rag
            if self._init_error:
                raise RuntimeError(self._init_error)

            try:
                from MCP.tool_rag.embedding_manager import (
                    EmbeddingManager,
                    get_default_model_alias,
                    resolve_local_model_path,
                )
                from MCP.tool_rag.travel_rag import TravelRAG

                alias = get_default_model_alias()
                # 提前校验本地模型完整性，给出可定位的错误而不是静默联网下载
                model_path = resolve_local_model_path(alias)
                logger.info(f"RAG 初始化：本地嵌入模型 {alias} @ {model_path}")

                embedding = EmbeddingManager().create_local_embedding(model_name=alias)
                rag = TravelRAG(
                    persist_directory=self.persist_dir,
                    collection_name=settings.RAG_COLLECTION_NAME,
                    embedding_model=embedding,
                    llm_model=None,       # 不做生成式改写，检索片段原文直出，避免编造
                    chunk_size=settings.RAG_CHUNK_SIZE,
                    chunk_overlap=settings.RAG_CHUNK_OVERLAP,
                )
                self._embedding = embedding
                self._rag = rag
                logger.info(
                    f"RAG 初始化完成：vector_db={self.persist_dir}, "
                    f"collection={settings.RAG_COLLECTION_NAME}, "
                    f"chunks={rag.chunk_count()}"
                )
                return rag
            except Exception as exc:  # noqa: BLE001 - 需要把任意底层异常转成结构化失败
                self._init_error = f"{type(exc).__name__}: {exc}"
                logger.error(f"RAG 初始化失败：{self._init_error}")
                raise RuntimeError(self._init_error) from exc

    # ------------------------------------------------------------------
    # 对外能力（全部返回 dict，供 MCP 工具序列化）
    # ------------------------------------------------------------------
    def add_document(self, text: str, source: str = "", title: str = "") -> Dict[str, Any]:
        """把一段文本切块写入知识库（幂等）。"""
        if not self.enabled:
            return {"success": False, "disabled": True, "error": "RAG 已禁用（RAG_ENABLED=false）"}
        try:
            rag = self._ensure()
            source = (source or "workflow").strip()
            info = rag.add_text(text=text, source=source, title=title or source)
            return {
                "success": True,
                "chunks_added": info["added"],
                "source": source,
                "title": info["title"],
                "total_chunks": rag.chunk_count(),
                "persist_directory": self.persist_dir,
            }
        except Exception as exc:
            logger.error(f"RAG 入库失败：{exc}")
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    def query(self, question: str, k: Optional[int] = None) -> Dict[str, Any]:
        """语义检索；返回命中片段与可直接拼接的 rag_text。"""
        if not self.enabled:
            return {
                "success": False,
                "disabled": True,
                "error": "RAG 已禁用（RAG_ENABLED=false）",
                "hits": [],
                "rag_text": "",
            }
        try:
            rag = self._ensure()
            top_k = max(1, min(int(k or settings.RAG_TOP_K), 20))
            raw_hits = rag.query_with_scores(question, k=top_k)

            # 归一化向量下的 L2 距离阈值（约等于余弦相似度 >= 0.4）。
            # 超阈值的片段不展示，避免用不相关内容污染主攻略。
            max_distance = float(settings.RAG_MAX_DISTANCE)
            hits = [h for h in raw_hits if h["distance"] <= max_distance]

            return {
                "success": True,
                "question": question,
                "k": top_k,
                "hits": hits,
                "rag_text": self.format_hits(hits),
                "retrieved": len(raw_hits),
                "filtered_out": len(raw_hits) - len(hits),
                "total_chunks": rag.chunk_count(),
                "max_distance": max_distance,
                "rag_type": rag.get_rag_type(),
            }
        except Exception as exc:
            logger.error(f"RAG 检索失败：{exc}")
            return {
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
                "hits": [],
                "rag_text": "",
            }

    def stats(self) -> Dict[str, Any]:
        """知识库状态（不触发模型加载亦可调用时返回 enabled 信息）。"""
        base: Dict[str, Any] = {
            "success": True,
            "enabled": self.enabled,
            "persist_directory": self.persist_dir,
            "collection_name": settings.RAG_COLLECTION_NAME,
            "top_k": settings.RAG_TOP_K,
            "max_distance": float(settings.RAG_MAX_DISTANCE),
            "initialized": self._rag is not None,
            "init_error": self._init_error,
        }
        if not self.enabled:
            return base
        try:
            from MCP.tool_rag.embedding_manager import get_default_model_alias

            rag = self._ensure()
            base.update({
                "embedding_model": get_default_model_alias(),
                "rag_type": rag.get_rag_type(),
                "vector_store_documents": rag.chunk_count(),
                "chunk_size": settings.RAG_CHUNK_SIZE,
                "chunk_overlap": settings.RAG_CHUNK_OVERLAP,
            })
        except Exception as exc:
            base["success"] = False
            base["error"] = f"{type(exc).__name__}: {exc}"
        return base

    def clear(self, confirm: bool = False) -> Dict[str, Any]:
        """清空知识库（需显式确认）；清空后下次调用自动重建集合。"""
        if not confirm:
            return {"success": False, "error": "清空知识库需显式传入 confirm=true"}
        try:
            rag = self._ensure()
            rag.vector_store.delete_collection()
            # 置空单例，下次 _ensure() 重新创建集合
            self._rag = None
            self._embedding = None
            self._init_error = None
            logger.info("RAG 知识库已清空")
            return {"success": True, "message": "知识库已清空，下次调用时自动重建"}
        except Exception as exc:
            return {"success": False, "error": f"{type(exc).__name__}: {exc}"}

    # ------------------------------------------------------------------
    @staticmethod
    def format_hits(hits: List[Dict[str, Any]]) -> str:
        """把检索片段组织成可追加到攻略末尾的文本；无命中返回空串。"""
        if not hits:
            return ""
        lines = [
            "## 📚 知识库检索补充",
            f"以下 {len(hits)} 条片段来自本地向量知识库（bge-m3 + Chroma），按相关度排序：",
            "",
        ]
        for idx, hit in enumerate(hits, 1):
            snippet = " ".join((hit.get("content") or "").split())
            if len(snippet) > 260:
                snippet = snippet[:260] + "…"
            label = hit.get("title") or hit.get("source") or "本地知识库"
            lines.append(f"{idx}. 【{label}】{snippet}")
        lines.append("")
        lines.append(
            "*说明：以上为本地知识库检索片段，非实时数据，仅供参考；"
            "完整信息请以上方攻略与参考来源为准。*"
        )
        return "\n".join(lines)


# ==================== 进程内单例 ====================

_service: Optional[RAGService] = None
_service_lock = threading.Lock()


def get_rag_service() -> RAGService:
    """获取全局 RAG 服务（模型与向量库只初始化一次）。"""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RAGService()
    return _service
