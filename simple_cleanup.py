#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
简单项目清理脚本
"""

import os
import shutil
import glob
from pathlib import Path

def clean_project():
    """清理项目中的无用文件"""

    project_root = Path(__file__).parent

    print("清理项目中的无用文件...")
    print("=" * 50)

    # 要清理的文件和目录模式
    # ⚠️ 禁止删除 .git 与源码/资源目录：
    # memory/ 被 frontend_api.py 直接导入，web/ 是前端静态资源，
    # 误删会导致 Web API 启动即 ImportError（P0-2 根因）。
    # 这里只清理构建/缓存产物。
    patterns_to_clean = [
        '**/__pycache__',
        '**/*.pyc',
        '**/*.log',
        '**/.idea',
        '**/logs',
    ]

    deleted_files = 0
    deleted_dirs = 0
    freed_space = 0

    for pattern in patterns_to_clean:
        for item in project_root.glob(pattern):
            try:
                if item.is_file():
                    freed_space += item.stat().st_size
                    item.unlink()
                    deleted_files += 1
                    print(f"删除文件: {item.relative_to(project_root)}")
                elif item.is_dir():
                    # 计算目录大小
                    dir_size = sum(f.stat().st_size for f in item.rglob('*') if f.is_file())
                    freed_space += dir_size
                    shutil.rmtree(item)
                    deleted_dirs += 1
                    print(f"删除目录: {item.relative_to(project_root)} ({dir_size:,} bytes)")
            except Exception as e:
                print(f"删除失败 {item}: {e}")

    print("\n" + "=" * 50)
    print(f"清理完成!")
    print(f"删除文件: {deleted_files} 个")
    print(f"删除目录: {deleted_dirs} 个")
    print(f"释放空间: {freed_space:,} bytes ({freed_space/1024/1024:.1f} MB)")

def show_core_structure():
    """显示核心项目结构"""

    project_root = Path(__file__).parent

    print("\n核心项目结构:")
    print("=" * 50)

    core_files = [
        'main.py',
        '.env',
        'MCP/protocol/server.py',
        'MCP/config/settings.py',
        'MCP/core/logger.py',
        'MCP/core/llm_client.py',
        'MCP/integration/tool_adapter.py',
        'MCP/workflow/graph.py',
        'MCP/agents/specialized/route_agent.py',
        'MCP/agents/specialized/intent_recognizer.py',
        'MCP/agents/specialized/search_agent.py',
        'MCP/agents/specialized/travel_agent.py',
        'MCP/tool_amap/client.py',
        'MCP/tool_amap/route_planner.py',
    ]

    for file_path in core_files:
        full_path = project_root / file_path
        if full_path.exists():
            size = full_path.stat().st_size if full_path.is_file() else 0
            print(f"  ✓ {file_path:<40} ({size:,} bytes)")
        else:
            print(f"  ❌ {file_path:<40} (不存在)")

if __name__ == "__main__":
    clean_project()
    show_core_structure()