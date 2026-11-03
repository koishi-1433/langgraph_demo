import asyncio
from MCP.tool_amap.route_planner import RoutePlanner
from MCP.config.settings import settings

# 测试用：你当前定位真实经纬度（浏览器控制台打印 window._cachedGPS 拿）
TEST_USER_GPS = "117.632,39.075"   # 换成你真实GPS坐标
TEST_CITY = "天津市"

async def test_calc_to_station():
    # 初始化路径规划器
    planner = RoutePlanner(amap_key=settings.AMAP_API_KEY)

    # 1. 逆地理编码
    print("===== 1. 用户坐标逆地理编码 =====")
    addr_info = await planner.amap.regeocode(TEST_USER_GPS)
    print("定位地址信息：", addr_info)

    # 2. 查找附近火车站
    print("\n===== 2. 查找附近火车站 =====")
    stations = await planner.amap.find_train_stations(TEST_USER_GPS, radius=80000)
    if not stations:
        print("未找到火车站！")
        return

    first_station = stations[0]
    station_name = first_station.get("name", "")
    station_loc = first_station.get("location", "")
    print("最近车站名称：", station_name)
    print("最近车站坐标：", station_loc)

    # 3. 核心：计算 用户GPS → 火车站 驾车距离
    print("\n===== 3. 计算家到车站 真实距离 =====")
    route_res = await planner.amap.driving_route(TEST_USER_GPS, station_loc)
    print("高德接口原始返回：", route_res)

    # 4. 解析距离、用时
    to_station_dist = 0.0
    to_station_dur = 0.0
    if route_res and route_res.get("status") == "1":
        paths = route_res.get("route", {}).get("paths", [])
        if paths:
            path = paths[0]
            to_station_dist = float(path.get("distance", 0))
            to_station_dur = float(path.get("duration", 0))

    print(f"\n✅ 最终结果：")
    print(f"驾车距离：{to_station_dist / 1000:.1f} km")
    print(f"驾车用时：{int(to_station_dur / 60)} 分钟")

if __name__ == "__main__":
    asyncio.run(test_calc_to_station())