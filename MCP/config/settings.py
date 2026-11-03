"""
配置层：配置管理模块
"""
import os
from pathlib import Path
from typing import ClassVar
from dotenv import load_dotenv
from pydantic_settings import BaseSettings, SettingsConfigDict

load_dotenv()


class Settings(BaseSettings):
    # ============ LLM 配置（ChatGPT Free / chatanywhere，OpenAI 兼容协议）============
    # 全项目唯一的 LLM 凭据来源：所有 Agent 均经 LLMClientFactory -> LLMClient 使用下面三项
    CHATGPT_FREE_API_KEY: str
    CHATGPT_FREE_BASE_URL: str = "https://api.chatanywhere.tech/v1"
    CHATGPT_FREE_MODEL_NAME: str = "gpt-4o-mini"
    LLM_TIMEOUT: int = 60

    # ============ 高德 / 检索 / 嵌入 ============
    AMAP_API_KEY: str
    # 本地嵌入模型别名与根目录；在线嵌入已移除，由 embedding_manager.py 直接读取
    EMBEDDING_MODEL: str = "bge-m3"
    EMBEDDING_MODEL_DIR: str = "MCP/tool_rag/mymodel"
    AMAP_VECTORSTORE_PATH: str = str(Path(__file__).resolve().parent.parent / "data" / "vectorstore")

    # ============ RAG 知识库（tool_rag 接入主程序）============
    # 关闭后 rag_* 工具直接返回 disabled，工作流自动跳过检索，不影响其它能力
    RAG_ENABLED: bool = True
    # 向量库持久化目录（相对路径按项目根解析）
    RAG_PERSIST_DIR: str = "MCP/tool_rag/rag_db"
    RAG_COLLECTION_NAME: str = "travel_knowledge"
    RAG_TOP_K: int = 4
    # 归一化向量下的 L2 距离上限，约等价于余弦相似度 >= 0.4；超阈值片段不展示
    RAG_MAX_DISTANCE: float = 1.10
    RAG_CHUNK_SIZE: int = 500
    RAG_CHUNK_OVERLAP: int = 80

    # TAVILY配置
    TAVILY_API_KEY: str
    TAVILY_MAX_RESULTS: int = 5

    # 日志配置
    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs/app.log")

    # MCP配置
    BASE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent
    MCP_SERVER_SCRIPT: str = str(BASE_DIR / "protocol" / "server.py")
    MCP_SERVER_NAME: str = "route_mcp"
    MCP_HTTP_HOST: str = "0.0.0.0"
    MCP_HTTP_PORT: int = 9000
    # 为空 = 同进程直连（默认，全项目统一）；非空 = 走远程 Streamable HTTP
    # MCP 端点（如 http://127.0.0.1:9000/mcp），由 client.py 的
    # create_mcp_client() 统一切换，Agent 不再各自持有 URL。
    MCP_HTTP_URL: str = ""

    # 工作流配置
    MAX_REVIEW_ITERATIONS: int = 3
    REVIEW_PASS_SCORE: int = 75

    model_config = SettingsConfigDict(
        env_file='.env',
        env_file_encoding='utf-8',
        extra="ignore"
    )


settings = Settings()
