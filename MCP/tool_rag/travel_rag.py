"""
travel_rag.py
路径: MCP/tool_rag/travel_rag.py
功能: 旅游助手专用RAG实现，基于本地向量库提供旅游问答与知识入库能力。

在主链路中的用法：
- add_text()          把搜索生成的攻略切块写入持久化向量库（确定性 ID，重复写入不产生重复块）
- query_with_scores() 按语义检索并返回带距离的片段，供工作流组织「知识库检索补充」
"""

import hashlib
from typing import Optional, List, Dict, Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_text_splitters import RecursiveCharacterTextSplitter

from MCP.tool_rag.base_rag import BaseRAG


class TravelRAG(BaseRAG):
    """旅游助手专用RAG实现"""

    def __init__(self,
                 persist_directory: str = "./rag_db",
                 collection_name: str = "travel_rag_collection",
                 embedding_model: Optional[Embeddings] = None,
                 llm_model: Optional[BaseChatModel] = None,
                 chunk_size: int = 1000,
                 chunk_overlap: int = 200):
        """
        初始化旅游助手RAG系统

        Args:
            persist_directory: 向量存储目录
            collection_name: 集合名称
            embedding_model: 嵌入模型
            llm_model: 语言模型
            chunk_size: 文本块大小
            chunk_overlap: 文本块重叠大小
        """
        super().__init__(
            persist_directory=persist_directory,
            collection_name=collection_name,
            embedding_model=embedding_model,
            llm_model=llm_model,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap
        )

    def get_rag_type(self) -> str:
        """获取RAG类型标识 - 实现抽象方法"""
        return "travel_assistant_rag"

    # ==================== 主链路接入扩展 ====================

    def add_text(self,
                 text: str,
                 source: str,
                 title: str = "",
                 metadata: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """把内存中的文本（如 Tavily 生成的攻略）切块写入向量库。

        ID 由「来源 + 块内容哈希」确定性生成，Chroma 底层走 upsert，
        因此同一篇文档反复入库不会产生重复块，也不会让知识库无限膨胀。

        Args:
            text: 原文
            source: 来源标识（如 "tavily:西安"），同时作为去重命名空间
            title: 展示用标题
            metadata: 附加元数据

        Returns:
            {"added": 本次写入块数, "source": ..., "title": ...}
        """
        if not text or not text.strip():
            return {"added": 0, "source": source, "title": title or source}

        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
        )
        chunks = splitter.split_text(text)

        source_key = hashlib.sha256((source or "unknown").encode("utf-8")).hexdigest()[:12]
        documents: List[Document] = []
        ids: List[str] = []
        seen = set()

        for idx, chunk in enumerate(chunks):
            chunk = chunk.strip()
            if not chunk:
                continue
            content_hash = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:16]
            chunk_id = f"{source_key}_{content_hash}"
            if chunk_id in seen:
                continue
            seen.add(chunk_id)

            doc_metadata: Dict[str, Any] = {
                "source": source or "unknown",
                "title": title or source or "unknown",
                "chunk_index": idx,
                "chunk_id": chunk_id,
                "doc_id": source_key,
                "content_hash": content_hash,
            }
            if metadata:
                doc_metadata.update(metadata)
            documents.append(Document(page_content=chunk, metadata=doc_metadata))
            ids.append(chunk_id)

        if documents:
            # 先查已存在的 ID，保证返回的 added 是「真正新增」而非本次 upsert 的总数
            try:
                existing = set(self.vector_store.get(ids=ids).get("ids") or [])
            except Exception:
                existing = set()
            new_count = len([cid for cid in ids if cid not in existing])
            # langchain_chroma.add_texts 内部使用 collection.upsert(ids=...)
            self.vector_store.add_documents(documents, ids=ids)
        else:
            new_count = 0

        return {"added": new_count, "source": source, "title": title or source}

    def query_with_scores(self, question: str, k: int = 5) -> List[Dict[str, Any]]:
        """语义检索并返回带距离的片段（距离越小越相关，归一化向量下为 L2 距离）。"""
        pairs = self.vector_store.similarity_search_with_score(question, k=k)
        results: List[Dict[str, Any]] = []
        for doc, score in pairs:
            results.append({
                "content": doc.page_content,
                "source": doc.metadata.get("source", "unknown"),
                "title": doc.metadata.get("title", ""),
                "chunk_id": doc.metadata.get("chunk_id", ""),
                "distance": round(float(score), 4),
            })
        return results

    def chunk_count(self) -> int:
        """返回向量库中当前块数量"""
        try:
            return int(self.vector_store._collection.count())
        except Exception:
            return 0

    def query_travel_info(self, question: str, k: int = 5) -> dict:
        """
        查询旅游信息

        Args:
            question: 用户问题
            k: 返回文档数量

        Returns:
            查询结果
        """
        try:
            # 执行基础查询
            retrieved_docs = self.query(question=question, k=k)

            # 生成答案
            answer = self.generate_answer(question=question, context=retrieved_docs)

            return {
                "question": question,
                "answer": answer,
                "retrieved_documents": retrieved_docs,
                "document_count": len(retrieved_docs),
                "rag_type": self.get_rag_type(),
                "status": "success"
            }

        except Exception as e:
            return {
                "question": question,
                "error": str(e),
                "status": "error"
            }