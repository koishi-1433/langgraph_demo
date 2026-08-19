"""
输入验证模块
===========

提供安全的输入验证功能
"""

import re
from typing import Optional, Tuple
from datetime import datetime


class InputValidator:
    """输入验证器"""

    @staticmethod
    def validate_coordinate(coord_str: str) -> bool:
        """
        验证坐标格式

        Args:
            coord_str: 坐标字符串 "经度,纬度"
            东经正、西经负；北纬正、南纬负
        Returns:
            bool: 是否有效
        """
        if not coord_str or not isinstance(coord_str, str):
            return False

        pattern = r'^-?\d+\.?\d*,-?\d+\.?\d*$'
        if not re.match(pattern, coord_str):
            # 不能匹配正则
            return False

        try:
            lng, lat = map(float, coord_str.split(','))
            return -180 <= lng <= 180 and -90 <= lat <= 90
        except ValueError:
            return False

    @staticmethod
    def validate_date(date_str: str) -> bool:
        """
        验证日期格式

        Args:
            date_str: 日期字符串 "YYYY-MM-DD"

        Returns:
            bool: 是否有效
        """
        if not date_str or not isinstance(date_str, str):
            return False

        try:
            datetime.strptime(date_str, "%Y-%m-%d")
            return True
        except ValueError:
            return False

    @staticmethod
    def validate_address(address: str) -> bool:
        """
        验证地址格式

        Args:
            address: 地址字符串

        Returns:
            bool: 是否有效
        """
        if not address or not isinstance(address, str):
            return False

        # 基本长度和字符检查
        return len(address.strip()) > 0 and len(address) <= 200

    @staticmethod
    def validate_api_key(api_key: str) -> bool:
        """
        验证API密钥格式

        Args:
            api_key: API密钥

        Returns:
            bool: 是否有效
        """
        if not api_key or not isinstance(api_key, str):
            return False

        # 高德地图API密钥通常是32位字符串
        return len(api_key.strip()) >= 10 and len(api_key) <= 100

    @staticmethod
    def sanitize_string(input_str: str, max_length: int = 200) -> str:
        """
        清理字符串输入

        Args:
            input_str: 输入字符串
            max_length: 最大长度

        Returns:
            str: 清理后的字符串
        """
        if not isinstance(input_str, str):
            return ""

        # 移除危险字符，限制长度
        sanitized = re.sub(r'[<>"\'&]', '', input_str.strip())
        return sanitized[:max_length]

    @staticmethod
    def parse_coordinate(coord_str: str) -> Optional[Tuple[float, float]]:
        """
        解析并验证坐标

        Args:
            coord_str: 坐标字符串 "经度,纬度"

        Returns:
            tuple: (经度, 纬度) 或 None
        """
        if not InputValidator.validate_coordinate(coord_str):
            return None

        try:
            lng, lat = map(float, coord_str.split(','))
            return lng, lat
        except ValueError:
            return None


def validate_required_params(params: dict, required_keys: list) -> Tuple[bool, str]:
    """
    验证必需参数

    Args:
        params: 参数字典
        required_keys: 必需键列表

    Returns:
        tuple: (是否有效, 错误信息)
    """
    for key in required_keys:
        if key not in params or params[key] is None:
            return False, f"Missing required parameter: {key}"
        if isinstance(params[key], str) and not params[key].strip():
            return False, f"Empty required parameter: {key}"

    return True, ""
