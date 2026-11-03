"""
工具函数模块
===========

提供各种辅助功能函数
"""


def estimate_train_cost(start_location: str, end_location: str, base_rate: float = 0.4) -> float:
    """
    估算火车票价

    Args:
        start_location: 起点位置坐标 "经度,纬度"
        end_location: 终点位置坐标 "经度,纬度"
        base_rate: 基础费率（每公里价格）

    Returns:
        float: 估算的火车票价
    """
    try:
        # 简单的距离估算（实际项目中应该使用地图API）
        start_lng, start_lat = map(float, start_location.split(','))
        end_lng, end_lat = map(float, end_location.split(','))

        # 使用简化的距离计算（欧几里得距离）
        distance_km = ((end_lng - start_lng) ** 2 + (end_lat - start_lat) ** 2) ** 0.5 * 111  # 粗略转换为公里

        # 估算火车票价
        estimated_cost = max(100, min(800, distance_km * base_rate))
        return estimated_cost

    except Exception:
        # 出现错误时返回默认价格
        return 300.0


def format_duration(minutes: int) -> str:
    """
    格式化时长显示

    Args:
        minutes: 分钟数

    Returns:
        str: 格式化的时长字符串
    """
    if minutes <= 0:
        # 0/负值通常意味着数据缺失或解析失败，不应显示成「0 minutes」这种看似有效的结论
        return "暂无数据"
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
