"""
全局配置加载与管理
"""
import os
import yaml
from typing import Dict, Any
from pathlib import Path


class ConfigLoader:
    """配置加载器"""
    
    _instance = None
    _config: Dict[str, Any] = None
    _config_path: str = None
    
    def __new__(cls, config_path: str = None):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._config_path = config_path or os.path.join(
                os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                "config", "global_config.yaml"
            )
            cls._instance._load_config()
        return cls._instance
    
    def _load_config(self) -> None:
        """加载YAML配置文件"""
        config_file = Path(self._config_path)
        if not config_file.exists():
            raise FileNotFoundError(f"配置文件不存在: {self._config_path}")
        
        with open(config_file, 'r', encoding='utf-8') as f:
            self._config = yaml.safe_load(f)
    
    def get(self, key_path: str, default: Any = None) -> Any:
        """
        获取配置项，支持点号分隔的路径访问
        例如: config.get('pre_check.test_coverage.min_unit_test_coverage')
        """
        keys = key_path.split('.')
        value = self._config
        
        try:
            for key in keys:
                value = value[key]
            return value
        except (KeyError, TypeError):
            return default
    
    def get_all(self) -> Dict[str, Any]:
        """获取完整配置"""
        return self._config
    
    def reload(self) -> None:
        """重新加载配置"""
        self._load_config()


def get_config() -> ConfigLoader:
    """获取全局配置实例"""
    return ConfigLoader()
