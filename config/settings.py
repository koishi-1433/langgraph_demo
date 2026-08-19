"""
配置层：配置管理模块
"""
import os
import sys
from pathlib import Path
from typing import ClassVar
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic_settings import BaseSettings, SettingsConfigDict

# 确保 .env 从 config/ 目录加载（无论从哪个 cwd 运行）
_ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # LLM配置
    CHATGPT_FREE_API_KEY: str
    CHATGPT_FREE_BASE_URL: str
    CHATGPT_FREE_MODEL_NAME: str

    # NVIDIA配置
    NVIDIA_API_KEY: str
    NVIDIA_BASE_URL: str
    NVIDIA_MODEL_NAME: str

    # deepseek 配置
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL_NAME: str

    # 高德地图配置
    AMAP_API_KEY: str

    # TAVILY配置
    TAVILY_API_KEY: str
    TAVILY_MAX_RESULTS: int = 5

    # 日志配置
    LOG_LEVEL: str = 'INFO'
    LOG_FILE: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs/app.log")

    # MCP配置
    BASE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent
    MCP_SERVER_SCRIPT: str = str(BASE_DIR / "mcp_server" / "mcp_server_stdio.py")
    MCP_SERVER_NAME: str = "mcp_server_stdio"
    # MCP 子进程解释器：优先读环境变量，默认用当前 Python 解释器
    MCP_PYTHON_PATH: str = os.getenv("MCP_PYTHON_PATH", sys.executable)
    # MCP客户端json配置
    MCP_CLIENT: MultiServerMCPClient = MultiServerMCPClient(
        {
            # stdio 交互方式
            "mcp_stdio_server": {
                "command": MCP_PYTHON_PATH,
                "args": [
                    MCP_SERVER_SCRIPT
                ],
                "transport": "stdio",
            },
        }
    )

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding='utf-8',
        extra="ignore"
    )


settings = Settings()