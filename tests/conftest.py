"""pytest 全局配置：确保 langgraph_demo 包可从项目父目录导入。"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# 测试环境不携带 MCP_PYTHON_PATH，保证 settings 默认值可测
os.environ.pop("MCP_PYTHON_PATH", None)
