"""
统一高德地图客户端
===================

合并来源：
  - amap_integration.py 的 AMapClient (sync requests)
  - tools/external/map_tools/route_tool.py 的 AmapRouteTool (async aiohttp)

统一为异步 aiohttp 实现，保留双方所有功能。
"""

import logging
import time
import asyncio
from typing import Dict, List, Optional

import aiohttp

from MCP.tool_amap.validators import InputValidator
from MCP.config.settings import settings

logger = logging.getLogger(__name__)


def _as_text(value) -> str:
    """把高德返回的字段归一为字符串。

    高德对直辖市（北京/上海/天津/重庆）的 addressComponent.city 会返回空列表 []，
    直接 f-string 拼接会得到「[]东城区」（DEF-02）。在 SDK 边界统一归一，
    避免每个调用方各写一份防御。
    """
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, (list, tuple)):
        return "".join(x for x in value if isinstance(x, str)).strip()
    return ""


class UnifiedAMapClient:
    """统一高德地图客户端（async aiohttp）【带缓存+限速+多Key轮询，不限流】

    整合了 AMapClient 和 AmapRouteTool 的所有功能：
    - 地理编码 / 逆地理编码
    - 驾车 / 公交路线规划
    - IP定位
    - GPS坐标转换
    - POI搜索 / 附近火车站搜索
    - 距离测量
    - 智能地址解析入口
    """

    BASE_URL = "https://restapi.amap.com/v3"
    GEOCODE_URL = "https://restapi.amap.com/v3/geocode/geo"
    REGEO_URL = "https://restapi.amap.com/v3/geocode/regeo"
    CAR_URL = "https://restapi.amap.com/v3/direction/driving"
    TRANSIT_URL = "https://restapi.amap.com/v3/direction/transit/integrated"
    GPS_URL = "https://restapi.amap.com/v3/ip"
    DISTANCE_URL = "https://restapi.amap.com/v3/distance"
    PLACE_TEXT_URL = "https://restapi.amap.com/v3/place/text"
    PLACE_AROUND_URL = "https://restapi.amap.com/v3/place/around"
    COORD_CONVERT_URL = "https://restapi.amap.com/v3/assistant/coordinate/convert"

    # ====================== 核心：多Key轮询 + 全局缓存 + 请求限速 ======================
    AMAP_KEYS = [
        settings.AMAP_API_KEY,
        # 可在这里追加多个高德key，例如：
        # "xxxxxx",
        # "yyyyyy"
    ]
    GEO_CACHE = {}        # 全局地址/坐标缓存
    LAST_API_CALL = 0     # 上一次请求时间戳
    MIN_INTERVAL = 0.4    # 最小请求间隔 0.4秒，压制QPS
    MAX_QPS_RETRIES = 3   # QPS 超限最大重试次数（禁止无限递归）
    key_index = 0         # key轮询下标

    def get_next_key(self):
        """轮询获取下一个高德Key"""
        if not self.AMAP_KEYS:
            return self.api_key
        key = self.AMAP_KEYS[self.key_index]
        self.key_index = (self.key_index + 1) % len(self.AMAP_KEYS)
        return key

    async def rate_limit(self):
        """异步强制请求间隔，防QPS超限"""
        now = time.time()
        wait_sec = self.MIN_INTERVAL - (now - self.LAST_API_CALL)
        if wait_sec > 0:
            await asyncio.sleep(wait_sec)
        self.LAST_API_CALL = time.time()
    # ===================================================================================

    def __init__(self, api_key: Optional[str] = None):
        """
        初始化高德地图客户端

        Args:
            api_key: 高德地图API密钥，默认从 settings 读取
        """
        self.api_key = api_key or settings.AMAP_API_KEY

        # API密钥验证
        if self.api_key and not InputValidator.validate_api_key(self.api_key):
            logger.warning("高德地图API密钥格式异常，部分功能可能不可用")

        self._session: Optional[aiohttp.ClientSession] = None
        logger.info("UnifiedAMapClient 初始化完成（已启用缓存+限速+多Key轮询）")

    async def __aenter__(self):
        """支持 async with UnifiedAMapClient() 自动释放会话（P1-12）"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def _get_session(self) -> aiohttp.ClientSession:
        """获取或创建 aiohttp 会话(异步向高德发送请求)"""
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=15)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close(self):
        """关闭会话（幂等，可被 lifecycle 重复调用）"""
        if self._session and not self._session.closed:
            await self._session.close()
        self._session = None

    async def _request(self, url: str, params: dict) -> Optional[dict]:
        """通用GET请求 + 限速 + 多Key轮询 + 有上限的 QPS 退避重试（P1-12）"""
        if not self.api_key:
            logger.warning("高德地图API密钥未配置")
            return None

        for attempt in range(self.MAX_QPS_RETRIES):
            # 每次尝试都重新限速并轮询 key，避免递归调用导致堆栈无限增长
            await self.rate_limit()
            params["key"] = self.get_next_key()
            params["output"] = "json"

            try:
                session = await self._get_session()
                async with session.get(url, params=params) as resp:
                    data = await resp.json()

                if data and data.get("info") == "CUQPS_HAS_EXCEEDED_THE_LIMIT":
                    wait_sec = min(2 ** attempt, 8)
                    logger.warning(
                        f"[高德] QPS 超限，第 {attempt + 1}/{self.MAX_QPS_RETRIES} 次重试，"
                        f"等待 {wait_sec}s -> {url}"
                    )
                    await asyncio.sleep(wait_sec)
                    continue

                return data
            except Exception as e:
                logger.error(f"高德API请求异常: {e}")
                return None

        logger.error(f"[高德] QPS 超限重试 {self.MAX_QPS_RETRIES} 次后仍失败 -> {url}")
        return None

    # ==================== 地理编码（带内存缓存） ====================
    async def geocode(self, address: str, city: str = None) -> Optional[str]:
        """地址 → 经纬度坐标（带城市，必解析成功）"""
        cache_key = f"geo_{address}_{city}"
        if cache_key in self.GEO_CACHE:
            return self.GEO_CACHE[cache_key]

        try:
            if city:
                address = f"{city}{address}"

            params = {"address": address}
            data = await self._request(self.GEOCODE_URL, params)

            if data and data.get("status") == "1" and data.get("geocodes"):
                location = data["geocodes"][0]["location"]
                self.GEO_CACHE[cache_key] = location
                return location

            logger.warning(f"地理编码失败: {address}")
            return None
        except Exception as e:
            logger.error(f"geocode 异常: {e}")
            return None

    async def regeocode(self, location: str) -> Optional[Dict]:
        """
        逆地理编码：坐标转地址（带缓存）
        """
        cache_key = f"regeo_{location}"
        if cache_key in self.GEO_CACHE:
            return self.GEO_CACHE[cache_key]

        # 🔥 修复：只校验坐标格式，不拦截正常地址
        try:
            # 简单判断是否是经纬度格式：xx.xx,yy.yy
            if "," not in location:
                logger.error(f"regeocode 传入的不是坐标: {location}")
                return None

            # 不再用外部校验器，避免误杀
            lng, lat = location.split(",")
            float(lng.strip())
            float(lat.strip())
        except:
            logger.error(f"坐标格式无效: {location}")
            return None

        params = {"location": location}
        data = await self._request(self.REGEO_URL, params)
        if not data:
            return None

        if data.get("status") == "1":
            regeocode = data.get("regeocode", {})
            address_component = regeocode.get("addressComponent", {})
            # 高德对直辖市返回 city=[]，统一在 SDK 边界归一（DEF-02）
            province = _as_text(address_component.get("province"))
            city = _as_text(address_component.get("city")) or province
            res = {
                "status": "success",
                "location": location,
                "formatted_address": regeocode.get("formatted_address", ""),
                "country": _as_text(address_component.get("country")),
                "province": province,
                "city": city,
                "district": _as_text(address_component.get("district")),
                "township": _as_text(address_component.get("township")),
                "street": _as_text(address_component.get("street")),
                "street_number": _as_text(address_component.get("streetNumber")),
                "business_areas": regeocode.get("businessAreas", []),
            }
            self.GEO_CACHE[cache_key] = res
            return res

        logger.warning(f"逆地理编码失败: {data.get('info', '')}")
        return None
    # ==================== 路线规划 ====================
    async def driving_route(self, origin: str, destination: str) -> Optional[Dict]:
        params = {"origin": origin, "destination": destination}
        return await self._request(self.CAR_URL, params)

    async def transit_route(self, origin: str, destination: str, city: str, strategy: str = "0") -> Optional[Dict]:
        params = {
            "origin": origin,
            "destination": destination,
            "city": city,
            "strategy": strategy,
        }
        return await self._request(self.TRANSIT_URL, params)

    # ==================== IP定位 ====================
    async def locate_by_ip(self) -> Dict:
        data = await self._request(self.GPS_URL, {})
        if not data:
            return {"status": "error", "error": "IP定位请求失败"}

        if data.get("status") == "1":
            return {
                "status": "success",
                "country": data.get("country", ""),
                "province": data.get("province", ""),
                "city": data.get("city", ""),
                "district": data.get("district", ""),
                "ip": data.get("ip", ""),
                "location": data.get("location", ""),
            }

        return {"status": "error", "error": data.get("info", "IP定位失败")}

    # ==================== 坐标转换 ====================
    async def convert_coords(self, location: str, from_coordsys: str = "gps") -> Dict:
        params = {"locations": location, "coordsys": from_coordsys}
        data = await self._request(self.COORD_CONVERT_URL, params)
        if not data:
            return {"status": "error", "error": "坐标转换请求失败"}

        if data.get("status") == "1":
            return {
                "status": "success",
                "original_location": location,
                "converted_location": data.get("locations", ""),
                "info": data.get("info", ""),
            }

        return {"status": "error", "error": data.get("info", "坐标转换失败")}

    # ==================== 距离测量 ====================
    async def measure_distance(self, origins: List[str], destination: str) -> Optional[List[Dict]]:
        params = {"origins": "|".join(origins), "destination": destination}
        data = await self._request(self.DISTANCE_URL, params)
        if not data:
            return None

        if data.get("status") == "1":
            return data.get("results", [])
        return None

    # ==================== POI搜索 ====================
    async def search_poi(self, keywords: str, city: Optional[str] = None, type_: Optional[str] = None) -> List[Dict]:
        params = {"keywords": keywords, "extensions": "all"}
        if city:
            params["city"] = city
        if type_:
            params["type"] = type_

        data = await self._request(self.PLACE_TEXT_URL, params)
        if data and data.get("status") == "1":
            return data.get("pois", [])
        return []

    async def search_nearby(self, location: str, radius: int = 1000, keywords: Optional[str] = None) -> List[Dict]:
        params = {"location": location, "radius": radius, "extensions": "all"}
        if keywords:
            params["keywords"] = keywords

        data = await self._request(self.PLACE_AROUND_URL, params)
        if data and data.get("status") == "1":
            return data.get("pois", [])
        return []

    async def find_train_stations(self, location: str, radius: int = 15000) -> List[Dict]:
        poi_type = "150200"
        keyword_list = ["火车站", "高铁站", "城际站"]
        all_stations = []

        city_name = ""
        try:
            addr_info = await self.regeocode(location)
            if isinstance(addr_info, dict):
                city_name = addr_info.get("city", "").strip()
        except Exception:
            city_name = ""

        for kw in keyword_list:
            params = {
                "location": location,
                "radius": radius,
                "types": poi_type,
                "keywords": kw,
                "extensions": "all"
            }
            if city_name:
                params["city"] = city_name

            data = await self._request(self.PLACE_AROUND_URL, params)
            if data and data.get("status") == "1":
                all_stations.extend(data.get("pois", []))

        unique = []
        name_set = set()
        for item in all_stations:
            name = item.get("name", "")
            if "站" in name and name not in name_set:
                name_set.add(name)
                unique.append(item)

        unique.sort(key=lambda x: float(x.get("distance", 999999)))
        return unique

    # ==================== 便捷入口（原 AmapRouteTool） ====================
    async def get_gps_coordinates(self, address: str, city: str = "") -> Dict:
        """
        获取指定地址的GPS坐标
        """
        location = await self.geocode(address, city)
        if location:
            lng, lat = location.split(",")
            return {
                "status": "success",
                "address": address,
                "city": city,
                "latitude": float(lat),
                "longitude": float(lng),
                "location": location,
            }

        return {"status": "error", "error": "无法获取GPS坐标", "address": address}

    async def smart_route(self, query: str) -> str:
        """
        智能路线规划
        支持：北京到上海 / 到上海（自动用当前IP位置做起点）
        """
        query = query.strip()

        if "到" not in query:
            return "请输入类似【北京 到 上海】或【到 上海】的格式"

        parts = query.replace(" ", "").split("到")
        if len(parts) != 2:
            return "无法识别起点和终点，请重新输入"

        start_input = parts[0].strip()
        end = parts[1].strip()

        if not start_input or start_input in ["当前位置", "这里", "出发地", "起点"]:
            logger.info("[高德工具] 未指定起点，自动IP定位获取当前城市")
            current = await self.locate_by_ip()
            if current.get("status") == "success":
                start = current.get("city", "当前位置")
                logger.info(f"[高德工具] 自动获取起点城市: {start}")
            else:
                return f"无法获取您的当前位置: {current.get('error')}。请直接指定起点，如【北京 到 上海】"
        else:
            start = start_input

        logger.info(f"[高德工具] 开始规划路线: {start} → {end}")
        start_loc = await self.geocode(start)
        end_loc = await self.geocode(end)

        if not start_loc or not end_loc:
            return f"地址解析失败：{start} → {end}"

        route_data = await self.driving_route(start_loc, end_loc)
        if not route_data:
            return "路线查询失败，请稍后重试"

        paths = route_data.get("route", {}).get("paths", [])
        if not paths:
            return "未查询到可用路线"

        path = paths[0]
        distance_km = int(path["distance"]) / 1000
        duration_min = int(path["duration"]) / 60

        return (
            f"驾车路线：{start} → {end}\n"
            f"全程：{distance_km:.1f} 公里\n"
            f"预计：{duration_min:.0f} 分钟"
        )