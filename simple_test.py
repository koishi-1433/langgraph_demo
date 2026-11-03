#!/usr/bin/env python3
"""
简单测试路线规划功能的两个问题
"""
import asyncio
import os
import sys
from datetime import datetime

# 添加项目根目录到Python路径
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_ROOT)

from MCP.workflow.graph import TravelPlanWorkflow

def save_output(test_name, user_input, gps_coords, output):
    """保存输出到文件"""
    log_dir = "logs"
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = os.path.join(log_dir, f"{test_name}_{timestamp}.txt")

    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"测试: {test_name}\n")
        f.write(f"时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
        f.write(f"用户输入: {user_input}\n")
        f.write(f"GPS坐标: {gps_coords}\n")
        f.write("\n" + "="*50 + "\n")
        f.write("输出结果:\n")
        f.write(output)
        f.write("\n" + "="*50 + "\n")

    print(f"结果已保存到: {filename}")
    return filename

async def run_tests():
    """运行测试"""
    workflow = TravelPlanWorkflow()

    async with workflow:
        # 测试1: "去上海的路线" (GPS定位)
        print("运行测试1...")
        gps_coords = "39.042865,117.698478"  # 滨海新区
        result1 = await workflow.run("去上海的路线", gps_coords=gps_coords)
        output1 = result1.get("final_output", "")
        save_output("test1_gps_route", "去上海的路线", gps_coords, output1)

        # 测试2: "从北京到上海的路线" (高德+12306)
        print("运行测试2...")
        result2 = await workflow.run("从北京到上海的路线", gps_coords=None)
        output2 = result2.get("final_output", "")
        save_output("test2_beijing_shanghai", "从北京到上海的路线", None, output2)

        # 分析问题
        print("\n分析结果:")
        analyze_outputs(output1, output2)

def analyze_outputs(output1, output2):
    """分析输出中的问题"""
    print("\n检查路线详情中的问题:")

    # 检查是否有"从 滨海新区 前往 滨海新区"的问题
    if "从 滨海新区 前往 滨海新区" in output1:
        print("❌ 测试1发现错误: 出现'从 滨海新区 前往 滨海新区'")
        print("  这表明起点和终点被错误地设置为同一个地点")
    else:
        print("✅ 测试1路线详情正常")

    if "从 滨海新区 前往 滨海新区" in output2:
        print("❌ 测试2发现错误: 出现'从 滨海新区 前往 滨海新区'")
    else:
        print("✅ 测试2路线详情正常")

    # 提取路线详情部分进行分析
    print("\n详细分析:")
    for i, output in enumerate([output1, output2], 1):
        lines = output.split('\n')
        in_route_details = False
        print(f"\n测试{i}的路线详情部分:")

        for line in lines:
            if "路线详情:" in line:
                in_route_details = True
                print(f"  {line}")
            elif in_route_details and line.strip().startswith("  "):
                print(f"  {line}")
            elif in_route_details and line.strip() and not line.strip().startswith("  "):
                break

if __name__ == "__main__":
    asyncio.run(run_tests())