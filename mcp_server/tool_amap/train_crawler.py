"""
py12306 客户端封装
===================

使用 py12306 库进行火车信息查询的高级封装
提供异步查询、缓存、重试等功能
"""

import asyncio
import json
import logging
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp
# 内置输入验证（避免外部依赖）
class InputValidator:
    """简单的输入验证器"""
    @staticmethod
    def validate_date(date_str: str) -> bool:
        import re
        if not date_str:
            return False
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        return bool(re.match(pattern, date_str))

    @staticmethod
    def validate_address(address: str) -> bool:
        if not address or not isinstance(address, str):
            return False
        return len(address.strip()) >= 2

# 配置日志
logger = logging.getLogger(__name__)

# 12306 相关常量
STATION_NAMES_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
CACHE_EXPIRE_DAYS = 7
BASE_URL = "https://kyfw.12306.cn"


class Py12306Client:
    """
    py12306 客户端封装

    提供异步火车信息查询功能，支持：
    - 车次查询
    - 余票查询
    - 价格查询
    - 车站信息查询
    """

    def __init__(self, cache_enabled: bool = True, timeout: int = 15):
        """
        初始化 py12306 客户端

        Args:
            cache_enabled: 是否启用缓存
            timeout: 请求超时时间（秒）
        """
        self.cache_enabled = cache_enabled
        self.timeout = timeout
        self.session = None
        self.station_codes = {}
        self._data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
        os.makedirs(self._data_dir, exist_ok=True)

        # 加载车站编码
        self._load_station_codes()

    async def __aenter__(self):
        """异步上下文管理器入口"""
        await self._init_session()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """异步上下文管理器出口"""
        await self.close()

    async def _init_session(self):
        """初始化异步会话 - 增强反爬伪装"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)

            # ✅ 增强请求头伪装，模拟真实浏览器
            import random
            user_agents = [
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
                'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36 Edg/123.0.0.0',
                'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36',
            ]

            self.session.headers.update({
                'User-Agent': random.choice(user_agents),
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://kyfw.12306.cn',
                'Referer': 'https://kyfw.12306.cn/otn/leftTicket/init',
                'Sec-Fetch-Dest': 'empty',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Site': 'same-origin',
                'X-Requested-With': 'XMLHttpRequest',
            })

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.session = None

    def _load_station_codes(self):
        """加载车站编码信息"""
        cache_file = os.path.join(self._data_dir, "station_codes.json")

        # 检查缓存是否有效
        if self.cache_enabled and os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if time.time() - mtime < CACHE_EXPIRE_DAYS * 24 * 3600:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        self.station_codes = json.load(f)
                    logger.info(f"✅ 加载缓存车站编码: {len(self.station_codes)} 个车站")
                    return
            except Exception as e:
                logger.warning(f"加载车站编码缓存失败: {e}")

        # 需要重新获取车站编码
        self.station_codes = {}

    async def _fetch_station_codes(self):
        """获取车站编码信息 - 增强版（支持多种格式）"""
        try:
            if not self.session:
                await self._init_session()

            async with self.session.get(STATION_NAMES_URL) as response:
                if response.status == 200:
                    text = await response.text()
                    import re

                    # ✅ 尝试多种正则格式
                    # 格式1: @bjb|北京北|VAP|beijingbei|bjb|0|0
                    stations = re.findall(r'@[a-z]+\\|([^|]+)\\|([A-Z]+)\\|[^|]+\\|[^|]+\\|', text)
                    if not stations:
                        # 格式2: @bjb|北京北|VAP|beijingbei|bjb|
                        stations = re.findall(r'@[a-z]+\\|([^|]+)\\|([A-Z]+)\\|[^|]+\\|[^|]+', text)
                    if not stations:
                        # 格式3: |北京北|VAP|
                        stations = re.findall(r'\\|([^|]+)\\|([A-Z]+)\\|', text)

                    if stations:
                        self.station_codes = {name: code for name, code in stations}

                        # 保存缓存
                        if self.cache_enabled:
                            cache_file = os.path.join(self._data_dir, "station_codes.json")
                            with open(cache_file, 'w', encoding='utf-8') as f:
                                json.dump(self.station_codes, f, ensure_ascii=False, indent=2)

                        logger.info(f"✅ 获取车站编码成功: {len(self.station_codes)} 个车站")
                        # 打印前5个作为示例
                        sample = list(self.station_codes.items())[:5]
                        logger.info(f"   示例: {sample}")
                    else:
                        logger.error(f"❌ 无法解析车站编码，响应内容前200字符: {text[:200]}")
                else:
                    logger.error(f"❌ 获取车站编码失败，HTTP状态码: {response.status}")

        except Exception as e:
            logger.error(f"❌ 获取车站编码异常: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _get_station_code(self, station_name: str) -> Optional[str]:
        """获取车站代码 - 增强版（支持城市名→车站映射+模糊匹配）"""
        if not self.station_codes:
            return None

        # ✅ 大城市映射表（城市名 → 默认车站）
        city_station_map = {
            "北京": "北京南", "上海": "上海虹桥", "天津": "天津",
            "重庆": "重庆北", "广州": "广州南", "深圳": "深圳北",
            "杭州": "杭州东", "南京": "南京南", "武汉": "武汉",
            "成都": "成都东", "西安": "西安北", "济南": "济南西",
            "长沙": "长沙南", "郑州": "郑州东", "沈阳": "沈阳北",
            "大连": "大连北", "青岛": "青岛", "厦门": "厦门",
            "苏州": "苏州", "宁波": "宁波", "滨海新区": "天津",
            "滨海": "天津",
        }

        # 如果传入的是城市名，映射到默认车站
        if station_name in city_station_map:
            station_name = city_station_map[station_name]

        # 精确匹配
        code = self.station_codes.get(station_name)
        if code:
            return code

        # 去掉"站"字再试
        if station_name.endswith("站") and len(station_name) > 1:
            code = self.station_codes.get(station_name[:-1])
            if code:
                return code

        # 模糊匹配（城市名前缀）
        candidates = []
        for name, code in self.station_codes.items():
            if name.startswith(station_name) and len(station_name) >= 2:
                candidates.append((len(name), name, code))

        if candidates:
            candidates.sort()
            return candidates[0][2]

        # ✅ 最后尝试：如果名字中包含"滨海"等关键词，映射到附近大城市
        if "滨海" in station_name:
            return self.station_codes.get("天津")

        return None

    async def query_train_schedule(self, date: str, from_station: str, to_station: str,
                                  include_price: bool = True, max_retries: int = 3) -> Dict:
        """
        查询火车时刻表 - 增强版（含重试机制和频率控制）

        Args:
            date: 出发日期 (YYYY-MM-DD)
            from_station: 出发站
            to_station: 到达站
            include_price: 是否包含价格信息
            max_retries: 最大重试次数

        Returns:
            火车时刻表信息
        """
        # 输入验证
        if not InputValidator.validate_date(date):
            return {"error": f"日期格式错误: {date}"}

        if not InputValidator.validate_address(from_station):
            return {"error": f"出发站格式错误: {from_station}"}

        if not InputValidator.validate_address(to_station):
            return {"error": f"到达站格式错误: {to_station}"}

        # 确保会话已初始化
        if not self.session:
            await self._init_session()

        # 确保车站编码已加载
        if not self.station_codes:
            await self._fetch_station_codes()

        # 获取车站代码
        from_code = self._get_station_code(from_station)
        to_code = self._get_station_code(to_station)

        logger.info(f"🔍 车站编码查询: {from_station} -> {from_code}, {to_station} -> {to_code}")
        logger.info(f"📊 当前车站编码库大小: {len(self.station_codes)} 个")

        if not from_code or not to_code:
            # ✅ 如果编码库为空，尝试重新获取
            if not self.station_codes:
                logger.warning("⚠️ 车站编码库为空，尝试重新获取...")
                await self._fetch_station_codes()
                from_code = self._get_station_code(from_station)
                to_code = self._get_station_code(to_station)

            if not from_code or not to_code:
                # ✅ 关闭 session
                await self.close()
                available = list(self.station_codes.keys())[:10]
                logger.error(f"❌ 找不到车站编码: {from_station}({from_code}) -> {to_station}({to_code})")
                logger.error(f"   编码库示例: {available}")
                return {"error": f"找不到车站编码: {from_station} -> {to_station}"}

        # ✅ 重试机制
        for attempt in range(max_retries):
            try:
                # ✅ 频率控制：每次请求间隔 1-3 秒
                if attempt > 0:
                    import random
                    wait_time = random.uniform(1.0, 3.0)
                    logger.info(f"⏱️ 第 {attempt + 1} 次重试，等待 {wait_time:.1f} 秒...")
                    await asyncio.sleep(wait_time)

                # 构造查询参数
                params = {
                    "leftTicketDTO.train_date": date,
                    "leftTicketDTO.from_station": from_code,
                    "leftTicketDTO.to_station": to_code,
                    "purpose_codes": "ADULT"
                }

                # 初始化页面获取Cookie
                init_url = f"{BASE_URL}/otn/leftTicket/init"
                async with self.session.get(init_url) as response:
                    if response.status != 200:
                        if attempt < max_retries - 1:
                            continue
                        return {"error": "无法连接12306网站"}

                # ✅ 2026年接口适配：尝试 queryG，失败则回退到 query
                query_urls = [
                    f"{BASE_URL}/otn/leftTicket/queryG",
                    f"{BASE_URL}/otn/leftTicket/query",
                    f"{BASE_URL}/otn/leftTicket/queryA",
                ]

                data = None
                for query_url in query_urls:
                    try:
                        async with self.session.get(query_url, params=params) as response:
                            if response.status == 200:
                                data = await response.json()
                                logger.info(f"✅ 接口成功: {query_url}")
                                break
                            else:
                                logger.warning(f"⚠️ 接口返回 {response.status}: {query_url}")
                    except Exception as e:
                        logger.warning(f"⚠️ 接口异常: {query_url}, 错误: {e}")
                        continue

                if data is None:
                    if attempt < max_retries - 1:
                        continue
                    # ✅ 关闭 session
                    await self.close()
                    return {"error": "所有查询接口均不可用"}

                if 'data' not in data:
                    error_msg = data.get('messages', ['未知错误'])[0] if data.get('messages') else '数据获取失败'
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ 12306返回错误: {error_msg}，准备重试...")
                        continue
                    # ✅ 关闭 session
                    await self.close()
                    return {"error": f"12306接口错误: {error_msg}"}

                # 解析车次信息
                results = data['data'].get('result', [])
                train_list = []

                for item in results:
                    train_info = self._parse_train_info(item)
                    if train_info:
                        train_list.append(train_info)

                if not train_list:
                    if attempt < max_retries - 1:
                        logger.warning(f"⚠️ 未找到车次，准备重试...")
                        continue
                    return {"error": "未找到符合条件的火车班次"}

                # 按发车时间排序
                train_list.sort(key=lambda x: x['departure_time'])

                return {
                    "success": True,
                    "date": date,
                    "from_station": from_station,
                    "to_station": to_station,
                    "total_trains": len(train_list),
                    "trains": train_list
                }

            except Exception as e:
                logger.error(f"❌ 查询异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    continue
                return {"error": f"查询失败: {str(e)}"}

        return {"error": "查询失败：超出最大重试次数"}

    def _parse_train_info(self, train_data: str) -> Optional[Dict]:
        """解析车次信息"""
        try:
            info = train_data.split('|')
            if len(info) < 35:
                return None

            # 基础信息
            train_number = info[3]
            departure_time = info[8]
            arrival_time = info[9]
            duration = info[10]

            # 座位信息
            seats = {
                "business_seat": info[32] if info[32] else '无',
                "first_class": info[31] if info[31] else '无',
                "second_class": info[30] if info[30] else '无',
                "soft_sleeper": info[23] if info[23] else '无',
                "hard_sleeper": info[28] if info[28] else '无',
                "hard_seat": info[29] if info[29] else '无',
                "no_seat": info[26] if info[26] else '无'
            }

            return {
                "train_number": train_number,
                "departure_time": departure_time,
                "arrival_time": arrival_time,
                "duration": duration,
                "seats": seats,
                "seat_types": list(seats.keys())
            }

        except Exception as e:
            logger.warning(f"解析车次失败: {e}")
            return None

    async def get_station_list(self) -> Dict[str, str]:
        """获取所有车站列表"""
        if not self.station_codes:
            await self._fetch_station_codes()
        return self.station_codes.copy()


# 使用示例
async def main():
    """测试函数"""
    async with Py12306Client() as client:
        result = await client.query_train_schedule(
            date="2026-05-15",
            from_station="北京",
            to_station="上海"
        )

        if result.get("success"):
            print(f"✅ 查询成功！共找到 {result['total_trains']} 趟列车")
            for train in result['trains'][:3]:
                print(f"\n🚄 {train['train_number']}")
                print(f"  时间: {train['departure_time']} → {train['arrival_time']}")
                print(f"  历时: {train['duration']}")
                print(f"  二等座: {train['seats']['second_class']}")
        else:
            print(f"❌ 查询失败: {result.get('error')}")




    # ==================== 同步包装方法 ====================
    def query_train_schedule_sync(self, date: str, from_station: str, to_station: str,
                                     include_price: bool = True) -> Dict:
        """
        同步查询火车时刻表（包装异步方法，方便非异步环境调用）

        Args:
            date: 出发日期 (YYYY-MM-DD)
            from_station: 出发站
            to_station: 到达站
            include_price: 是否包含价格信息

        Returns:
            火车时刻表信息
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                # 如果在运行中的事件循环中，创建新任务
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(
                    self.query_train_schedule(date, from_station, to_station, include_price)
                )
            else:
                return loop.run_until_complete(
                    self.query_train_schedule(date, from_station, to_station, include_price)
                )
        except RuntimeError:
            # 没有事件循环，创建新的
            return asyncio.run(
                self.query_train_schedule(date, from_station, to_station, include_price)
            )
        except Exception as e:
            logger.error(f"同步查询失败: {e}")
            return {"error": f"同步查询失败: {str(e)}"}

    def get_station_list_sync(self) -> Dict[str, str]:
        """同步获取车站列表"""
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                import nest_asyncio
                nest_asyncio.apply()
                return loop.run_until_complete(self.get_station_list())
            else:
                return loop.run_until_complete(self.get_station_list())
        except RuntimeError:
            return asyncio.run(self.get_station_list())
        except Exception as e:
            logger.error(f"同步获取车站列表失败: {e}")
            return {}


if __name__ == "__main__":
    asyncio.run(main())