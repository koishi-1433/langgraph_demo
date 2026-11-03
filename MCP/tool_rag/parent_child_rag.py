"""
parent_child_rag.py
=================

具有层次化分块策略的父子 RAG 实现。使用父块提供上下文，使用子块进行检索。
"""

import os
import uuid
from typing import List, Optional, Dict, Any
from dataclasses import dataclass

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.language_models import BaseChatModel
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .base_rag import BaseRAG, DocumentInfo


@dataclass
class ChunkRelation:
    """块关系结构"""
    chunk_id: str
    parent_id: Optional[str]
    doc_id: str
    chunk_type: str  # "parent" or "child"


class ParentChildRAG(BaseRAG):
    """具有层次化分块的父子 RAG"""

    def __init__(self,
                 persist_directory: str = "./parent_child_db",
                 collection_name: str = "parent_child_collection",
                 embedding_model: Optional[Embeddings] = None,
                 llm_model: Optional[BaseChatModel] = None,
                 parent_chunk_size: int = 1000,
                 parent_chunk_overlap: int = 200,
                 child_chunk_size: int = 300,
                 child_chunk_overlap: int = 50):
        """
        初始化父子 RAG

        Args:
            persist_directory: 向量库持久化存储目录
            collection_name: 向量集合名称
            embedding_model: 嵌入模型实例
            llm_model: 语言模型实例
            parent_chunk_size: 父块的大小
            parent_chunk_overlap: 父块之间的重叠
            child_chunk_size: 子块的大小
            child_chunk_overlap: 子块之间的重叠
        """
        self.parent_chunk_size = parent_chunk_size
        self.parent_chunk_overlap = parent_chunk_overlap
        self.child_chunk_size = child_chunk_size
        self.child_chunk_overlap = child_chunk_overlap

        # 初始化块关系跟踪
        self.chunk_relations: Dict[str, ChunkRelation] = {}
        self.chunk_content_map: Dict[str, str] = {}

        super().__init__(
            persist_directory=persist_directory,
            collection_name=collection_name,
            embedding_model=embedding_model,
            llm_model=llm_model,
            chunk_size=child_chunk_size,  # Use child size for base chunking
            chunk_overlap=child_chunk_overlap
        )

    def _create_chunks(self, text: str, file_path: str, doc_id: str) -> List[Document]:
        """创建父子块"""
        # 创建分割器
        parent_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.parent_chunk_size,
            chunk_overlap=self.parent_chunk_overlap
        )

        child_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.child_chunk_size,
            chunk_overlap=self.child_chunk_overlap
        )

        # 生成父块
        parent_texts = parent_splitter.split_text(text)
        documents = []

        for i, parent_text in enumerate(parent_texts):
            if not parent_text.strip():
                continue

            parent_id = f"{doc_id}_p_{i}"

            # 添加父块
            parent_doc = Document(
                page_content=parent_text,
                metadata={
                    "chunk_type": "parent",
                    "chunk_id": parent_id,
                    "doc_id": doc_id,
                    "source_file": os.path.basename(file_path)
                }
            )
            documents.append(parent_doc)

            # 记录父块
            self.chunk_relations[parent_id] = ChunkRelation(
                chunk_id=parent_id,
                parent_id=None,
                doc_id=doc_id,
                chunk_type="parent"
            )
            self.chunk_content_map[parent_id] = parent_text

            # 从父块生成子块
            child_texts = child_splitter.split_text(parent_text)
            for j, child_text in enumerate(child_texts):
                if not child_text.strip():
                    continue

                child_id = f"{doc_id}_c_{i}_{j}"
                child_doc = Document(
                    page_content=child_text,
                    metadata={
                        "chunk_type": "child",
                        "chunk_id": child_id,
                        "parent_id": parent_id,
                        "doc_id": doc_id,
                        "source_file": os.path.basename(file_path)
                    }
                )
                documents.append(child_doc)

                # 记录子块
                self.chunk_relations[child_id] = ChunkRelation(
                    chunk_id=child_id,
                    parent_id=parent_id,
                    doc_id=doc_id,
                    chunk_type="child"
                )

        return documents

    def query(self, question: str, k: int = 5, return_parent: bool = True) -> List[Dict[str, Any]]:
        """
        使用父子上下文增强查询

        Args:
            question: 用户问题
            k: 返回结果数量
            return_parent: 是否包含父上下文

        Returns:
            包含父上下文的结果列表
        """
        try:
            # 搜索子块
            results = self.vector_store.similarity_search(question, k=k)

            if not results:
                return []

            processed_results = []
            seen_parent_ids = set()

            for doc in results:
                result = {
                    "content": doc.page_content,
                    "source": doc.metadata.get("source_file", "Unknown"),
                    "chunk_type": doc.metadata.get("chunk_type", "unknown"),
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "parent_context": ""
                }

                # 如果请求且可用，添加父上下文
                if return_parent and doc.metadata.get("chunk_type") == "child":
                    parent_id = doc.metadata.get("parent_id")
                    if parent_id and parent_id not in seen_parent_ids:
                        parent_content = self.chunk_content_map.get(parent_id, "")
                        result["parent_context"] = parent_content
                        seen_parent_ids.add(parent_id)

                processed_results.append(result)

            return processed_results
        except Exception as e:
            print(f"查询出错: {e}")
            return []

    def get_statistics(self) -> Dict[str, Any]:
        """获取父子 RAG 的增强统计信息"""
        base_stats = super().get_statistics()

        # 统计父块和子块数量
        parent_chunks = sum(1 for rel in self.chunk_relations.values() if rel.chunk_type == "parent")
        child_chunks = sum(1 for rel in self.chunk_relations.values() if rel.chunk_type == "child")

        base_stats.update({
            "parent_chunks": parent_chunks,
            "child_chunks": child_chunks,
            "total_chunk_relations": len(self.chunk_relations),
            "chunk_content_map_size": len(self.chunk_content_map)
        })

        return base_stats

    def get_rag_type(self) -> str:
        """获取 RAG 类型标识符"""
        return "parent_child"