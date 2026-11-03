@echo off
chcp 65001 > nul
set PYTHONIOENCODING=utf-8
set PYTHONUTF8=1
cd /d "%~dp0"
echo 🚀 正在启动 MCP 服务器（Streamable HTTP /mcp）...
python -X utf8 MCP/protocol/server.py
pause