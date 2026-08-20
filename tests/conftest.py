"""pytest 全局配置：确保 langgraph_demo 包可从项目父目录导入。"""
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试环境不携带 MCP_PYTHON_PATH，保证 settings 默认值可测
os.environ.pop("MCP_PYTHON_PATH", None)


def pytest_addoption(parser):
    parser.addoption(
        "--run-network",
        action="store_true",
        default=False,
        help="运行需要真实网络/外部 API 的测试（Tavily / 高德 / 12306）",
    )


def pytest_configure(config):
    config.addinivalue_line("markers", "network: 需要真实网络/外部 API 的测试")


def pytest_collection_modifyitems(config, items):
    if config.getoption("--run-network"):
        return
    skip_network = pytest.mark.skip(reason="需要 --run-network 参数（真实网络）")
    for item in items:
        if "network" in item.keywords:
            item.add_marker(skip_network)
