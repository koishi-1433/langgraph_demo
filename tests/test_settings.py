"""settings 配置外置测试：MCP_PYTHON_PATH 默认值与环境变量覆盖。"""
import sys

from langgraph_demo.config.settings import Settings, settings


def test_default_python_path_is_current_interpreter():
    # conftest 已清除环境变量，此处应命中 sys.executable 默认值
    assert settings.MCP_PYTHON_PATH == sys.executable


def test_env_var_overrides(monkeypatch):
    monkeypatch.setenv("MCP_PYTHON_PATH", r"C:\custom\python.exe")
    s = Settings()
    assert s.MCP_PYTHON_PATH == r"C:\custom\python.exe"
    # MCP 客户端使用配置的解释器
    assert s.MCP_CLIENT is not None


def test_settings_required_env_loaded():
    # .env 已加载，密钥字段非空（仅验证可实例化，不校验有效性）
    assert settings.CHATGPT_FREE_API_KEY
    assert settings.AMAP_API_KEY
    assert settings.TAVILY_API_KEY
