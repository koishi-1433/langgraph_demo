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
from .validators import InputValidator

# 配置日志
logger = logging.getLogger(__name__)

# 12306 相关常量
STATION_NAMES_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
CACHE_EXPIRE_DAYS = 7
BASE_URL = "https://kyfw.12306.cn"


class Py12306Client:
    """
    py12306 客户端封装（与 train_crawler.Py12306Client 为重复实现，保持行为一致）

    提供异步火车信息查询功能，支持：
    - 车次查询
    - 余票查询
    - 价格查询
    - 车站信息查询
    """

    MIN_VALID_STATION_CODES = 100

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
        """初始化异步会话"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=self.timeout)
            self.session = aiohttp.ClientSession(timeout=timeout)

            # 设置请求头
            self.session.headers.update({
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'application/json, text/javascript, */*; q=0.01',
                'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
                'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8',
                'Origin': 'https://kyfw.12306.cn',
                'Referer': 'https://kyfw.12306.cn/otn/leftTicket/init',
            })

    async def close(self):
        """关闭会话"""
        if self.session:
            await self.session.close()
            self.session = None

    def _load_station_codes(self):
        """加载车站编码：内置完整映射为基线，空/损坏缓存不得覆盖（P1-11）"""
        bundled = os.path.join(self._data_dir, "station_names.json")
        if os.path.exists(bundled):
            try:
                with open(bundled, 'r', encoding='utf-8') as f:
                    codes = json.load(f)
                if len(codes) >= self.MIN_VALID_STATION_CODES:
                    self.station_codes = codes
                    logger.info(f"✅ 加载内置车站编码: {len(codes)} 个车站")
            except Exception as e:
                logger.warning(f"加载内置车站编码失败: {e}")

        cache_file = os.path.join(self._data_dir, "station_codes.json")
        if self.cache_enabled and os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if time.time() - mtime < CACHE_EXPIRE_DAYS * 24 * 3600:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        cached = json.load(f)
                    if len(cached) >= self.MIN_VALID_STATION_CODES:
                        self.station_codes = cached
                        logger.info(f"✅ 加载缓存车站编码: {len(cached)} 个车站")
                    else:
                        logger.warning(f"⚠️ 车站编码缓存仅 {len(cached)} 条，已忽略并保留内置映射")
            except Exception as e:
                logger.warning(f"加载车站编码缓存失败: {e}")

        if not self.station_codes:
            logger.warning("⚠️ 车站编码库为空：实时车次查询将不可用（结果会明确标注，不再编造）")

    async def _fetch_station_codes(self):
        """获取车站编码信息"""
        try:
            if not self.session:
                await self._init_session()

            async with self.session.get(STATION_NAMES_URL) as response:
                if response.status == 200:
                    text = await response.text()
                    # 解析车站信息: @bjb|北京北|VAP|beijingbei|bjb|
                    import re
                    stations = re.findall(r'\|([^|]+)\|([A-Z]+)\|', text)
                    if stations and len(stations) >= self.MIN_VALID_STATION_CODES:
                        self.station_codes = {name: code for name, code in stations}

                        # 保存缓存（写前已校验条数，避免空缓存污染）
                        if self.cache_enabled:
                            cache_file = os.path.join(self._data_dir, "station_codes.json")
                            with open(cache_file, 'w', encoding='utf-8') as f:
                                json.dump(self.station_codes, f, ensure_ascii=False, indent=2)

                        logger.info(f"✅ 获取车站编码成功: {len(self.station_codes)} 个车站")
                    else:
                        logger.error(
                            f"❌ 车站编码解析结果仅 {len(stations) if stations else 0} 条，"
                            "拒绝写缓存，保留内置映射"
                        )
                else:
                    logger.error(f"获取车站编码失败: {response.status}")

        except Exception as e:
            logger.error(f"获取车站编码异常: {e}")

    def _get_station_code(self, station_name: str) -> Optional[str]:
        """获取车站代码"""
        if not self.station_codes:
            return None

        # 精确匹配
        code = self.station_codes.get(station_name)
        if code:
            return code

        # 去掉"站"字再试
        if station_name.endswith("站") and len(station_name) > 1:
            code = self.station_codes.get(station_name[:-1])
            if code:
                return code

        # 模糊匹配（城市名）
        candidates = []
        for name, code in self.station_codes.items():
            if name.startswith(station_name) and len(station_name) >= 2:
                candidates.append((len(name), name, code))

        if candidates:
            candidates.sort()
            return candidates[0][2]

        return None

    async def query_train_schedule(self, date: str, from_station: str, to_station: str,
                                  include_price: bool = True) -> Dict:
        """
        查询火车时刻表

        Args:
            date: 出发日期 (YYYY-MM-DD)
            from_station: 出发站
            to_station: 到达站
            include_price: 是否包含价格信息

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

        if not from_code or not to_code:
            return {"error": f"找不到车站编码: {from_station} -> {to_station}"}

        try:
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
                    return {"error": "无法连接12306网站"}

            # 查询车次
            query_url = f"{BASE_URL}/otn/leftTicket/queryG"
            async with self.session.get(query_url, params=params) as response:
                if response.status != 200:
                    return {"error": "查询失败"}

                data = await response.json()

            if 'data' not in data:
                error_msg = data.get('messages', ['未知错误'])[0] if data.get('messages') else '数据获取失败'
                return {"error": f"12306接口错误: {error_msg}"}

            # 解析车次信息
            results = data['data'].get('result', [])
            train_list = []

            for item in results:
                train_info = self._parse_train_info(item)
                if train_info:
                    train_list.append(train_info)

            if not train_list:
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
            logger.error(f"查询异常: {e}")
            return {"error": f"查询失败: {str(e)}"}

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


if __name__ == "__main__":
    asyncio.run(main())
