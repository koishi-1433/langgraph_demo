"""
智能路径规划模块 - 火车路线修复版
===============

修复内容：
1. 三段式输出：市内→火车→市内
2. 总距离显示城市间总距离（不是仅到车站）
3. 第一段显示从城市中心点到出发站的驾车距离/用时
4. 第三段显示从到达站到目的地
"""
import sys
import os
import logging
from datetime import datetime
from typing import Dict, List, Optional
from .client import UnifiedAMapClient
from .train_crawler import Py12306Client
from .utils import estimate_train_cost, format_duration, format_distance, format_cost

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)



logger = logging.getLogger(__name__)


class RoutePlanningResult:
    """路径规划结果"""

    def __init__(self):
        self.transport_mode = ""
        self.total_distance = 0.0  # km - 城市间总距离
        self.total_duration = 0  # minutes - 总耗时（含市内+火车）
        self.total_cost = 0.0  # CNY
        self.walking_distance = 0.0  # meters
        self.route_details = []
        self.static_map_url = None
        self.train_info = None
        self.error = None
        self.raw_response = None
        # ✅ 新增：三段式详细信息
        self.segment_1 = {}  # 市内到出发站
        self.segment_2 = {}  # 火车段
        self.segment_3 = {}  # 到达站到目的地

    def to_dict(self) -> Dict:
        return {
            "transport_mode": self.transport_mode,
            "total_distance": round(self.total_distance, 2),
            "total_duration": self.total_duration,
            "total_cost": round(self.total_cost, 2),
            "walking_distance": round(self.walking_distance, 0),
            "route_details": self.route_details,
            "static_map_url": self.static_map_url,
            "train_info": self.train_info,
            "error": self.error,
            "segment_1": self.segment_1,
            "segment_2": self.segment_2,
            "segment_3": self.segment_3,
        }

    def __str__(self) -> str:
        if self.error:
            return f"Route planning failed: {self.error}"
        lines = [
            f"Transport: {self.transport_mode}",
            f"Distance: {format_distance(self.total_distance)}",
            f"Duration: {format_duration(self.total_duration)}",
            f"Cost: {format_cost(self.total_cost)}"
        ]
        if self.walking_distance > 0:
            lines.append(f"Walking: {self.walking_distance:.0f}m")
        if self.static_map_url:
            lines.append(f"Map: {self.static_map_url}")
        if self.train_info:
            lines.append(f"\nTrain Info:")
            train = self.train_info
            lines.extend([
                f"  Train: {train.get('train_number', 'N/A')}",
                f"  Time: {train.get('departure_time', 'N/A')} → {train.get('arrival_time', 'N/A')}",
                f"  Duration: {train.get('duration', 'N/A')}",
                f"  Second class: {train.get('second_class', 'N/A')}"
            ])
        return "\n".join(lines)


class RoutePlanner:
    """智能路径规划器 - 火车路线修复版"""

    def __init__(self, amap_key: str = None, enable_train_info: bool = True, enable_static_map: bool = True):
        self.amap_key = amap_key
        self.enable_train_info = enable_train_info and bool(amap_key)
        self.enable_static_map = enable_static_map and bool(amap_key)

        if amap_key:
            self.amap = UnifiedAMapClient(api_key=amap_key)
        else:
            self.amap = None

        if enable_train_info:
            try:
                self.train_client = Py12306Client(cache_enabled=True, timeout=15)
                print("✅ Py12306Client 火车票查询功能已启用")
            except Exception as e:
                print(f"⚠️ Py12306Client 初始化失败: {e}")
                self.train_client = None
                self.enable_train_info = False
        else:
            self.train_client = None

    async def plan_route(self, start: str, end: str, transport_mode: str = "train",
                         user_real_location: str = None) -> "RoutePlanningResult":
        from datetime import datetime
        date = datetime.now().strftime("%Y-%m-%d")

        # 清洗终点名称
        end = self._clean_end_name(end)
        start = self._clean_start_name(start)

        start_loc = await self.amap.geocode(start) if start else user_real_location
        end_loc = await self.amap.geocode(end)

        if transport_mode == "train":
            return await self._plan_train_route(start, end, start_loc, end_loc, date, user_real_location)
        elif transport_mode == "driving":
            return await self._plan_driving_route(start, end, start_loc, end_loc)
        elif transport_mode == "transit":
            return await self._plan_transit_route(start, end, start_loc, end_loc, "0")
        else:
            return await self._plan_train_route(start, end, start_loc, end_loc, date, user_real_location)

    def _clean_end_name(self, end: str) -> str:
        """清洗终点名称 - 强净化，剔除无用后缀、剔除站字"""
        if not end:
            return end
        end = end.strip()
        suffixes = [
            "的旅游规划", "旅游规划", "的旅行规划", "旅行规划",
            "的攻略", "攻略", "的游玩攻略", "游玩攻略",
            "的距离", "距离", "的路线", "路线",
            "怎么走", "怎么去", "怎么坐车", "怎么到达",
            "的高铁", "的火车", "的动车", "的高铁票", "的火车票",
            "附近", "周边", "周围", "好玩吗", "怎么样", "推荐"
        ]
        for suffix in suffixes:
            if end.endswith(suffix):
                end = end[:-len(suffix)].strip()
        # 关键：去掉末尾「站」字，适配12306编码库
        if end.endswith("站"):
            end = end[:-1].strip()
        return end

    def _clean_start_name(self, start: str) -> str:
        """清洗起点名称 - 强净化，剔除前缀后缀、剔除站字"""
        if not start:
            return start
        start = start.strip()
        prefixes = ["从", "在", "去", "往", "到"]
        for prefix in prefixes:
            if start.startswith(prefix):
                start = start[len(prefix):].strip()
        suffixes = ["出发", "起点", "起始地", "出发地"]
        for suffix in suffixes:
            if start.endswith(suffix):
                start = start[:-len(suffix)].strip()
        # 关键：去掉末尾「站」字
        if start.endswith("站"):
            start = start[:-1].strip()
        return start

    def _clean_station_name(self, name: str) -> str:
        """超强净化车站名：只保留城市+站，砍掉所有多余文字"""
        import re
        # 只保留 中文 + "站"，过滤掉“改签窗口、售票厅、入口、出口”等垃圾后缀
        name = re.sub(r'[a-zA-Z0-9]', '', name)
        name = re.sub(r'改签窗口.*|售票厅.*|入口.*|出口.*|广场.*|通道.*', '', name)
        name = name.strip()
        # 只取前5个字，防止过长
        if len(name) > 5:
            name = name[:5]
        # 确保以“站”结尾
        if not name.endswith("站") and len(name) >= 2:
            name = name + "站"
        return name

    async def _plan_train_route(self, start: str, end: str, start_loc: str, end_loc: str,
                                date: str, user_real_location: str = None) -> "RoutePlanningResult":
        """
        规划火车路线 - 三段式完整版

        核心逻辑：
        - 有起点：市中心(start_loc) → 起点站 → 终点站 → 市中心(end_loc)
        - 无起点：定位(real_start_loc) → 附近站 → 终点站 → 市中心(end_loc)
        """
        result = RoutePlanningResult()
        result.transport_mode = "Train Travel"

        try:
            # ==================== 1. 确定起点类型和坐标 ====================
            has_start = bool(start and start.strip() and start != "当前定位")

            if has_start:
                # 有起点：使用城市中心坐标作为 segment_1 起点
                segment1_from = start  # 显示用：城市名
                real_start_loc = start_loc  # 导航用：城市中心坐标
                logger.info(f"📍 有起点城市: {start}, 城市中心坐标: {real_start_loc}")
            elif user_real_location and user_real_location.strip():
                # 无起点但有GPS：使用GPS坐标
                segment1_from = "当前定位"
                real_start_loc = user_real_location
                logger.info(f"📍 无起点，使用GPS坐标: {real_start_loc}")
            else:
                # 兜底：用 start_loc（可能是IP定位的城市中心）
                segment1_from = "当前定位"
                real_start_loc = start_loc
                logger.info(f"📍 无起点，使用默认坐标: {real_start_loc}")

            # ==================== 2. 车站映射表 ====================
            city_station_map = {
                "北京": "北京南", "上海": "上海虹桥",
                "天津": "天津", "重庆": "重庆北",
                "广州": "广州南", "深圳": "深圳北",
                "杭州": "杭州东", "南京": "南京南",
                "武汉": "武汉", "成都": "成都东",
                "西安": "西安北", "济南": "济南西",
                "长沙": "长沙南", "郑州": "郑州东",
                "沈阳": "沈阳北", "大连": "大连",
                "青岛": "青岛", "厦门": "厦门",
                "苏州": "苏州", "宁波": "宁波",
            }

            # ==================== 3. 确定起点站 ====================
            start_station_location = None

            if has_start and start in city_station_map:
                # 有起点且在映射表：直接用映射的车站
                train_station_from = city_station_map[start]
                start_station_location = await self.amap.geocode(train_station_from)
                logger.info(f"🏙️ 起点城市映射: {start} -> {train_station_from} ({start_station_location})")
            else:
                # 无起点 或 不在映射表：搜索附近车站
                start_stations = await self.amap.find_train_stations(real_start_loc, radius=80000)
                if start_stations:
                    # 找到附近车站
                    nearest_station = start_stations[0]
                    raw_name = nearest_station.get("name", "")
                    # 净化车站名
                    train_station_from = self._clean_station_name(raw_name)
                    start_station_location = nearest_station.get("location", real_start_loc)
                    logger.info(f"🚉 附近车站: {raw_name} -> 净化后: {train_station_from} ({start_station_location})")
                else:
                    # 没找到车站，用起点城市名兜底
                    train_station_from = start if has_start else "未知站点"
                    start_station_location = real_start_loc
                    logger.warning(f"⚠️ 未找到附近车站，使用: {train_station_from}")

            # ==================== 4. 确定终点站 ====================
            # 净化终点名
            end_clean = self._clean_end_name(end)

            if end_clean in city_station_map:
                # 终点在映射表
                train_station_to = city_station_map[end_clean]
                logger.info(f"🏙️ 终点城市映射: {end_clean} -> {train_station_to}")
            else:
                # 终点不在映射表，尝试净化后使用
                train_station_to = self._clean_station_name(end_clean)
                logger.info(f"🚉 终点车站: {train_station_to}")

            # ==================== 5. 计算三段距离/时长 ====================

            # 第1段：起点 → 起点站
            seg1_dist = 0.0
            seg1_dur = 0.0
            if real_start_loc and start_station_location:
                route1 = await self.amap.driving_route(real_start_loc, start_station_location)
                if route1 and route1.get("status") == "1":
                    path = route1["route"]["paths"][0]
                    seg1_dist = float(path.get("distance", 0)) / 1000
                    seg1_dur = float(path.get("duration", 0)) / 60
                    logger.info(f"🚗 第1段: {seg1_dist:.1f}km, {seg1_dur:.0f}min")

            # 第2段：城市间直线距离（用于显示）
            city_distance = self._calculate_straight_distance(start_loc, end_loc)

            # 第3段：终点站 → 终点市中心（估算）
            # 尝试获取终点车站到终点市中心的距离
            seg3_dist = 15.0
            seg3_dur = 30.0
            end_station_loc = await self.amap.geocode(train_station_to)
            if end_station_loc and end_loc:
                route3 = await self.amap.driving_route(end_station_loc, end_loc)
                if route3 and route3.get("status") == "1":
                    path = route3["route"]["paths"][0]
                    seg3_dist = float(path.get("distance", 0)) / 1000
                    seg3_dur = float(path.get("duration", 0)) / 60
                    logger.info(f"🚗 第3段: {seg3_dist:.1f}km, {seg3_dur:.0f}min")

            # ==================== 6. 查询火车信息 ====================
            train_info = await self._get_train_schedule(date, train_station_from, train_station_to)
            if train_info and train_info.get("success") and train_info["trains"]:
                best_train = train_info["trains"][0]
                result.train_info = best_train
                try:
                    train_cost = float(best_train['second_class']) if str(best_train['second_class']).replace('.','').isdigit() else 0
                except:
                    train_cost = estimate_train_cost(start_station_location, end_loc)
                logger.info(f"🚄 查询到 {len(train_info['trains'])} 趟列车")
            else:
                result.train_info = self._create_default_train_info(train_station_from, train_station_to)
                train_cost = estimate_train_cost(start_station_location, end_loc)
                logger.warning("⚠️ 未查询到火车信息，使用默认值")

            # ==================== 7. 计算总费用/距离/时长 ====================
            train_duration_min = self._parse_train_duration(result.train_info.get("duration", "0")) if result.train_info else 0

            result.total_cost = train_cost + max(20, seg1_dist * 0.8) + max(20, seg3_dist * 0.8)
            result.total_distance = city_distance
            result.total_duration = int(seg1_dur + train_duration_min + seg3_dur)

            # ==================== 8. 组装三段式结果 ====================

            # 第1段：起点 → 起点站
            result.segment_1 = {
                "step": 1,
                "icon": "🚗",
                "mode": "市内交通",
                "from": segment1_from,
                "to": train_station_from,
                "distance": seg1_dist,
                "duration": int(seg1_dur),
                "detail": f"从 {segment1_from} 驾车前往 {train_station_from}，约 {seg1_dist:.1f}km，{int(seg1_dur)}分钟"
            }

            # 第2段：起点站 → 终点站
            result.segment_2 = {
                "step": 2,
                "icon": "🚄",
                "mode": "高铁",
                "from": train_station_from,
                "to": train_station_to,
                "distance": city_distance,
                "duration": train_duration_min,
                "detail": self._format_train_detail(result.train_info)
            }

            # 第3段：终点站 → 终点市中心
            result.segment_3 = {
                "step": 3,
                "icon": "🚗",
                "mode": "市内交通",
                "from": train_station_to,
                "to": end_clean,
                "distance": seg3_dist,
                "duration": int(seg3_dur),
                "detail": f"从 {train_station_to} 驾车前往 {end_clean}市中心，约 {seg3_dist:.1f}km，{int(seg3_dur)}分钟"
            }

            # route_details 文本
            result.route_details = [
                f"📍 第1段：市内交通",
                f"  从 {segment1_from} 到 {train_station_from}",
                f"  距离: {seg1_dist:.1f}km",
                f"  用时: {int(seg1_dur)}分钟",
                "",
                f"🚄 第2段：火车",
                f"  从 {train_station_from} → {train_station_to}",
                f"  车次: {result.train_info.get('train_number', '查询中')}",
                f"  时间: {result.train_info.get('departure_time', '--:--')} → {result.train_info.get('arrival_time', '--:--')}",
                f"  历时: {result.train_info.get('duration', '--')}",
                f"  票价: {result.train_info.get('second_class', '暂无')}",
                "",
                f"📍 第3段：市内交通",
                f"  从 {train_station_to} 到 {end_clean}市中心",
                f"  距离: {seg3_dist:.1f}km",
                f"  用时: {int(seg3_dur)}分钟",
                "",
                f"📊 总览",
                f"  总距离: {city_distance:.1f}km",
                f"  总耗时: {format_duration(result.total_duration)}",
                f"  总费用: {format_cost(result.total_cost)}",
            ]

            logger.info("✅ 火车路线规划完成")
            return result

        except Exception as e:
            result.error = f"规划失败：{str(e)}"
            logger.error(f"_plan_train_route 异常: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return result

    def _parse_train_duration(self, duration_str: str) -> int:
        """解析火车时长字符串为分钟数"""
        try:
            if not duration_str or duration_str == "--":
                return 0
            # 处理格式如 "05:53" 或 "5小时53分"
            if ":" in duration_str:
                parts = duration_str.split(":")
                hours = int(parts[0])
                minutes = int(parts[1])
                return hours * 60 + minutes
            return 0
        except:
            return 0

    def _format_train_detail(self, train_info: dict) -> str:
        """格式化火车详情"""
        if not train_info:
            return "暂无火车信息"
        return (
            f"车次: {train_info.get('train_number', '待查询')}\n"
            f"时间: {train_info.get('departure_time', '--:--')} → {train_info.get('arrival_time', '--:--')}\n"
            f"历时: {train_info.get('duration', '--')}\n"
            f"二等座: {train_info.get('second_class', '--')}"
        )

    def _calculate_straight_distance(self, start_loc: str, end_loc: str) -> float:
        try:
            start_lng, start_lat = map(float, start_loc.split(','))
            end_lng, end_lat = map(float, end_loc.split(','))
            import math
            start_lat_rad = math.radians(start_lat)
            start_lng_rad = math.radians(start_lng)
            end_lat_rad = math.radians(end_lat)
            end_lng_rad = math.radians(end_lng)
            delta_lat = end_lat_rad - start_lat_rad
            delta_lng = end_lng_rad - start_lng_rad
            a = (math.sin(delta_lat / 2) ** 2 +
                 math.cos(start_lat_rad) * math.cos(end_lat_rad) * math.sin(delta_lng / 2) ** 2)
            c = 2 * math.asin(math.sqrt(a))
            earth_radius = 6371.0
            distance = earth_radius * c
            return distance
        except Exception as e:
            logger.error(f"直线距离计算失败: {e}")
            return 0.0

    def _should_use_train(self, start_loc: str, end_loc: str) -> bool:
        try:
            distance = self._calculate_straight_distance(start_loc, end_loc)
            return distance > 120.0
        except Exception as e:
            logger.error(f"火车推荐判断失败: {e}")
            return False

    async def _plan_transit_route(self, start: str, end: str, start_loc: str, end_loc: str, strategy: str) -> "RoutePlanningResult":
        result = RoutePlanningResult()
        result.transport_mode = "Public Transit"
        try:
            city = self._extract_city_from_address(start)
            transit_data = await self.amap.transit_route(start_loc, end_loc, city, strategy)
            if not transit_data or transit_data.get("status") != "1":
                result.error = f"Transit planning failed: {transit_data.get('info', 'Unknown error')}"
                return result
            result.raw_response = transit_data
            route = transit_data.get("route", {})
            transits = route.get("transits", [])
            if not transits:
                result.error = "No viable public transit route found"
                return result
            best_transit = transits[0]
            if isinstance(best_transit, list):
                best_transit = best_transit[0] if best_transit else {}
            cost_val = best_transit.get("cost", 0)
            if isinstance(cost_val, list):
                cost_val = cost_val[0] if cost_val else 0
            result.total_cost = float(cost_val)
            dur_val = best_transit.get("duration", 0)
            if isinstance(dur_val, list):
                dur_val = dur_val[0] if dur_val else 0
            result.total_duration = int(float(dur_val) / 60)
            dist_val = best_transit.get("distance", 0)
            if isinstance(dist_val, list):
                dist_val = dist_val[0] if dist_val else 0
            result.total_distance = float(dist_val) / 1000
            walk_val = best_transit.get("walking_distance", 0)
            if isinstance(walk_val, list):
                walk_val = walk_val[0] if walk_val else 0
            result.walking_distance = float(walk_val) if walk_val != [] else 0
            result.route_details = self._parse_transit_segments(best_transit.get("segments", []), start, end)
        except Exception as e:
            result.error = f"Public transit route planning failed: {str(e)}"
        return result

    async def _plan_driving_route(self, start: str, end: str, start_loc: str, end_loc: str) -> "RoutePlanningResult":
        result = RoutePlanningResult()
        result.transport_mode = "Driving"
        try:
            route_data = await self.amap.driving_route(start_loc, end_loc)
            if not route_data or route_data.get("status") != "1":
                result.error = f"Driving planning failed: {route_data.get('info', 'Unknown error')}"
                return result
            route = route_data.get("route", {})
            paths = route.get("paths", [])
            if not paths:
                result.error = "No viable driving route found"
                return result
            best_path = paths[0]
            result.total_distance = float(best_path.get("distance", 0)) / 1000
            result.total_duration = int(float(best_path.get("duration", 0)) / 60)
            result.total_cost = max(50, result.total_distance * 0.8)
            result.route_details = [
                f"Drive from {start} to {end}",
                f"  Distance: {format_distance(result.total_distance)}",
                f"  Duration: {format_duration(result.total_duration)}",
                f"  Cost: {format_cost(result.total_cost)} (fuel + tolls)"
            ]
        except Exception as e:
            result.error = f"Driving route planning failed: {str(e)}"
        return result

    async def _get_train_schedule(self, date: str, from_station: str, to_station: str) -> Optional[Dict]:
        """使用 Py12306Client 异步查询火车时刻表 - 手动管理 session"""
        if not self.enable_train_info or not self.train_client:
            logger.warning("⚠️ Py12306Client 未启用，跳过火车查询")
            return None
        try:
            # ✅ 手动初始化 session（避免 async with 重复创建/关闭）
            if not self.train_client.session:
                await self.train_client._init_session()

            # ✅ 直接调用查询方法
            result = await self.train_client.query_train_schedule(
                date=date,
                from_station=from_station,
                to_station=to_station,
                include_price=True
            )

            # ✅ 查询完成后关闭 session
            await self.train_client.close()

            if result.get("success") and result.get("trains"):
                logger.info(f"✅ 查询到 {len(result['trains'])} 趟列车: {from_station} → {to_station}")
                return result
            else:
                error_msg = result.get("error", "未知错误")
                logger.warning(f"⚠️ 火车查询未返回数据: {error_msg}")
                return None

        except Exception as e:
            logger.error(f"❌ Py12306Client 查询失败: {e}")
            # ✅ 确保异常时关闭 session
            try:
                await self.train_client.close()
            except:
                pass
            return None

    def _create_default_train_info(self, start_station: str, end_station: str) -> Dict:
        return {
            "train_number": "Check 12306",
            "departure_time": "08:00",
            "arrival_time": "14:00",
            "duration": "4-6 hours",
            "second_class": "300-600 CNY",
            "first_class": "400-800 CNY",
            "soft_sleeper": "500-900 CNY",
            "hard_sleeper": "400-700 CNY",
            "hard_seat": "200-400 CNY",
            "note": f"Check 12306 official website for {start_station}→{end_station}"
        }

    def _extract_city_from_address(self, address: str) -> str:
        cities = ["北京", "上海", "广州", "深圳", "杭州", "南京", "武汉", "成都"]
        for city in cities:
            if city in address:
                return city
        return address[:2] if len(address) >= 2 else address

    def _parse_transit_segments(self, segments: List[Dict], start: str, end: str) -> List[str]:
        details = [
            f"Public transit from {start} to {end}",
            "Detailed route:"
        ]
        for i, segment in enumerate(segments, 1):
            if not isinstance(segment, dict):
                details.append(f"  {i}. Unknown segment type")
                continue
            if "walking" in segment:
                walking = segment["walking"]
                if isinstance(walking, list):
                    walking = walking[0] if walking else {}
                distance = walking.get("distance", 0)
                duration = int(float(walking.get("duration", 0)) / 60) if walking.get("duration") else 0
                details.append(f"  {i}. Walk {distance}m ({duration}min)")
            elif "bus" in segment:
                bus = segment["bus"]
                buslines = bus.get("buslines", [])
                for busline in buslines:
                    name = busline.get("name", "Unknown")
                    duration = int(float(busline.get("duration", 0)) / 60)
                    details.append(f"  {i}. Take {name} ({duration}min)")
        return details

