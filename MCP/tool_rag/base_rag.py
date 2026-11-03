"""
base_rag.py
===========

基础 RAG 系统实现，采用模块化设计。提供可被其他 RAG 实现扩展的核心 RAG 功能。
"""

import os
import uuid
from typing import List, Optional, Dict, Any, Union
from dataclasses import dataclass
from abc import ABC, abstractmethod

from langchain_core.documents import Document
from langchain_core.embeddings import Embeddings
from langchain_core.vectorstores import VectorStore
from langchain_core.language_models import BaseChatModel
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_chroma import Chroma


@dataclass
class DocumentInfo:
    """文档元数据结构"""
    doc_id: str
    filename: str
    file_path: str
    file_hash: str
    status: str = "active"  # active, deleted
    created_at: str = ""
    updated_at: str = ""


class BaseRAG(ABC):
    """采用模块化设计的基础 RAG 系统"""

    def __init__(self,
                 persist_directory: str = "./rag_db",
                 collection_name: str = "rag_collection",
                 embedding_model: Optional[Embeddings] = None,
                 llm_model: Optional[BaseChatModel] = None,
                 chunk_size: int = 1000,
                 chunk_overlap: int = 200):
        """
        初始化基础 RAG 系统

        Args:
            persist_directory: 向量库持久化存储目录
            collection_name: 向量集合名称
            embedding_model: 嵌入模型实例
            llm_model: 大语言模型实例
            chunk_size: 文本分块大小
            chunk_overlap: 文本块之间的重叠长度
        """
        self.persist_directory = persist_directory
        self.collection_name = collection_name
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        # 初始化模型
        self.embedding_model = embedding_model
        self.llm_model = llm_model

        # 初始化向量库
        self.vector_store = None
        self._init_vector_store()

        # 文档管理
        self.documents: Dict[str, DocumentInfo] = {}

        print("基础 RAG 系统已初始化")

    def _init_vector_store(self):
        """初始化向量库"""
        if not self.embedding_model:
            raise ValueError("Embedding model is required")

        chroma_path = os.path.join(self.persist_directory, "chroma")
        os.makedirs(chroma_path, exist_ok=True)

        self.vector_store = Chroma(
            collection_name=self.collection_name,
            embedding_function=self.embedding_model,
            persist_directory=chroma_path
        )
        print(f"向量库已初始化: {chroma_path}")

    def add_documents(self, file_paths: List[str]) -> bool:
        """添加文档到 RAG 系统"""
        success_count = 0
        for file_path in file_paths:
            try:
                if self._process_single_file(file_path):
                    success_count += 1
            except Exception as e:
                print(f"处理文件 {file_path} 失败: {e}")

        print(f"成功处理 {success_count}/{len(file_paths)} 个文件")
        return success_count > 0

    def _process_single_file(self, file_path: str) -> bool:
        """处理单个文件"""
        if not os.path.exists(file_path):
            print(f"文件未找到: {file_path}")
            return False

        # 创建文档信息
        doc_id = str(uuid.uuid4())
        doc_info = DocumentInfo(
            doc_id=doc_id,
            filename=os.path.basename(file_path),
            file_path=file_path,
            file_hash=str(hash(open(file_path, 'rb').read()))
        )

        # 存储文档信息
        self.documents[doc_id] = doc_info

        # 读取文件内容
        try:
            if file_path.endswith(('.md', '.txt')):
                with open(file_path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            else:
                content = f"二进制文件: {os.path.basename(file_path)}"
        except Exception as e:
            print(f"读取文件 {file_path} 时失败: {e}")
            return False

        # 创建块并添加到向量库
        documents = self._create_chunks(content, file_path, doc_id)
        if documents:
            self.vector_store.add_documents(documents)
            print(f"[成功] 处理: {os.path.basename(file_path)} ({len(documents)} 个块)")
            return True

        return False

    def _create_chunks(self, text: str, file_path: str, doc_id: str) -> List[Document]:
        """从内容创建文本块"""
        splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap
        )

        texts = splitter.split_text(text)
        documents = []

        for i, chunk_text in enumerate(texts):
            if not chunk_text.strip():
                continue

            doc = Document(
                page_content=chunk_text,
                metadata={
                    "chunk_id": f"{doc_id}_chunk_{i}",
                    "doc_id": doc_id,
                    "source_file": os.path.basename(file_path),
                    "chunk_index": i
                }
            )
            documents.append(doc)

        return documents

    def query(self, question: str, k: int = 5) -> List[Dict[str, Any]]:
        """
        查询 RAG 系统

        Args:
            question: 用户问题
            k: 返回结果数量

        Returns:
            包含元数据的检索文档列表
        """
        try:
            results = self.vector_store.similarity_search(question, k=k)

            processed_results = []
            for doc in results:
                result = {
                    "content": doc.page_content,
                    "source": doc.metadata.get("source_file", "Unknown"),
                    "chunk_id": doc.metadata.get("chunk_id", ""),
                    "doc_id": doc.metadata.get("doc_id", "")
                }
                processed_results.append(result)

            return processed_results
        except Exception as e:
            print(f"查询时出错: {e}")
            return []

    def generate_answer(self, question: str, context: List[Dict[str, Any]]) -> str:
        """
        使用 LLM 生成答案（如果可用）

        Args:
            question: 用户问题
            context: 检索到的上下文

        Returns:
            生成的答案
        """
        if not self.llm_model:
            return "LLM 模型不可用。以下是相关文档:\n" + \
                   "\n".join([f"- {doc['content'][:200]}..." for doc in context])

        # 合并上下文
        context_text = "\n\n".join([doc["content"] for doc in context])

        prompt = f"""根据以下内容，回答问题。

内容:
{context_text}

问题: {question}

答案:"""

        try:
            response = self.llm_model.invoke(prompt)
            return response
        except Exception as e:
            return f"生成答案时出错: {e}"

    def get_statistics(self) -> Dict[str, Any]:
        """获取系统统计信息"""
        try:
            total_docs = len(self.documents)
            vector_count = self.vector_store._collection.count() if self.vector_store._collection else 0

            return {
                "total_documents": total_docs,
                "vector_store_documents": vector_count,
                "collection_name": self.collection_name,
                "persist_directory": self.persist_directory
            }
        except Exception as e:
            print(f"获取统计信息失败: {e}")
            return {}

    @abstractmethod
    def get_rag_type(self) -> str:
        """获取 RAG 类型标识符 - 由子类实现"""
        pass