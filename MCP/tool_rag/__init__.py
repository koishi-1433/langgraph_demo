"""
RAG Tools Package
=================

一个解耦的、独立的 RAG（检索增强生成）工具包，已接入主程序：
MCP Server 的 rag_* 工具 → rag_service 单例 → TravelRAG → Chroma + 本地 bge-m3。

主要组件:
- BaseRAG: 核心 RAG 功能
- ParentChildRAG: 父子分块策略
- DualVectorRAG: 双向量数据库方法
- TravelRAG: 旅游助手专用实现（主链路使用）
- EmbeddingManager: 本地嵌入模型管理
- DatabaseManager: 向量数据库管理
- RAGService / get_rag_service: 主程序接入层（懒加载、单例、优雅降级）

实验性能力（功能已验证，但主链路未调用）：
- ParentChildRAG / DualVectorRAG / DatabaseManager 面向后续检索策略扩展；
  当前 rag_service 固定使用 TravelRAG + Chroma，启用高级策略需显式配置工厂。
"""

__version__ = "0.2.0"
__author__ = "Claude Code"

from .base_rag import BaseRAG
from .parent_child_rag import ParentChildRAG
from .dual_vector_rag import DualVectorRAG
from .travel_rag import TravelRAG
from .embedding_manager import EmbeddingManager
from .database_manager import DatabaseManager
from .rag_service import RAGService, get_rag_service

__all__ = [
    'BaseRAG',
    'ParentChildRAG',
    'DualVectorRAG',
    'TravelRAG',
    'EmbeddingManager',
    'DatabaseManager',
    'RAGService',
    'get_rag_service',
]
