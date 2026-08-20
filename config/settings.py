"""
配置层：配置管理模块
====================

- 所有配置变量集中在 Settings（pydantic-settings），从 config/.env 加载；
  复制 config/.env.example 为 config/.env 并填入真实密钥即可运行。
- 提供三组 LLM 服务商配置块：实际生效的是 NVIDIA_*（LLMClientFactory 的
  provider="openai"/"nvidia" 均读取 NVIDIA_*）；CHATGPT_FREE_* / DEEPSEEK_*
  为预留位（当前未接入 LLMClient），保持占位以便日后切换服务商。
- MCP 子进程的启动参数（解释器 + 脚本路径）也在本模块集中定义，
  保证 workflow / app / 测试各层引用的是同一份配置。
"""
import os
import sys
from pathlib import Path
from typing import ClassVar
from dotenv import load_dotenv
from langchain_mcp_adapters.client import MultiServerMCPClient
from pydantic_settings import BaseSettings, SettingsConfigDict

# 确保 .env 从 config/ 目录加载（无论从哪个 cwd 运行，路径都锚定到本文件所在目录）
_ENV_FILE = Path(__file__).resolve().parent / ".env"
load_dotenv(_ENV_FILE)


class Settings(BaseSettings):
    # ── LLM 服务商配置 ──────────────────────────────────────────────
    # 注意：以下三组配置中只有 NVIDIA_* 被 LLMClient 实际使用；
    # CHATGPT_FREE_* / DEEPSEEK_* 为预留占位（见模块 docstring）。
    # 每组均包含 API Key、Base URL、模型名三段，未来切换服务商只需改这里。

    # ChatGPT Free（预留，未启用）
    CHATGPT_FREE_API_KEY: str
    CHATGPT_FREE_BASE_URL: str
    CHATGPT_FREE_MODEL_NAME: str

    # NVIDIA（当前生效：provider="openai" / "nvidia" 都读这组配置）
    NVIDIA_API_KEY: str
    NVIDIA_BASE_URL: str
    NVIDIA_MODEL_NAME: str

    # DeepSeek（预留，未启用）
    DEEPSEEK_API_KEY: str
    DEEPSEEK_BASE_URL: str
    DEEPSEEK_MODEL_NAME: str

    # ── 高德地图（地理编码 / 逆地理编码 / 路线规划） ────────────────
    AMAP_API_KEY: str

    # ── Tavily 网页搜索 ─────────────────────────────────────────────
    TAVILY_API_KEY: str
    TAVILY_MAX_RESULTS: int = 5

    # ── 日志 ─────────────────────────────────────────────────────────
    LOG_LEVEL: str = 'INFO'                              # 文件日志级别（控制台恒为 DEBUG）
    LOG_FILE: str = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs/app.log")

    # ── MCP（stdio 子进程） ─────────────────────────────────────────
    BASE_DIR: ClassVar[Path] = Path(__file__).resolve().parent.parent   # 项目根目录 langgraph_demo/
    MCP_SERVER_SCRIPT: str = str(BASE_DIR / "mcp_server" / "mcp_server_stdio.py")  # MCP 服务端脚本
    MCP_SERVER_NAME: str = "mcp_server_stdio"
    # MCP 子进程解释器：优先读环境变量（可指向虚拟环境），默认用当前 Python 解释器
    MCP_PYTHON_PATH: str = os.getenv("MCP_PYTHON_PATH", sys.executable)
    # MCP 客户端实例（类属性，全进程共享一份）：以 stdio 方式拉起 MCP 服务端脚本。
    # 注意：此处在类定义期即创建实例，因此 MCP_PYTHON_PATH 等必须定义在它之前。
    MCP_CLIENT: MultiServerMCPClient = MultiServerMCPClient(
        {
            # stdio 交互方式：command = 解释器，args = [服务端脚本路径]
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
        # extra="ignore"：.env / 系统环境变量中的其他变量（如 PATH、MCP_PYTHON_PATH）不报错
        extra="ignore"
    )


settings = Settings()