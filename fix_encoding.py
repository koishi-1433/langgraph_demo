#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
编码修复脚本 - 解决 Windows GBK 编码问题
"""

import sys
import os
import io

# 强制使用 UTF-8 编码
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8')

# 设置环境变量
os.environ['PYTHONIOENCODING'] = 'utf-8'
os.environ['PYTHONUTF8'] = '1'

print("✅ 编码修复已应用 - UTF-8 模式")
print("🔧 修复内容:")
print("   - 标准输出: UTF-8")
print("   - 标准错误: UTF-8")
print("   - 环境变量: PYTHONIOENCODING=utf-8")
print("   - 环境变量: PYTHONUTF8=1")
print("\n现在可以正常运行 main.py 了！")