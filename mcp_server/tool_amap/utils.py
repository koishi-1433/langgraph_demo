"""
工具函数模块
===========

提供各种辅助功能函数
"""
import math

# ==================== 火车票价估算（P1-V2 #4） ====================
# 公式：haversine 直线距离 × 1.15 绕行系数 × 分级费率
# 分级费率贴近高铁二等座实际定价斜率：短途单价高、长途单价低
CIRCUITY_FACTOR = 1.15          # 直线距离 → 铁路里程 绕行系数
TIER_RATES = [(1000.0, 0.42), (500.0, 0.45), (0.0, 0.5)]  # (distance_lower_bound, rate)
ESTIMATE_FLOOR = 30.0           # 最低估算价（元）
ESTIMATE_CAP = 2000.0           # 最高估算价（元），覆盖长途（京广 850+、京哈 900+）
FALLBACK_ESTIMATE = 300.0       # 坐标缺失/解析失败时的默认价


def _haversine_km(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点球面距离（km），与 route_planner._calculate_straight_distance 同一公式。"""
    lat1_rad = math.radians(lat1)
    lng1_rad = math.radians(lng1)
    lat2_rad = math.radians(lat2)
    lng2_rad = math.radians(lng2)
    delta_lat = lat2_rad - lat1_rad
    delta_lng = lng2_rad - lng1_rad
    a = (math.sin(delta_lat / 2) ** 2 +
         math.cos(lat1_rad) * math.cos(lat2_rad) * math.sin(delta_lng / 2) ** 2)
    c = 2 * math.asin(math.sqrt(a))
    return 6371.0 * c


def estimate_train_cost_by_distance(distance_km: float) -> float:
    """按直线距离估算二等座票价（分级费率，含绕行系数与上下限）。

    Args:
        distance_km: 起终点直线距离（公里）

    Returns:
        float: 估算的二等座票价（元）
    """
    if not isinstance(distance_km, (int, float)) or distance_km <= 0:
        return FALLBACK_ESTIMATE
    rate = 0.5
    for bound, tier_rate in TIER_RATES:
        if distance_km >= bound:
            rate = tier_rate
            break
    cost = distance_km * CIRCUITY_FACTOR * rate
    return max(ESTIMATE_FLOOR, min(ESTIMATE_CAP, cost))


def estimate_train_cost(start_location: str, end_location: str) -> float:
    """
    估算火车二等座票价（haversine 直线距离 × 1.15 绕行系数 × 分级费率）

    Args:
        start_location: 起点位置坐标 "经度,纬度"
        end_location: 终点位置坐标 "经度,纬度"

    Returns:
        float: 估算的火车票价
    """
    try:
        start_lng, start_lat = map(float, start_location.split(','))
        end_lng, end_lat = map(float, end_location.split(','))
        distance_km = _haversine_km(start_lng, start_lat, end_lng, end_lat)
        return estimate_train_cost_by_distance(distance_km)
    except Exception:
        # 坐标缺失/解析失败时返回默认价格
        return FALLBACK_ESTIMATE


def format_duration(minutes: int) -> str:
    """
    格式化时长显示

    Args:
        minutes: 分钟数

    Returns:
        str: 格式化的时长字符串
    """
    if minutes < 60:
        return f"{minutes} minutes"
    else:
        hours = minutes // 60
        remaining_minutes = minutes % 60
        if remaining_minutes == 0:
            return f"{hours} hours"
        else:
            return f"{hours}h {remaining_minutes}m"


def format_distance(kilometers: float) -> str:
    """
    格式化距离显示

    Args:
        kilometers: 公里数

    Returns:
        str: 格式化的距离字符串
    """
    if kilometers < 1:
        return f"{kilometers * 1000:.0f}m"
    else:
        return f"{kilometers:.1f}km"


def format_cost(cny: float) -> str:
    """
    格式化费用显示

    Args:
        cny: 人民币金额

    Returns:
        str: 格式化的费用字符串
    """
    return f"CNY {cny:.2f}"
