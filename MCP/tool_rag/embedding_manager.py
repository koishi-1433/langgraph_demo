"""
embedding_manager.py
==================

RAG 系统的嵌入模型管理（仅本地模型，在线 DashScope 嵌入路径已移除）。

所有模型统一放在 MCP/tool_rag/mymodel 下，运行时离线加载，不再联网下载。
根目录由 EMBEDDING_MODEL_DIR 指定（相对路径按项目根解析），默认即包内 mymodel。
"""

import os
from pathlib import Path
from typing import Optional, Dict

from langchain_core.embeddings import Embeddings
from langchain_huggingface import HuggingFaceEmbeddings


_PACKAGE_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _PACKAGE_DIR.parents[1]
DEFAULT_EMBEDDING_MODEL_DIR = _PACKAGE_DIR / "mymodel"


def get_embedding_model_dir() -> Path:
    """解析本地模型根目录：优先 EMBEDDING_MODEL_DIR，相对路径按项目根解析。"""
    raw = os.getenv("EMBEDDING_MODEL_DIR", "").strip()
    if not raw:
        return DEFAULT_EMBEDDING_MODEL_DIR
    path = Path(raw)
    if not path.is_absolute():
        path = _PROJECT_ROOT / path
    return path


# 模型别名 -> mymodel 下的相对子目录（ModelScope 下载结构：别名/组织/模型名）
_MODEL_PATHS = {
    "bge-m3": Path("bge-m3") / "BAAI" / "bge-m3",
    "bce": Path("bce") / "maidalun" / "bce-embedding-base_v1",
    "bce-embedding-base_v1": Path("bce") / "maidalun" / "bce-embedding-base_v1",
}


def get_default_model_alias() -> str:
    """默认本地模型别名，可由 .env 的 EMBEDDING_MODEL 覆盖。"""
    return os.getenv("EMBEDDING_MODEL", "bge-m3").strip() or "bge-m3"


def resolve_local_model_path(model_name: str, model_path: Optional[str] = None) -> str:
    """把别名解析为本地绝对路径并校验完整性；找不到直接报错，不再静默联网下载。"""
    root = get_embedding_model_dir()
    if model_path:
        candidate = Path(model_path)
        if not candidate.is_absolute():
            candidate = root / candidate
    else:
        rel = _MODEL_PATHS.get(model_name)
        if rel is None:
            raise ValueError(
                f"未知的本地嵌入模型别名 {model_name!r}；可选：{sorted(_MODEL_PATHS)}，"
                f"或通过 model_path= 指定本地目录"
            )
        candidate = root / rel

    candidate = candidate.resolve()
    if not (candidate / "config.json").is_file():
        raise FileNotFoundError(
            f"本地嵌入模型目录不完整（缺少 config.json）：{candidate}；"
            f"当前 EMBEDDING_MODEL_DIR={root}"
        )
    return str(candidate)


class EmbeddingManager:
    """本地嵌入模型注册表（在线路径已移除）"""

    def __init__(self):
        self.models: Dict[str, Embeddings] = {}
        self.default_model: Optional[str] = None

    def create_local_embedding(self,
                             model_name: Optional[str] = None,
                             model_path: Optional[str] = None,
                             device: Optional[str] = None,
                             **kwargs) -> Embeddings:
        """
        创建本地 HuggingFace 嵌入模型（离线）

        Args:
            model_name: 本地模型别名（bge-m3 / bce），默认取 EMBEDDING_MODEL
            model_path: 显式指定本地模型目录（优先于别名映射）
            device: "cuda" / "cpu"，默认自动检测
            **kwargs: 支持 model_kwargs / encode_kwargs 覆盖

        Returns:
            嵌入实例
        """
        model_name = (model_name or get_default_model_alias()).strip()
        resolved_path = resolve_local_model_path(model_name, model_path)

        if device is None:
            import torch
            device = "cuda" if torch.cuda.is_available() else "cpu"

        model_kwargs = {'device': device}
        if kwargs.get('model_kwargs'):
            model_kwargs.update(kwargs['model_kwargs'])

        encode_kwargs = {'normalize_embeddings': True, 'batch_size': 32}
        if kwargs.get('encode_kwargs'):
            encode_kwargs.update(kwargs['encode_kwargs'])

        embedding = HuggingFaceEmbeddings(
            model_name=resolved_path,
            model_kwargs=model_kwargs,
            encode_kwargs=encode_kwargs
        )

        model_id = f"local_{model_name}"
        self.models[model_id] = embedding
        if self.default_model is None:
            self.default_model = model_id

        print(f"本地嵌入模型已创建: {model_name} @ {resolved_path} ({device})")
        return embedding

    def get_embedding(self, model_id: Optional[str] = None) -> Optional[Embeddings]:
        """按ID获取嵌入模型；不传则使用默认模型"""
        if model_id is None:
            model_id = self.default_model
        return self.models.get(model_id)

    def list_models(self) -> Dict[str, str]:
        """列出已注册的嵌入模型"""
        return {
            model_id: str(embedding.__class__.__name__)
            for model_id, embedding in self.models.items()
        }

    def set_default(self, model_id: str) -> bool:
        """设置默认嵌入模型"""
        if model_id in self.models:
            self.default_model = model_id
            return True
        return False

    def remove_model(self, model_id: str) -> bool:
        """移除嵌入模型；移除默认项时自动回退"""
        if model_id in self.models:
            del self.models[model_id]
            if self.default_model == model_id:
                self.default_model = next(iter(self.models.keys()), None)
            return True
        return False


# Convenience functions

def create_bge_embedding(model_path: Optional[str] = None, device: Optional[str] = None) -> Embeddings:
    """创建本地 BGE-M3 嵌入模型（MCP/tool_rag/mymodel/bge-m3/BAAI/bge-m3）"""
    manager = EmbeddingManager()
    return manager.create_local_embedding(
        model_name="bge-m3", model_path=model_path, device=device
    )


def create_bce_embedding(model_path: Optional[str] = None, device: Optional[str] = None) -> Embeddings:
    """创建本地 BCE 嵌入模型（MCP/tool_rag/mymodel/bce/maidalun/bce-embedding-base_v1）"""
    manager = EmbeddingManager()
    return manager.create_local_embedding(
        model_name="bce", model_path=model_path, device=device
    )


def create_mock_embedding() -> Embeddings:
    """创建用于测试的模拟嵌入（128 维常数向量，不能用于真实检索）"""
    class MockEmbeddings:
        def embed_documents(self, texts):
            return [[0.1] * 128 for _ in texts]

        def embed_query(self, text):
            return [0.1] * 128

    return MockEmbeddings()
