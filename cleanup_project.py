#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
项目清理和化简工具
移除无用文件，保留核心功能
"""

import os
import shutil
import json
from pathlib import Path

def analyze_project_structure():
    """分析项目结构，识别核心文件和无用文件"""

    project_root = Path(__file__).parent

    # 核心功能文件（必须保留）
    core_files = {
        'main.py': '主程序入口',
        'MCP/protocol/server.py': 'MCP服务器核心',
        'MCP/config/settings.py': '配置文件',
        'MCP/core/logger.py': '日志模块',
        'MCP/core/llm_client.py': 'LLM客户端',
        'MCP/integration/tool_adapter.py': '工具适配器',
        'MCP/workflow/graph.py': '工作流引擎',
        'MCP/agents/specialized/route_agent.py': '路线规划代理',
        'MCP/agents/specialized/intent_recognizer.py': '意图识别',
        'MCP/agents/specialized/search_agent.py': '搜索代理',
        'MCP/agents/specialized/travel_agent.py': '旅游代理',
        'MCP/tool_amap/client.py': '高德地图客户端',
        'MCP/tool_amap/route_planner.py': '路线规划器',
        '.env': '环境变量配置'
    }

    # 可删除的无用文件
    useless_patterns = [
        '__pycache__',      # Python缓存
        '*.pyc',           # 编译文件
        '*.log',           # 日志文件
        '.idea',           # IDE配置
        # ⚠️ 禁止删除 .git：版本库元数据，删除后历史无法恢复
        '*.tmp',           # 临时文件
        'backup',          # 备份文件
        'test_*',          # 测试文件
        'example_*',       # 示例文件
        'demo_*',          # 演示文件
        'old_*',           # 旧版本文件
        'draft_*',         # 草稿文件
        'temp_*',          # 临时文件
    ]

    # 可选删除的目录（仅供展示，自动清理只允许删 logs）
    # ⚠️ web/ 与 memory/ 是运行时依赖：frontend_api.py 会 import memory，
    # 删除后整个 Web API 无法启动（P0-2 根因），已禁止自动删除。
    optional_dirs = {
        'logs': '日志目录',
    }

    print("项目结构分析")
    print("=" * 50)

    # 检查核心文件
    print("\n✅ 核心文件:")
    for file_path, description in core_files.items():
        full_path = project_root / file_path
        if full_path.exists():
            size = full_path.stat().st_size if full_path.is_file() else 0
            print(f"  ✓ {file_path:<30} ({size:,} bytes) - {description}")
        else:
            print(f"  ❌ {file_path:<30} - {description}")

    # 查找无用文件
    print("\n🗑️  可删除的无用文件:")
    useless_files = []
    for pattern in useless_patterns:
        if '*' in pattern:
            # 通配符匹配
            for file_path in project_root.rglob(pattern):
                if file_path.exists():
                    useless_files.append(file_path)
        else:
            # 目录名匹配
            for dir_path in project_root.rglob(pattern):
                if dir_path.is_dir():
                    useless_files.append(dir_path)

    for file_path in sorted(set(useless_files))[:20]:  # 显示前20个
        try:
            if file_path.is_file():
                size = file_path.stat().st_size
                print(f"  🗑️  {file_path.relative_to(project_root):<40} ({size:,} bytes)")
            elif file_path.is_dir():
                print(f"  🗂️  {file_path.relative_to(project_root):<40} (目录)")
        except Exception:
            continue

    # 可选删除目录
    print("\n❓ 可选删除的目录:")
    for dir_name, description in optional_dirs.items():
        dir_path = project_root / dir_name
        if dir_path.exists():
            print(f"  📁 {dir_name:<20} - {description}")

    return core_files, useless_files, optional_dirs

def clean_project(remove_optional=False):
    """清理项目"""

    project_root = Path(__file__).parent

    print("🧹 开始清理项目...")
    print("=" * 50)

    # 删除无用文件
    deleted_count = 0
    freed_space = 0

    # 清理 __pycache__ 和 .pyc 文件
    for pattern in ['__pycache__', '*.pyc']:
        for item in project_root.rglob(pattern):
            try:
                if item.is_file():
                    freed_space += item.stat().st_size
                    item.unlink()
                    deleted_count += 1
                elif item.is_dir():
                    shutil.rmtree(item)
                    deleted_count += 1
            except Exception as e:
                print(f"  ⚠️  删除失败 {item}: {e}")

    # 清理日志文件
    for log_file in project_root.rglob('*.log'):
        try:
            freed_space += log_file.stat().st_size
            log_file.unlink()
            deleted_count += 1
        except Exception as e:
            print(f"  ⚠️  删除失败 {log_file}: {e}")

    # 可选清理：只允许删日志，绝不自动删 web / memory / instruction / data
    if remove_optional:
        optional_dirs = ['logs']
        for dir_name in optional_dirs:
            dir_path = project_root / dir_name
            if dir_path.exists():
                try:
                    shutil.rmtree(dir_path)
                    print(f"  🗑️  已删除目录: {dir_name}")
                    deleted_count += 1
                except Exception as e:
                    print(f"  ⚠️  删除失败 {dir_name}: {e}")

    print(f"\n✅ 清理完成!")
    print(f"   删除项目: {deleted_count} 个")
    print(f"   释放空间: {freed_space:,} bytes ({freed_space/1024/1024:.1f} MB)")

def create_minimal_structure():
    """创建最小化项目结构"""

    project_root = Path(__file__).parent

    minimal_structure = {
        'core': {
            'main.py': '主程序入口',
            'server.py': 'MCP服务器',
            'config.py': '配置文件',
            'workflow.py': '工作流引擎',
        },
        'agents': {
            'route_agent.py': '路线规划',
            'intent_recognizer.py': '意图识别',
            'search_agent.py': '搜索功能',
        },
        'tools': {
            'amap_client.py': '高德地图',
            'mcp_adapter.py': 'MCP适配器',
        },
        'data': {
            '.env': '环境配置',
            'requirements.txt': '依赖列表',
        }
    }

    print("📐 最小化项目结构建议")
    print("=" * 50)

    for category, files in minimal_structure.items():
        print(f"\n📁 {category.upper()}:")
        for file_name, description in files.items():
            print(f"   📄 {file_name:<20} - {description}")

def main():
    """主函数"""
    print("MCP 旅游助手项目清理工具")
    print("=" * 60)

    # 分析项目结构
    core_files, useless_files, optional_dirs = analyze_project_structure()

    print("\n" + "=" * 60)
    print("💡 建议操作:")
    print("   1. 清理缓存和临时文件（安全）")
    print("   2. 删除可选目录（如果需要简化）")
    print("   3. 查看最小化结构建议")

    choice = input("\n请选择操作 (1-3, 直接回车退出): ").strip()

    if choice == '1':
        clean_project(remove_optional=False)
    elif choice == '2':
        clean_project(remove_optional=True)
    elif choice == '3':
        create_minimal_structure()
    else:
        print("👋 退出清理工具")

if __name__ == "__main__":
    main()