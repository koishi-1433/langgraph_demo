"""
dual_vector_rag.py
=================

使用两个独立向量库的双向量 RAG 实现。通过多种向量数据库策略提供增强检索。
"""

import os
from typing import List, Optional, Dict, Any

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_chroma import Chroma
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .base_rag import BaseRAG


class DualVectorRAG(BaseRAG):
    """具有两个独立向量库的双向量 RAG"""

    def __init__(self,
                 persist_directory: str = "./dual_vector_db",
                 collection_name: str = "dual_vector_collection",
                 embedding_model: Optional[Embeddings] = None,
                 llm_model: Optional[BaseChatModel] = None,
                 primary_chunk_size: int = 500,
                 secondary_chunk_size: int = 1000):
        """
        初始化双向量 RAG

        Args:
            persist_directory: 向量库持久化存储目录
            collection_name: 向量集合的基础名称
            embedding_model: 嵌入模型实例
            llm_model: 语言模型实例
            primary_chunk_size: 主要（密集）块的大小
            secondary_chunk_size: 次要（上下文）块的大小
        """
        self.primary_chunk_size = primary_chunk_size
        self.secondary_chunk_size = secondary_chunk_size

        # 初始化双向量库
        self.primary_vector_store = None
        self.secondary_vector_store = None

        super().__init__(
            persist_directory=persist_directory,
            collection_name=collection_name,
            embedding_model=embedding_model,
            llm_model=llm_model,
            chunk_size=primary_chunk_size,
            chunk_overlap=50
        )

    def _init_vector_store(self):
        """初始化双向量库"""
        if not self.embedding_model:
            raise ValueError("需要嵌入模型")

        # 主要向量库（密集块）
        primary_path = os.path.join(self.persist_directory, "primary")
        os.makedirs(primary_path, exist_ok=True)

        self.primary_vector_store = Chroma(
            collection_name=f"{self.collection_name}_primary",
            embedding_function=self.embedding_model,
            persist_directory=primary_path
        )

        # 次要向量库（上下文块）
        secondary_path = os.path.join(self.persist_directory, "secondary")
        os.makedirs(secondary_path, exist_ok=True)

        self.secondary_vector_store = Chroma(
            collection_name=f"{self.collection_name}_secondary",
            embedding_function=self.embedding_model,
            persist_directory=secondary_path
        )

        print(f"双向量库已初始化:")
        print(f"  主要: {primary_path}")
        print(f"  次要: {secondary_path}")

        # 将主向量库设置为主要库以实现基础功能
        self.vector_store = self.primary_vector_store

    def _create_chunks(self, text: str, file_path: str, doc_id: str) -> List[Document]:
        """为两个向量库创建块"""
        # 创建分割器
        primary_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.primary_chunk_size,
            chunk_overlap=50
        )

        secondary_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.secondary_chunk_size,
            chunk_overlap=100
        )

        # 生成主要块（密集、详细）
        primary_texts = primary_splitter.split_text(text)
        primary_documents = []

        for i, chunk_text in enumerate(primary_texts):
            if not chunk_text.strip():
                continue

            doc = Document(
                page_content=chunk_text,
                metadata={
                    "store_type": "primary",
                    "chunk_id": f"{doc_id}_primary_{i}",
                    "doc_id": doc_id,
                    "source_file": os.path.basename(file_path),
                    "chunk_index": i
                }
            )
            primary_documents.append(doc)

        # 生成次要块（上下文、更广泛）
        secondary_texts = secondary_splitter.split_text(text)
        secondary_documents = []

        for i, chunk_text in enumerate(secondary_texts):
            if not chunk_text.strip():
                continue

            doc = Document(
                page_content=chunk_text,
                metadata={
                    "store_type": "secondary",
                    "chunk_id": f"{doc_id}_secondary_{i}",
                    "doc_id": doc_id,
                    "source_file": os.path.basename(file_path),
                    "chunk_index": i
                }
            )
            secondary_documents.append(doc)

        # 添加到相应库
        if primary_documents:
            self.primary_vector_store.add_documents(primary_documents)
        if secondary_documents:
            self.secondary_vector_store.add_documents(secondary_documents)

        # 返回组合以实现基础功能
        return primary_documents + secondary_documents

    def query(self, question: str, k: int = 5, strategy: str = "combined") -> List[Dict[str, Any]]:
        """
        使用双向量策略查询

        Args:
            question: 用户问题
            k: 返回结果数量
            strategy: 查询策略 ("primary", "secondary", "combined", "hybrid")

        Returns:
            检索到的文档列表
        """
        try:
            results = []

            if strategy == "primary":
                results = self._query_store(self.primary_vector_store, question, k)
            elif strategy == "secondary":
                results = self._query_store(self.secondary_vector_store, question, k)
            elif strategy == "combined":
                # 查询两个库并组合
                primary_results = self._query_store(self.primary_vector_store, question, k//2)
                secondary_results = self._query_store(self.secondary_vector_store, question, k//2)
                results = primary_results + secondary_results
            elif strategy == "hybrid":
                # 查询两个库并按相关性排名
                primary_results = self._query_store(self.primary_vector_store, question, k)
                secondary_results = self._query_store(self.secondary_vector_store, question, k)
                results = self._hybrid_rank(primary_results, secondary_results, k)

            return results[:k]
        except Exception as e:
            print(f"查询出错: {e}")
            return []

    def _query_store(self, vector_store: Chroma, question: str, k: int) -> List[Dict[str, Any]]:
        """查询特定向量库"""
        results = vector_store.similarity_search(question, k=k)

        processed_results = []
        for doc in results:
            result = {
                "content": doc.page_content,
                "source": doc.metadata.get("source_file", "Unknown"),
                "store_type": doc.metadata.get("store_type", "unknown"),
                "chunk_id": doc.metadata.get("chunk_id", ""),
                "doc_id": doc.metadata.get("doc_id", "")
            }
            processed_results.append(result)

        return processed_results

    def _hybrid_rank(self, primary_results: List[Dict], secondary_results: List[Dict], k: int) -> List[Dict]:
        """结合两个库结果的混合排名"""
        # 简单混合：优先主要结果，然后添加次要结果
        combined = primary_results.copy()

        # 添加不重复的次要结果
        seen_content = {result["content"][:100] for result in combined}
        for result in secondary_results:
            content_preview = result["content"][:100]
            if content_preview not in seen_content:
                combined.append(result)
                seen_content.add(content_preview)

        return combined[:k]

    def enhanced_query(self, question: str, k: int = 5) -> str:
        """
        生成综合答案的增强查询

        Args:
            question: 用户问题
            k: 使用的结果数量

        Returns:
            生成的答案
        """
        # 从两个库获取结果
        primary_results = self._query_store(self.primary_vector_store, question, k)
        secondary_results = self._query_store(self.secondary_vector_store, question, k)

        # 合并上下文
        all_results = primary_results + secondary_results
        context_text = "\n\n".join([result["content"] for result in all_results])

        if not self.llm_model:
            return f"找到 {len(all_results)} 个相关文档。上下文: {context_text[:500]}..."

        prompt = f"""基于以下来自多个来源的上下文，为问题提供综合答案。

上下文:
{context_text}

问题: {question}

请提供一个综合所有相关来源信息的详细答案:"""

        try:
            response = self.llm_model.invoke(prompt)
            return response
        except Exception as e:
            return f"生成答案时出错: {e}"

    def get_statistics(self) -> Dict[str, Any]:
        """获取双向量库的统计信息"""
        base_stats = super().get_statistics()

        primary_count = self.primary_vector_store._collection.count() if self.primary_vector_store._collection else 0
        secondary_count = self.secondary_vector_store._collection.count() if self.secondary_vector_store._collection else 0

        base_stats.update({
            "primary_store_documents": primary_count,
            "secondary_store_documents": secondary_count,
            "total_documents": primary_count + secondary_count,
            "primary_chunk_size": self.primary_chunk_size,
            "secondary_chunk_size": self.secondary_chunk_size
        })

        return base_stats

    def get_rag_type(self) -> str:
        """获取 RAG 类型标识符"""
        return "dual_vector"