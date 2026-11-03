#!/usr/bin/env python3
"""
调试坐标传递问题
"""
import asyncio
import os
import sys

# 添加项目根目录到Python路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from MCP.workflow.graph import TravelPlanWorkflow

def debug_coordinate_flow():
    """调试坐标传递流程"""

    # 模拟前端传递的GPS坐标
    gps_lat = 39.04380407373195
    gps_lng = 117.72074659825327

    print("=== 坐标传递流程分析 ===")
    print(f"1. 前端传递的GPS坐标:")
    print(f"   lat = {gps_lat}")
    print(f"   lng = {gps_lng}")

    # 前端API的处理
    gps_coords = f"{gps_lat},{gps_lng}"
    print(f"\n2. 前端API构造的gps_coords字符串:")
    print(f"   gps_coords = '{gps_coords}'")

    # 工作流的解析
    parts = gps_coords.split(",")
    if len(parts) == 2:
        lat = float(parts[0].strip())
        lng = float(parts[1].strip())
    print(f"\n3. 工作流解析后的坐标:")
    print(f"   lat = {lat}")
    print(f"   lng = {lng}")

    # RouteAgent的处理
    user_real_location = f"{lng},{lat}"
    print(f"\n4. RouteAgent构造的user_real_location:")
    print(f"   user_real_location = '{user_real_location}'")

    print(f"\n5. 高德地图API需要的格式是: 经度,纬度")
    print(f"   所以正确的格式应该是: '117.72074659825327,39.04380407373195'")
    print(f"   RouteAgent生成的格式是: '{user_real_location}'")

    # 检查是否匹配
    expected = f"{gps_lng},{gps_lat}"
    print(f"\n6. 检查:")
    print(f"   期望值: '{expected}'")
    print(f"   实际值: '{user_real_location}'")
    print(f"   是否匹配: {expected == user_real_location}")

if __name__ == "__main__":
    debug_coordinate_flow()