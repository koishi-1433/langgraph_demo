"""
配置文件
=======

集中管理所有配置参数
"""

from typing import Dict, Any
import os


class Config:
    """配置管理类"""

    # 默认配置
    DEFAULT_CONFIG = {
        # 距离阈值（公里）
        "TRAIN_DISTANCE_THRESHOLD": 300,

        # 费用计算参数
        "DRIVING_COST_PER_KM": 0.8,
        "TRAIN_COST_BASE_RATE": 0.4,
        "MIN_TRAIN_COST": 100,
        "MAX_TRAIN_COST": 800,

        # API超时设置
        "API_TIMEOUT": 15,
        "AMAP_API_TIMEOUT": 15,

        # 搜索半径（米）
        "TRAIN_STATION_SEARCH_RADIUS": 50000,
        "POI_SEARCH_RADIUS": 1000,

        # 浏览器设置
        "BROWSER_HEADLESS": True,
        "BROWSER_TIMEOUT": 30,

        # 日志设置
        "LOG_LEVEL": "INFO",
        "LOG_FORMAT": "%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    }

    def __init__(self):
        """初始化配置"""
        self._config = self.DEFAULT_CONFIG.copy()
        self._load_environment_variables()

    def _load_environment_variables(self):
        """从环境变量加载配置"""
        env_mappings = {
            "ROUTE_PLANNING_TRAIN_DISTANCE_THRESHOLD": "TRAIN_DISTANCE_THRESHOLD",
            "ROUTE_PLANNING_DRIVING_COST_PER_KM": "DRIVING_COST_PER_KM",
            "ROUTE_PLANNING_TRAIN_COST_BASE_RATE": "TRAIN_COST_BASE_RATE",
            "ROUTE_PLANNING_API_TIMEOUT": "API_TIMEOUT",
            "ROUTE_PLANNING_LOG_LEVEL": "LOG_LEVEL",
        }

        for env_key, config_key in env_mappings.items():
            if env_key in os.environ:
                value = os.environ[env_key]
                # 尝试转换为适当类型
                if config_key in ["TRAIN_DISTANCE_THRESHOLD", "API_TIMEOUT"]:
                    try:
                        self._config[config_key] = int(value)
                    except ValueError:
                        pass
                elif config_key in ["DRIVING_COST_PER_KM", "TRAIN_COST_BASE_RATE"]:
                    try:
                        self._config[config_key] = float(value)
                    except ValueError:
                        pass
                else:
                    self._config[config_key] = value

    def get(self, key: str, default=None) -> Any:
        """
        获取配置值

        Args:
            key: 配置键
            default: 默认值

        Returns:
            配置值
        """
        return self._config.get(key, default)

    def set(self, key: str, value: Any):
        """
        设置配置值

        Args:
            key: 配置键
            value: 配置值
        """
        self._config[key] = value

    def update(self, config_dict: Dict[str, Any]):
        """
        批量更新配置

        Args:
            config_dict: 配置字典
        """
        self._config.update(config_dict)

    def get_all(self) -> Dict[str, Any]:
        """
        获取所有配置

        Returns:
            配置字典
        """
        return self._config.copy()


# 全局配置实例
config = Config()


# 便捷函数
def get_config(key: str, default=None) -> Any:
    """获取配置值的便捷函数"""
    return config.get(key, default)


def set_config(key: str, value: Any):
    """设置配置值的便捷函数"""
    config.set(key, value)
