"""
database_manager.py
==================

RAG 系统的数据库管理。提供不同向量数据库的统一接口。
"""

import json
import os
from datetime import datetime
from typing import Optional, Dict, Any, List
from abc import ABC, abstractmethod

from langchain_core.vectorstores import VectorStore
from langchain_core.embeddings import Embeddings
from langchain_chroma import Chroma
from langchain_community.vectorstores import FAISS

from MCP.core.logger import get_logger

logger = get_logger(__name__)


class VectorDatabase(ABC):
    """向量数据库的抽象基类"""

    @abstractmethod
    def initialize(self, embedding_model: Embeddings, persist_directory: str):
        """初始化向量数据库"""
        pass

    @abstractmethod
    def add_documents(self, documents: List[Any]):
        """添加文档到数据库"""
        pass

    @abstractmethod
    def similarity_search(self, query: str, k: int = 4):
        """执行相似性搜索"""
        pass

    @abstractmethod
    def get_document_count(self) -> int:
        """获取总文档数量"""
        pass


class ChromaDatabase(VectorDatabase):
    """Chroma 向量数据库实现"""

    def __init__(self, collection_name: str = "rag_collection"):
        self.collection_name = collection_name
        self.vector_store: Optional[Chroma] = None

    def initialize(self, embedding_model: Embeddings, persist_directory: str):
        """初始化 Chroma 数据库"""
        chroma_path = os.path.join(persist_directory, "chroma")
        os.makedirs(chroma_path, exist_ok=True)

        self.vector_store = Chroma(
            collection_name=self.collection_name,
            embedding_function=embedding_model,
            persist_directory=chroma_path
        )
        print(f"Chroma 数据库已初始化: {chroma_path}")

    def add_documents(self, documents: List[Any]):
        """添加文档到 Chroma"""
        if not self.vector_store:
            raise ValueError("数据库未初始化")
        self.vector_store.add_documents(documents)

    def similarity_search(self, query: str, k: int = 4):
        """在 Chroma 中搜索"""
        if not self.vector_store:
            raise ValueError("数据库未初始化")
        return self.vector_store.similarity_search(query, k=k)

    def get_document_count(self) -> int:
        """从 Chroma 获取文档数量"""
        if not self.vector_store or not self.vector_store._collection:
            return 0
        return self.vector_store._collection.count()


class FAISSDatabase(VectorDatabase):
    """FAISS 向量数据库实现"""

    # 索引清单格式版本；不兼容时重建，避免反序列化结构不符的旧 pickle
    MANIFEST_SCHEMA = 1

    def __init__(self, index_name: str = "rag_index"):
        self.index_name = index_name
        self.vector_store: Optional[FAISS] = None
        self._embedding_signature = ""

    @staticmethod
    def _as_signature(embedding_model: Embeddings) -> str:
        """尽力提取嵌入模型标识；取不到时返回空串（跳过签名校验）。"""
        for attr in ("model_name", "model", "model_path"):
            value = getattr(embedding_model, attr, None)
            if isinstance(value, str) and value:
                return value
        return ""

    def _manifest_path(self, faiss_path: str) -> str:
        return os.path.join(faiss_path, f"{self.index_name}.manifest.json")

    def _read_manifest(self, path: str) -> Dict[str, Any]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _write_manifest(self, path: str):
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump({
                    "schema": self.MANIFEST_SCHEMA,
                    "index_name": self.index_name,
                    "embedding_signature": self._embedding_signature,
                    "saved_at": datetime.now().isoformat(timespec="seconds"),
                }, f, ensure_ascii=False, indent=2)
        except Exception as e:
            logger.warning(f"FAISS 索引清单写入失败（不影响索引本身）: {e}")

    @staticmethod
    def _new_empty_store(embedding_model: Embeddings) -> FAISS:
        from langchain_core.documents import Document
        return FAISS.from_documents([Document(page_content="")], embedding_model)

    def initialize(self, embedding_model: Embeddings, persist_directory: str):
        """初始化：优先复用已有索引，缺失/损坏/清单不兼容时重建空索引（DEF-01）。"""
        faiss_path = os.path.join(persist_directory, "faiss")
        os.makedirs(faiss_path, exist_ok=True)

        index_path = os.path.join(faiss_path, f"{self.index_name}.faiss")
        manifest_path = self._manifest_path(faiss_path)
        self._embedding_signature = self._as_signature(embedding_model)

        if os.path.exists(index_path):
            manifest = self._read_manifest(manifest_path)
            saved_sig = manifest.get("embedding_signature", "") or ""
            compatible = (
                manifest.get("schema") == self.MANIFEST_SCHEMA
                and (not saved_sig or not self._embedding_signature
                     or saved_sig == self._embedding_signature)
            )
            if compatible:
                try:
                    # 仅加载本程序 save_local() 自产的索引；绝不用于外部/上传文件
                    self.vector_store = FAISS.load_local(
                        faiss_path,
                        embedding_model,
                        index_name=self.index_name,
                        allow_dangerous_deserialization=True,
                    )
                    logger.info(f"FAISS 数据库已加载: {faiss_path}")
                    return
                except Exception as e:
                    logger.error(f"FAISS 索引加载失败，将重建空索引: {e}")
            else:
                logger.warning(
                    f"FAISS 索引清单不兼容（schema={manifest.get('schema')} / "
                    f"模型={saved_sig or '未知'}），重建空索引: {manifest_path}"
                )

        self.vector_store = self._new_empty_store(embedding_model)
        self._write_manifest(manifest_path)
        logger.info(f"FAISS 数据库已创建: {faiss_path}")

    def add_documents(self, documents: List[Any]):
        """添加文档到 FAISS"""
        if not self.vector_store:
            raise ValueError("数据库未初始化")
        self.vector_store.add_documents(documents)

    def similarity_search(self, query: str, k: int = 4):
        """在 FAISS 中搜索"""
        if not self.vector_store:
            raise ValueError("数据库未初始化")
        return self.vector_store.similarity_search(query, k=k)

    def get_document_count(self) -> int:
        """从 FAISS 获取文档数量"""
        if not self.vector_store:
            return 0
        return self.vector_store.index.ntotal

    def save(self, persist_directory: str):
        """保存 FAISS 索引并写入清单"""
        if not self.vector_store:
            raise ValueError("数据库未初始化")

        faiss_path = os.path.join(persist_directory, "faiss")
        os.makedirs(faiss_path, exist_ok=True)
        self.vector_store.save_local(faiss_path, index_name=self.index_name)
        self._write_manifest(self._manifest_path(faiss_path))
        logger.info(f"FAISS 数据库已保存: {faiss_path}")


class DatabaseManager:
    """向量数据库管理器"""

    def __init__(self):
        self.databases: Dict[str, VectorDatabase] = {}
        self.default_database = None
        # 最近一次初始化失败原因（不再静默吞异常）
        self.last_error: Optional[str] = None

    def create_chroma_database(self,
                             db_id: str,
                             collection_name: str = "rag_collection") -> ChromaDatabase:
        """创建 Chroma 数据库"""
        database = ChromaDatabase(collection_name)
        self.databases[db_id] = database

        if self.default_database is None:
            self.default_database = db_id

        return database

    def create_faiss_database(self,
                            db_id: str,
                            index_name: str = "rag_index") -> FAISSDatabase:
        """创建 FAISS 数据库"""
        database = FAISSDatabase(index_name)
        self.databases[db_id] = database

        if self.default_database is None:
            self.default_database = db_id

        return database

    def get_database(self, db_id: Optional[str] = None) -> Optional[VectorDatabase]:
        """按 ID 获取数据库"""
        if db_id is None:
            db_id = self.default_database
        return self.databases.get(db_id)

    def initialize_database(self,
                          db_id: str,
                          embedding_model: Embeddings,
                          persist_directory: str) -> bool:
        """初始化数据库；失败原因写入 self.last_error，不再静默吞异常（DEF-01）。"""
        database = self.get_database(db_id)
        if not database:
            self.last_error = f"数据库 {db_id} 不存在"
            logger.error(self.last_error)
            return False

        try:
            database.initialize(embedding_model, persist_directory)
            self.last_error = None
            return True
        except Exception as e:
            self.last_error = f"{type(e).__name__}: {e}"
            logger.exception(f"初始化数据库 {db_id} 失败: {e}")
            return False

    def list_databases(self) -> Dict[str, str]:
        """列出可用数据库"""
        return {
            db_id: database.__class__.__name__
            for db_id, database in self.databases.items()
        }

    def set_default(self, db_id: str) -> bool:
        """设置默认数据库"""
        if db_id in self.databases:
            self.default_database = db_id
            return True
        return False

    def remove_database(self, db_id: str) -> bool:
        """移除数据库"""
        if db_id in self.databases:
            del self.databases[db_id]
            if self.default_database == db_id:
                self.default_database = next(iter(self.databases.keys()), None)
            return True
        return False


# Convenience functions

def create_chroma_manager() -> DatabaseManager:
    """使用 Chroma 创建数据库管理器"""
    manager = DatabaseManager()
    manager.create_chroma_database("default", "rag_collection")
    return manager


def create_dual_database_manager() -> DatabaseManager:
    """使用 Chroma 和 FAISS 创建数据库管理器"""
    manager = DatabaseManager()
    manager.create_chroma_database("chroma", "chroma_collection")
    manager.create_faiss_database("faiss", "faiss_index")
    return manager