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
import random
import re
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Tuple

import aiohttp

# 配置日志
logger = logging.getLogger(__name__)

# 12306 相关常量
STATION_NAMES_URL = "https://kyfw.12306.cn/otn/resources/js/framework/station_name.js"
CACHE_EXPIRE_DAYS = 7
BASE_URL = "https://kyfw.12306.cn"
PRICE_URL = "https://kyfw.12306.cn/otn/leftTicketPrice/queryAllPrice"

# 车站编码格式（12306 station_name.js 中的电报码，如 VNP / SHH）
STATION_CODE_RE = re.compile(r"^[A-Z]{2,5}$")

# 班次查询缓存默认参数（实例可通过 __init__ 覆盖行为）
SCHEDULE_CACHE_TTL = 300   # 秒
SCHEDULE_CACHE_MAX = 64    # 最大缓存条目
NEGATIVE_CACHE_TTL = 30    # 失败结果负缓存 TTL（秒）：12306 抖动时避免同参数反复打站
MIN_REQUEST_INTERVAL = 1.0  # 默认请求间隔（秒）

# ==================== 12306 leftTicket 返回字段偏移（'|' 分隔行） ====================
# 格式变更时只需改这里，_parse_train_info / 查询循环共用一份定义
IDX_TRAIN_NO = 2           # 车次编号（训练用，如 240000G1010）
IDX_TRAIN_NUMBER = 3       # 车次（如 G1）
IDX_DEPARTURE_TIME = 8     # 发车时间 HH:MM
IDX_ARRIVAL_TIME = 9       # 到站时间 HH:MM
IDX_DURATION = 10          # 历时 HH:MM
IDX_SOFT_SLEEPER = 23      # 软卧余票状态
IDX_NO_SEAT = 26           # 无座余票状态
IDX_HARD_SLEEPER = 28      # 硬卧余票状态
IDX_HARD_SEAT = 29         # 硬座余票状态
IDX_SECOND_CLASS = 30      # 二等座余票状态
IDX_FIRST_CLASS = 31       # 一等座余票状态
IDX_BUSINESS_SEAT = 32     # 商务座余票状态
MIN_TRAIN_FIELDS = 35      # 解析所需最少字段数（防御：字段过少视为垃圾行）


class InputValidator:
    """简单的输入验证器"""

    @staticmethod
    def validate_date(date_str: str) -> bool:
        if not date_str:
            return False
        pattern = r'^\d{4}-\d{2}-\d{2}$'
        return bool(re.match(pattern, date_str))

    @staticmethod
    def validate_address(address: str) -> bool:
        if not address or not isinstance(address, str):
            return False
        return len(address.strip()) >= 2


class Py12306Client:
    """
    py12306 客户端封装

    提供异步火车信息查询功能，支持：
    - 车次查询
    - 余票查询
    - 价格查询
    - 车站信息查询
    """

    # 班次查询缓存（同参数短 TTL，避免重复打 12306）+ 全局限速（跨实例共享）
    SCHEDULE_CACHE = {}        # (date, from_station, to_station) -> (timestamp, result)
    SCHEDULE_CACHE_TTL = SCHEDULE_CACHE_TTL
    SCHEDULE_CACHE_MAX = SCHEDULE_CACHE_MAX
    NEGATIVE_CACHE_TTL = NEGATIVE_CACHE_TTL
    FAIL_CACHE = {}            # 失败结果负缓存：cache_key -> (timestamp, result)
    LAST_CALL_TS = 0.0

    # 城市 -> 默认车站映射表（覆盖主要地级市及常用县级市）
    CITY_STATION_MAP = {
        # ===== 直辖市 =====
        "北京": "北京南", "上海": "上海虹桥", "天津": "天津",
        "重庆": "重庆北",

        # ===== 河北省 =====
        "石家庄": "石家庄", "唐山": "唐山", "秦皇岛": "秦皇岛",
        "邯郸": "邯郸东", "邢台": "邢台东", "保定": "保定东",
        "张家口": "张家口", "承德": "承德南", "沧州": "沧州西",
        "廊坊": "廊坊", "衡水": "衡水北",

        # ===== 山西省 =====
        "太原": "太原南", "大同": "大同南", "阳泉": "阳泉北",
        "长治": "长治东", "晋城": "晋城", "朔州": "朔州",
        "晋中": "晋中", "运城": "运城", "忻州": "忻州",
        "临汾": "临汾西", "吕梁": "吕梁",

        # ===== 内蒙古 =====
        "呼和浩特": "呼和浩特东", "包头": "包头", "乌海": "乌海",
        "赤峰": "赤峰", "通辽": "通辽", "鄂尔多斯": "鄂尔多斯",
        "呼伦贝尔": "海拉尔", "巴彦淖尔": "巴彦淖尔", "乌兰察布": "乌兰察布",

        # ===== 辽宁省 =====
        "沈阳": "沈阳北", "大连": "大连北", "鞍山": "鞍山西",
        "抚顺": "抚顺北", "本溪": "本溪", "丹东": "丹东",
        "锦州": "锦州南", "营口": "营口东", "阜新": "阜新",
        "辽阳": "辽阳", "盘锦": "盘锦", "铁岭": "铁岭西",
        "朝阳": "朝阳", "葫芦岛": "葫芦岛北",

        # ===== 吉林省 =====
        "长春": "长春", "吉林": "吉林", "四平": "四平东",
        "辽源": "辽源", "通化": "通化", "白山": "白山",
        "松原": "松原", "白城": "白城", "延边": "延吉西",

        # ===== 黑龙江省 =====
        "哈尔滨": "哈尔滨西", "齐齐哈尔": "齐齐哈尔南",
        "鸡西": "鸡西", "鹤岗": "鹤岗", "双鸭山": "双鸭山",
        "大庆": "大庆东", "伊春": "伊春", "佳木斯": "佳木斯",
        "七台河": "七台河", "牡丹江": "牡丹江", "黑河": "黑河",
        "绥化": "绥化", "大兴安岭": "加格达奇",

        # ===== 江苏省 =====
        "南京": "南京南", "苏州": "苏州", "无锡": "无锡东",
        "常州": "常州北", "徐州": "徐州东", "南通": "南通",
        "连云港": "连云港", "淮安": "淮安", "盐城": "盐城",
        "扬州": "扬州东", "镇江": "镇江", "泰州": "泰州",
        "宿迁": "宿迁",

        # ===== 浙江省 =====
        "杭州": "杭州东", "宁波": "宁波", "温州": "温州南",
        "嘉兴": "嘉兴南", "湖州": "湖州", "绍兴": "绍兴北",
        "金华": "金华", "衢州": "衢州", "舟山": "舟山",
        "台州": "台州西", "丽水": "丽水",

        # ===== 安徽省 =====
        "合肥": "合肥南", "芜湖": "芜湖", "蚌埠": "蚌埠南",
        "淮南": "淮南南", "马鞍山": "马鞍山东", "淮北": "淮北",
        "铜陵": "铜陵", "安庆": "安庆", "黄山": "黄山北",
        "滁州": "滁州", "阜阳": "阜阳西", "宿州": "宿州东",
        "六安": "六安", "亳州": "亳州南", "池州": "池州",
        "宣城": "宣城",

        # ===== 福建省 =====
        "福州": "福州", "厦门": "厦门", "莆田": "莆田",
        "三明": "三明北", "泉州": "泉州", "漳州": "漳州",
        "南平": "南平市", "龙岩": "龙岩", "宁德": "宁德",

        # ===== 江西省 =====
        "南昌": "南昌西", "景德镇": "景德镇北", "萍乡": "萍乡北",
        "九江": "九江", "新余": "新余北", "鹰潭": "鹰潭北",
        "赣州": "赣州西", "吉安": "吉安西", "宜春": "宜春",
        "抚州": "抚州", "上饶": "上饶",

        # ===== 山东省 =====
        "济南": "济南西", "青岛": "青岛", "淄博": "淄博",
        "枣庄": "枣庄", "东营": "东营", "烟台": "烟台",
        "潍坊": "潍坊", "济宁": "济宁", "泰安": "泰安",
        "威海": "威海", "日照": "日照", "临沂": "临沂北",
        "德州": "德州东", "聊城": "聊城", "滨州": "滨州",
        "菏泽": "菏泽东",

        # ===== 河南省 =====
        "郑州": "郑州东", "开封": "开封北", "洛阳": "洛阳龙门",
        "平顶山": "平顶山西", "安阳": "安阳东", "鹤壁": "鹤壁东",
        "新乡": "新乡东", "焦作": "焦作", "濮阳": "濮阳东",
        "许昌": "许昌东", "漯河": "漯河西", "三门峡": "三门峡南",
        "南阳": "南阳东", "商丘": "商丘", "信阳": "信阳东",
        "周口": "周口东", "驻马店": "驻马店西",

        # ===== 湖北省 =====
        "武汉": "武汉", "黄石": "黄石北", "十堰": "十堰东",
        "宜昌": "宜昌东", "襄阳": "襄阳东", "鄂州": "鄂州",
        "荆门": "荆门", "孝感": "孝感东", "荆州": "荆州",
        "黄冈": "黄冈东", "咸宁": "咸宁北", "随州": "随州",
        "恩施": "恩施",

        # ===== 湖南省 =====
        "长沙": "长沙南", "株洲": "株洲西", "湘潭": "湘潭",
        "衡阳": "衡阳东", "邵阳": "邵阳", "岳阳": "岳阳东",
        "常德": "常德", "张家界": "张家界西", "益阳": "益阳南",
        "郴州": "郴州西", "永州": "永州", "怀化": "怀化南",
        "娄底": "娄底南", "湘西": "吉首东",

        # ===== 广东省 =====
        "广州": "广州南", "深圳": "深圳北", "珠海": "珠海",
        "汕头": "汕头", "佛山": "佛山西", "韶关": "韶关",
        "湛江": "湛江西", "肇庆": "肇庆东", "江门": "江门",
        "茂名": "茂名", "惠州": "惠州南", "梅州": "梅州西",
        "汕尾": "汕尾", "河源": "河源东", "阳江": "阳江",
        "清远": "清远", "东莞": "东莞", "中山": "中山",
        "潮州": "潮汕", "揭阳": "揭阳", "云浮": "云浮东",

        # ===== 广西 =====
        "南宁": "南宁东", "柳州": "柳州", "桂林": "桂林北",
        "梧州": "梧州南", "北海": "北海", "防城港": "防城港北",
        "钦州": "钦州东", "贵港": "贵港", "玉林": "玉林",
        "百色": "百色", "贺州": "贺州", "河池": "河池",
        "来宾": "来宾北", "崇左": "崇左南",

        # ===== 海南省 =====
        "海口": "海口东", "三亚": "三亚", "三沙": "三沙",
        "儋州": "儋州",

        # ===== 四川省 =====
        "成都": "成都东", "自贡": "自贡", "攀枝花": "攀枝花南",
        "泸州": "泸州", "德阳": "德阳", "绵阳": "绵阳",
        "广元": "广元", "遂宁": "遂宁", "内江": "内江北",
        "乐山": "乐山", "南充": "南充", "眉山": "眉山",
        "宜宾": "宜宾西", "广安": "广安南", "达州": "达州",
        "雅安": "雅安", "巴中": "巴中", "资阳": "资阳北",

        # ===== 贵州省 =====
        "贵阳": "贵阳北", "六盘水": "六盘水", "遵义": "遵义",
        "安顺": "安顺西", "毕节": "毕节", "铜仁": "铜仁南",
        "黔西南": "兴义", "黔东南": "凯里南", "黔南": "都匀东",

        # ===== 云南省 =====
        "昆明": "昆明南", "曲靖": "曲靖北", "玉溪": "玉溪",
        "保山": "保山", "昭通": "昭通", "丽江": "丽江",
        "普洱": "普洱", "临沧": "临沧", "楚雄": "楚雄",
        "红河": "红河", "文山": "文山", "西双版纳": "西双版纳",
        "大理": "大理", "德宏": "芒市", "怒江": "怒江",
        "迪庆": "香格里拉",

        # ===== 西藏 =====
        "拉萨": "拉萨", "日喀则": "日喀则",

        # ===== 陕西省 =====
        "西安": "西安北", "铜川": "铜川东", "宝鸡": "宝鸡南",
        "咸阳": "咸阳西", "渭南": "渭南北", "延安": "延安",
        "汉中": "汉中", "榆林": "榆林", "安康": "安康",
        "商洛": "商洛",

        # ===== 甘肃省 =====
        "兰州": "兰州西", "嘉峪关": "嘉峪关南", "金昌": "金昌",
        "白银": "白银南", "天水": "天水南", "武威": "武威",
        "张掖": "张掖西", "平凉": "平凉", "酒泉": "酒泉南",
        "庆阳": "庆阳", "定西": "定西北", "陇南": "陇南",
        "临夏": "临夏", "甘南": "甘南",

        # ===== 青海省 =====
        "西宁": "西宁", "海东": "海东", "海北": "海北",
        "黄南": "黄南", "海南": "海南", "果洛": "果洛",
        "玉树": "玉树", "海西": "海西",

        # ===== 宁夏 =====
        "银川": "银川", "石嘴山": "石嘴山", "吴忠": "吴忠",
        "固原": "固原", "中卫": "中卫南",

        # ===== 新疆 =====
        "乌鲁木齐": "乌鲁木齐", "克拉玛依": "克拉玛依",
        "吐鲁番": "吐鲁番北", "哈密": "哈密", "阿克苏": "阿克苏",
        "喀什": "喀什", "和田": "和田", "伊犁": "伊宁",
        "塔城": "塔城", "阿勒泰": "阿勒泰", "石河子": "石河子",
        "昌吉": "昌吉", "博尔塔拉": "博乐", "巴音郭楞": "库尔勒",
        "克孜勒苏": "阿图什",

        # ===== 港澳台 =====
        "香港": "香港西九龙", "澳门": "珠海", "台北": "台北",

        # ===== 特殊/县级市常用 =====
        "义乌": "义乌", "昆山": "昆山南", "晋江": "晋江",
        "慈溪": "余姚北", "江阴": "江阴", "张家港": "张家港",
        "常熟": "常熟", "太仓": "太仓", "嘉善": "嘉善南",
        "海宁": "海宁西", "桐乡": "桐乡", "诸暨": "诸暨",
        "温岭": "温岭", "瑞安": "瑞安", "乐清": "乐清",
        "余姚": "余姚北", "都江堰": "都江堰", "峨眉山": "峨眉山",
        "黄山": "黄山北", "武夷山": "武夷山北",

        # ===== 常见输入变体 =====
        "滨海新区": "天津", "滨海": "天津",
        "雄安新区": "雄安", "雄安": "雄安",
    }

    def __init__(self, cache_enabled: bool = True, timeout: int = 15,
                 max_retries: int = 2, min_interval: float = MIN_REQUEST_INTERVAL):
        """
        初始化 py12306 客户端

        Args:
            cache_enabled: 是否启用缓存
            timeout: 请求超时时间（秒）
            max_retries: 查询失败最大重试次数
            min_interval: 两次请求之间的最小间隔（秒），防高频被封
        """
        self.cache_enabled = cache_enabled
        self.timeout = timeout
        self.max_retries = max_retries
        self.min_interval = min_interval
        self.session = None
        self.station_codes = {}
        self._city_station_map_auto = {}
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

    @staticmethod
    def _is_valid_station_codes(codes) -> bool:
        """校验车站编码表：非空字典、键非空、值符合 12306 电报码格式、条目数合理。

        防止损坏/垃圾缓存（如 {"": ""}）被直接加载后导致所有车站解析失败。
        """
        if not isinstance(codes, dict) or len(codes) < 100:
            return False
        for name, code in codes.items():
            if not isinstance(name, str) or not name.strip():
                return False
            if not isinstance(code, str) or not STATION_CODE_RE.match(code):
                return False
        return True

    def _load_station_codes(self):
        """加载车站编码信息（带结构校验，损坏缓存自动丢弃重新抓取）"""
        cache_file = os.path.join(self._data_dir, "station_codes.json")

        if self.cache_enabled and os.path.exists(cache_file):
            try:
                mtime = os.path.getmtime(cache_file)
                if time.time() - mtime < CACHE_EXPIRE_DAYS * 24 * 3600:
                    with open(cache_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if self._is_valid_station_codes(data):
                        self.station_codes = data
                        logger.info(f"✅ 加载缓存车站编码: {len(self.station_codes)} 个车站")
                        return
                    size = len(data) if isinstance(data, dict) else "非字典"
                    logger.warning(f"⚠️ 车站编码缓存校验失败（{size} 条），丢弃并重新抓取")
            except Exception as e:
                logger.warning(f"加载车站编码缓存失败: {e}")

        self.station_codes = {}

    async def _rate_limit(self):
        """跨实例请求限速，防止高频调用触发 12306 反爬。"""
        now = time.time()
        wait = self.min_interval - (now - Py12306Client.LAST_CALL_TS)
        if wait > 0:
            await asyncio.sleep(wait)
        Py12306Client.LAST_CALL_TS = time.time()

    async def _fetch_station_codes(self):
        """获取车站编码信息 - 增强版（支持多种格式）"""
        try:
            if not self.session:
                await self._init_session()

            async with self.session.get(STATION_NAMES_URL) as response:
                if response.status == 200:
                    text = await response.text()

                    # 修正：使用正确的正则匹配 12306 返回格式
                    # 格式: @bjb|北京北|VAP|beijingbei|bjb|0|0
                    stations = re.findall(r'@[a-z]+\|([^|]+)\|([A-Z]+)\|[^|]+\|[^|]+\|', text)
                    if not stations:
                        stations = re.findall(r'@[a-z]+\|([^|]+)\|([A-Z]+)\|[^|]+\|[^|]+', text)
                    if not stations:
                        stations = re.findall(r'\|([^|]+)\|([A-Z]+)\|', text)

                    if stations:
                        self.station_codes = {name: code for name, code in stations}

                        if self._is_valid_station_codes(self.station_codes):
                            if self.cache_enabled:
                                cache_file = os.path.join(self._data_dir, "station_codes.json")
                                with open(cache_file, 'w', encoding='utf-8') as f:
                                    json.dump(self.station_codes, f, ensure_ascii=False, indent=2)

                            logger.info(f"✅ 获取车站编码成功: {len(self.station_codes)} 个车站")
                            sample = list(self.station_codes.items())[:5]
                            logger.info(f"   示例: {sample}")
                        else:
                            # 解析出垃圾数据（如 {"": ""}），丢弃且不写缓存，避免污染 7 天
                            self.station_codes = {}
                            logger.error(f"❌ 解析结果不合法（{len(stations)} 条），不写入缓存")
                    else:
                        logger.error(f"❌ 无法解析车站编码，响应内容前200字符: {text[:200]}")
                else:
                    logger.error(f"❌ 获取车站编码失败，HTTP状态码: {response.status}")

        except Exception as e:
            logger.error(f"❌ 获取车站编码异常: {e}")
            import traceback
            logger.error(traceback.format_exc())

    def _build_city_station_map_auto(self) -> Dict[str, List[str]]:
        """
        从 station_codes 自动构建城市→车站列表映射
        规则：去掉"站"、方位后缀后匹配
        """
        if self._city_station_map_auto:
            return self._city_station_map_auto

        city_map = {}
        for station_name in self.station_codes.keys():
            city = station_name
            for suffix in ["东站", "南站", "西站", "北站", "机场站", "站", "东", "南", "西", "北"]:
                if city.endswith(suffix):
                    city = city[:-len(suffix)]
                    break
            if city not in city_map:
                city_map[city] = []
            city_map[city].append(station_name)

        self._city_station_map_auto = city_map
        return city_map

    def _get_station_code(self, station_name: str) -> Optional[str]:
        """
        获取车站代码 - 增强版
        支持：精确匹配 → 去"站"匹配 → 硬编码城市映射 → 自动城市推导 → 模糊前缀匹配
        """
        if not self.station_codes:
            return None

        # 1. 精确匹配
        code = self.station_codes.get(station_name)
        if code:
            return code

        # 2. 去掉"站"字再试
        if station_name.endswith("站") and len(station_name) > 1:
            code = self.station_codes.get(station_name[:-1])
            if code:
                return code

        # 3. 硬编码城市映射表
        if station_name in self.CITY_STATION_MAP:
            mapped_station = self.CITY_STATION_MAP[station_name]
            code = self.station_codes.get(mapped_station)
            if code:
                return code

        # 4. 自动推导：从 station_codes 动态解析城市归属
        city_map = self._build_city_station_map_auto()
        if station_name in city_map:
            stations = city_map[station_name]
            # 优先级：同名站 > 含方位的高铁站 > 第一个
            for s in stations:
                if s == station_name:
                    return self.station_codes[s]
            for s in stations:
                if any(d in s for d in ["东", "南", "西", "北"]):
                    return self.station_codes[s]
            return self.station_codes[stations[0]]

        # 5. 模糊前缀匹配
        candidates = [
            (len(name), name, code)
            for name, code in self.station_codes.items()
            if name.startswith(station_name) and len(station_name) >= 2
        ]
        if candidates:
            candidates.sort()
            return candidates[0][2]

        # 6. 包含关键词的特殊映射
        if "滨海" in station_name:
            return self.station_codes.get("天津")

        return None

    async def query_train_schedule(self, date: str, from_station: str, to_station: str,
                                   include_price: bool = True, max_retries: int = None) -> Dict:
        """
        查询火车时刻表 - 增强版（含 TTL 缓存、失败负缓存、限速、票价增强）

        Args:
            date: 出发日期 (YYYY-MM-DD)
            from_station: 出发站
            to_station: 到达站
            include_price: 是否包含价格信息（失败自动回退，不影响主流程）
            max_retries: 最大重试次数，默认取实例配置

        Returns:
            火车时刻表信息；失败时返回 {"error": ..., "error_code": ...}

        重试策略（P0-V2 #1）：只对网络/传输类异常重试；
        NO_TRAINS / 12306接口错误 等确定性错误立即返回。
        会话保活（P0-V2 #2）：不在此关闭，由进程级 atexit 统一关闭。
        失败负缓存（P2-V2 #9）：失败结果短 TTL（30s），上游抖动时不反复打站。
        """
        if not InputValidator.validate_date(date):
            return {"error": f"日期格式错误: {date}", "error_code": "INVALID_INPUT"}

        if not InputValidator.validate_address(from_station):
            return {"error": f"出发站格式错误: {from_station}", "error_code": "INVALID_INPUT"}

        if not InputValidator.validate_address(to_station):
            return {"error": f"到达站格式错误: {to_station}", "error_code": "INVALID_INPUT"}

        max_retries = max_retries or self.max_retries
        cache_key = (date, from_station, to_station)

        # ==================== TTL 缓存命中 ====================
        if self.cache_enabled:
            cached = Py12306Client.SCHEDULE_CACHE.get(cache_key)
            if cached and time.time() - cached[0] < Py12306Client.SCHEDULE_CACHE_TTL:
                logger.info(f"💾 命中班次缓存: {from_station} → {to_station} ({date})")
                return cached[1]

            # 失败负缓存（P2-V2 #9）：30s 内同参数失败直接返回，不打上游
            failed = Py12306Client.FAIL_CACHE.get(cache_key)
            if failed and time.time() - failed[0] < Py12306Client.NEGATIVE_CACHE_TTL:
                logger.info(f"⛔ 命中失败负缓存: {from_station} → {to_station} ({date})")
                return failed[1]

        if not self.session:
            await self._init_session()

        if not self.station_codes:
            await self._fetch_station_codes()

        from_code = self._get_station_code(from_station)
        to_code = self._get_station_code(to_station)

        logger.info(f"🔍 车站编码查询: {from_station} -> {from_code}, {to_station} -> {to_code}")
        logger.info(f"📊 当前车站编码库大小: {len(self.station_codes)} 个")

        if not from_code or not to_code:
            if not self.station_codes:
                logger.warning("⚠️ 车站编码库为空，尝试重新获取...")
                await self._fetch_station_codes()
                from_code = self._get_station_code(from_station)
                to_code = self._get_station_code(to_station)

            if not from_code or not to_code:
                available = list(self.station_codes.keys())[:10]
                logger.error(f"❌ 找不到车站编码: {from_station}({from_code}) -> {to_station}({to_code})")
                logger.error(f"   编码库示例: {available}")
                return {"error": f"找不到车站编码: {from_station} -> {to_station}",
                        "error_code": "STATION_NOT_FOUND"}

        last_error = None
        last_error_code = "QUERY_FAILED"
        for attempt in range(max_retries):
            try:
                if attempt > 0:
                    wait_time = random.uniform(1.0, 2.0)
                    logger.info(f"⏱️ 第 {attempt + 1} 次重试，等待 {wait_time:.1f} 秒...")
                    await asyncio.sleep(wait_time)

                # 限速：同一进程内所有实例共享间隔
                await self._rate_limit()

                params = {
                    "leftTicketDTO.train_date": date,
                    "leftTicketDTO.from_station": from_code,
                    "leftTicketDTO.to_station": to_code,
                    "purpose_codes": "ADULT"
                }

                init_url = f"{BASE_URL}/otn/leftTicket/init"
                async with self.session.get(init_url) as response:
                    if response.status != 200:
                        last_error = "无法连接12306网站"
                        last_error_code = "UPSTREAM_UNREACHABLE"
                        continue

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
                    last_error = "所有查询接口均不可用"
                    last_error_code = "UPSTREAM_UNREACHABLE"
                    continue

                if 'data' not in data:
                    error_msg = data.get('messages', ['未知错误'])[0] if data.get('messages') else '数据获取失败'
                    last_error = f"12306接口错误: {error_msg}"
                    last_error_code = "UPSTREAM_ERROR"
                    logger.warning(f"⚠️ 12306返回错误: {error_msg}（确定性错误，不重试）")
                    break  # P0-V2 #1：业务性错误重试无意义，立即返回

                results = data['data'].get('result', [])
                pairs = []
                for item in results:
                    fields = item.split('|')
                    train_no = fields[IDX_TRAIN_NO] if len(fields) > IDX_TRAIN_NO else None
                    train_info = self._parse_train_info(item)
                    if train_info:
                        pairs.append((train_no, train_info))

                if not pairs:
                    last_error = "未找到符合条件的火车班次"
                    last_error_code = "NO_TRAINS"
                    logger.warning(f"⚠️ 未找到车次（确定性错误，不重试）")
                    break  # P0-V2 #1：无班次是确定性结果，重试只会白白等待

                pairs.sort(key=lambda p: p[1]['departure_time'])
                train_list = [p[1] for p in pairs]
                train_nos = [p[0] for p in pairs]

                # 票价增强：仅取前 3 趟（限频），失败静默回退
                if include_price:
                    for i in range(min(3, len(train_list))):
                        train_no = train_nos[i]
                        if not train_no:
                            continue
                        try:
                            prices = await self._fetch_train_price(train_no, from_code, to_code, date)
                            if prices:
                                train_list[i]["price"] = self._extract_prices(prices)
                        except Exception as e:
                            logger.warning(f"⚠️ 票价查询失败 {train_no}: {e}")

                result = {
                    "success": True,
                    "date": date,
                    "from_station": from_station,
                    "to_station": to_station,
                    "total_trains": len(train_list),
                    "trains": train_list
                }

                # 仅缓存成功结果；成功时清除失败负缓存
                if self.cache_enabled:
                    Py12306Client.FAIL_CACHE.pop(cache_key, None)
                    Py12306Client.SCHEDULE_CACHE[cache_key] = (time.time(), result)
                    if len(Py12306Client.SCHEDULE_CACHE) > Py12306Client.SCHEDULE_CACHE_MAX:
                        oldest_key = min(Py12306Client.SCHEDULE_CACHE,
                                         key=lambda k: Py12306Client.SCHEDULE_CACHE[k][0])
                        Py12306Client.SCHEDULE_CACHE.pop(oldest_key, None)

                return result

            except Exception as e:
                logger.error(f"❌ 查询异常 (尝试 {attempt + 1}/{max_retries}): {e}")
                last_error = f"查询失败: {str(e)}"
                last_error_code = "QUERY_FAILED"

        # 所有重试都失败后，写入失败负缓存（短 TTL，避免上游抖动时反复打站）。
        # 会话保持存活（P0-V2 #2）：由进程级 atexit 统一关闭，不再每次调用后 close。
        if self.cache_enabled:
            Py12306Client.FAIL_CACHE[cache_key] = (time.time(),
                                                   {"error": last_error, "error_code": last_error_code})
            if len(Py12306Client.FAIL_CACHE) > Py12306Client.SCHEDULE_CACHE_MAX:
                oldest_key = min(Py12306Client.FAIL_CACHE,
                                 key=lambda k: Py12306Client.FAIL_CACHE[k][0])
                Py12306Client.FAIL_CACHE.pop(oldest_key, None)
        return {"error": last_error or "查询失败：超出最大重试次数",
                "error_code": last_error_code}

    async def _fetch_train_price(self, train_no: str, from_code: str, to_code: str,
                                 date: str) -> Optional[Dict]:
        """查询单趟列车票价（queryAllPrice）。解析失败/接口异常返回 None，调用方回退估算。

        12306 返回结构（seat_types 编码 → 票价条目），防御式解析：
        {"A1": {"seat_type_name": "商务座", "seat_price": "933.0", ...}, ...}
        """
        try:
            if not self.session:
                await self._init_session()
            params = {
                "train_no": train_no,
                "from_station_telecode": from_code,
                "to_station_telecode": to_code,
                "seat_types": "A1A2A3A4A6A7A9MOP1P2P3P4P5P6",
                "train_date": date,
            }
            async with self.session.get(PRICE_URL, params=params) as response:
                if response.status != 200:
                    return None
                data = await response.json()
            if not data or data.get("status") is not True or not data.get("data"):
                return None
            payload = data["data"]
            if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
                payload = payload["data"]
            return payload if isinstance(payload, dict) else None
        except Exception as e:
            logger.warning(f"⚠️ 票价接口异常 {train_no}: {e}")
            return None

    @staticmethod
    def _extract_prices(payload: Dict) -> Dict:
        """把 queryAllPrice 返回的座位编码映射为可读价格字段（无法解析的座位为 None）。"""
        seat_code_map = {
            "A1": "business_seat", "A2": "first_class", "A3": "second_class",
            "A4": "premier_seat", "A6": "deluxe_soft_sleeper", "A9": "first_sleeper",
            "M": "second_sleeper", "O": "first_class", "P1": "hard_seat",
            "P2": "soft_seat", "P3": "hard_sleeper", "P4": "soft_sleeper",
            "P5": "no_seat", "P6": "deluxe_soft_sleeper",
        }
        prices = {}
        for code, entry in payload.items():
            field = seat_code_map.get(code)
            if not field or not isinstance(entry, dict):
                continue
            try:
                val = entry.get("seat_price")
                prices[field] = float(val) if val not in (None, "") else None
            except (TypeError, ValueError):
                prices[field] = None
        return prices

    def _parse_train_info(self, train_data: str) -> Optional[Dict]:
        """解析车次信息。

        注意：seats 字段为「余票状态」（"有"/"无"/数量），**不是票价**；
        票价见 price 字段（由 _fetch_train_price 补充，可能缺失）。

        字段偏移集中在模块级 IDX_* 命名常量（见 train_crawler.py 头部），
        12306 格式变更时只需调整常量表。
        """
        if not isinstance(train_data, str):
            logger.warning(f"解析车次失败：输入类型异常 {type(train_data).__name__}")
            return None
        try:
            info = train_data.split('|')
            if len(info) < MIN_TRAIN_FIELDS:
                return None

            train_number = info[IDX_TRAIN_NUMBER]
            departure_time = info[IDX_DEPARTURE_TIME]
            arrival_time = info[IDX_ARRIVAL_TIME]
            duration = info[IDX_DURATION]

            seats = {
                "business_seat": info[IDX_BUSINESS_SEAT] if info[IDX_BUSINESS_SEAT] else '无',
                "first_class": info[IDX_FIRST_CLASS] if info[IDX_FIRST_CLASS] else '无',
                "second_class": info[IDX_SECOND_CLASS] if info[IDX_SECOND_CLASS] else '无',
                "soft_sleeper": info[IDX_SOFT_SLEEPER] if info[IDX_SOFT_SLEEPER] else '无',
                "hard_sleeper": info[IDX_HARD_SLEEPER] if info[IDX_HARD_SLEEPER] else '无',
                "hard_seat": info[IDX_HARD_SEAT] if info[IDX_HARD_SEAT] else '无',
                "no_seat": info[IDX_NO_SEAT] if info[IDX_NO_SEAT] else '无'
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

    # ==================== 同步包装方法 ====================

    def query_train_schedule_sync(self, date: str, from_station: str, to_station: str,
                                  include_price: bool = True) -> Dict:
        """
        同步查询火车时刻表（包装异步方法，方便非异步环境调用）
        """
        try:
            loop = asyncio.get_event_loop()
            if loop.is_running():
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                except ImportError:
                    logger.warning("nest_asyncio 未安装，在运行中的事件循环中可能无法正常工作")
                    return {"error": "当前处于运行中的事件循环，请安装 nest_asyncio 或直接使用异步方法"}
                return loop.run_until_complete(
                    self.query_train_schedule(date, from_station, to_station, include_price)
                )
            else:
                return loop.run_until_complete(
                    self.query_train_schedule(date, from_station, to_station, include_price)
                )
        except RuntimeError:
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
                try:
                    import nest_asyncio
                    nest_asyncio.apply()
                except ImportError:
                    return {"error": "当前处于运行中的事件循环，请安装 nest_asyncio 或直接使用异步方法"}
                return loop.run_until_complete(self.get_station_list())
            else:
                return loop.run_until_complete(self.get_station_list())
        except RuntimeError:
            return asyncio.run(self.get_station_list())
        except Exception as e:
            logger.error(f"同步获取车站列表失败: {e}")
            return {}


# ==================== 测试函数 ====================

async def main():
    """测试函数"""
    async with Py12306Client() as client:
        result = await client.query_train_schedule(
            date="2026-08-20",
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