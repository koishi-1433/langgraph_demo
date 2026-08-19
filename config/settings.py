"""
配置层：配置管理模块
"""
import os
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
    MCP_HTTP_HOST: str = "0.0.0.0"
    MCP_HTTP_PORT: int = 8001
    # MCP客户端json配置
    MCP_CLIENT: MultiServerMCPClient = MultiServerMCPClient(
        {
            # stdio 交互方式
            "mcp_stdio_server": {
                "command": r"D:\miniconda3\envs\myenv312\python.exe",
                "args": [
                    MCP_SERVER_SCRIPT
                ],
                "transport": "stdio",
            },
            # streamable-http 交互方式
            # "mcp_http_server": {
            #         "transport": "http",
            #         "url": f"http://{MCP_HTTP_HOST}:{MCP_HTTP_PORT}/mcp",
            # },
        }
    )

    # 工作流配置
    MAX_REVIEW_ITERATIONS: int = 3
    REVIEW_PASS_SCORE: int = 75

    model_config = SettingsConfigDict(
        env_file=str(_ENV_FILE),
        env_file_encoding='utf-8',
        extra="ignore"
    )


settings = Settings()