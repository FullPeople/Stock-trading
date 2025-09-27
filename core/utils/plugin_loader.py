# core/utils/plugin_loader.py
# 功能：插件加载器，支持动态发现和加载平台/策略插件
import os
import json
import importlib
from typing import Dict, List, Any, Optional, Type, Tuple
from pathlib import Path
from core.logger import logger

class PluginLoadError(Exception):
    """插件加载异常"""
    pass

class PluginValidator:
    """插件配置验证器"""
    
    @staticmethod
    def validate_platform_plugin(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """验证平台插件配置"""
        errors = []
        required_fields = [
            "name", "display_name", "adapter_class", "version", 
            "capabilities", "required_credentials"
        ]
        
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
        
        # 验证capabilities结构
        if "capabilities" in config:
            cap = config["capabilities"]
            cap_required = ["hedge_support", "position_mode", "unit_type", "supported_order_types"]
            for field in cap_required:
                if field not in cap:
                    errors.append(f"Missing capability field: {field}")
        
        # 验证required_credentials结构
        if "required_credentials" in config:
            for i, cred in enumerate(config["required_credentials"]):
                if not isinstance(cred, dict):
                    errors.append(f"Credential {i} must be a dict")
                    continue
                cred_required = ["name", "display_name", "type", "required"]
                for field in cred_required:
                    if field not in cred:
                        errors.append(f"Credential {i} missing field: {field}")
        
        return len(errors) == 0, errors
    
    @staticmethod
    def validate_strategy_plugin(config: Dict[str, Any]) -> Tuple[bool, List[str]]:
        """验证策略插件配置"""
        errors = []
        required_fields = [
            "name", "display_name", "strategy_class", "version",
            "supported_platforms", "default_params", "param_schema"
        ]
        
        for field in required_fields:
            if field not in config:
                errors.append(f"Missing required field: {field}")
        
        # 验证param_schema结构
        if "param_schema" in config:
            schema = config["param_schema"]
            if not isinstance(schema, dict) or "type" not in schema:
                errors.append("param_schema must be a valid JSON schema object")
        
        return len(errors) == 0, errors

class PluginLoader:
    """
    插件加载器
    
    功能：
    1. 扫描插件目录，加载JSON配置
    2. 验证插件配置格式
    3. 动态导入Python类
    4. 缓存插件信息
    5. 支持热重载（可选）
    """
    
    def __init__(self, core_path: Optional[str] = None):
        if core_path is None:
            # 自动检测core路径
            current_file = Path(__file__).resolve()
            self.core_path = current_file.parent.parent
        else:
            self.core_path = Path(core_path)
        
        self.platform_plugins_path = self.core_path / "platform" / "plugins"
        self.strategy_plugins_path = self.core_path / "strategy" / "plugins"
        
        # 插件缓存
        self._platform_plugins: Dict[str, Dict[str, Any]] = {}
        self._strategy_plugins: Dict[str, Dict[str, Any]] = {}
        
        # 类缓存
        self._platform_classes: Dict[str, Type] = {}
        self._strategy_classes: Dict[str, Type] = {}
        
        # 插件文件修改时间缓存（用于热重载检测）
        self._file_mtimes: Dict[str, float] = {}
    
    def scan_platform_plugins(self, force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        扫描并加载平台插件
        
        Args:
            force_reload: 是否强制重新加载
            
        Returns:
            Dict[plugin_name, plugin_config]
        """
        if not force_reload and self._platform_plugins:
            return self._platform_plugins.copy()
        
        self._platform_plugins.clear()
        
        if not self.platform_plugins_path.exists():
            logger.log_warning(f"Platform plugins directory not found: {self.platform_plugins_path}")
            return {}
        
        for plugin_file in self.platform_plugins_path.glob("*.json"):
            try:
                config = self._load_plugin_config(plugin_file)
                if not config:
                    continue
                
                # 验证配置
                valid, errors = PluginValidator.validate_platform_plugin(config)
                if not valid:
                    logger.log_error(f"Invalid platform plugin {plugin_file.name}: {errors}")
                    continue
                
                plugin_name = config["name"]
                self._platform_plugins[plugin_name] = config
                logger.log_info(f"✅ Loaded platform plugin: {plugin_name}")
                
            except Exception as e:
                logger.log_error(f"Failed to load platform plugin {plugin_file.name}: {e}")
        
        return self._platform_plugins.copy()
    
    def scan_strategy_plugins(self, force_reload: bool = False) -> Dict[str, Dict[str, Any]]:
        """
        扫描并加载策略插件
        
        Args:
            force_reload: 是否强制重新加载
            
        Returns:
            Dict[plugin_name, plugin_config]
        """
        if not force_reload and self._strategy_plugins:
            return self._strategy_plugins.copy()
        
        self._strategy_plugins.clear()
        
        if not self.strategy_plugins_path.exists():
            logger.log_warning(f"Strategy plugins directory not found: {self.strategy_plugins_path}")
            return {}
        
        for plugin_file in self.strategy_plugins_path.glob("*.json"):
            try:
                config = self._load_plugin_config(plugin_file)
                if not config:
                    continue
                
                # 验证配置
                valid, errors = PluginValidator.validate_strategy_plugin(config)
                if not valid:
                    logger.log_error(f"Invalid strategy plugin {plugin_file.name}: {errors}")
                    continue
                
                plugin_name = config["name"]
                self._strategy_plugins[plugin_name] = config
                logger.log_info(f"✅ Loaded strategy plugin: {plugin_name}")
                
            except Exception as e:
                logger.log_error(f"Failed to load strategy plugin {plugin_file.name}: {e}")
        
        return self._strategy_plugins.copy()
    
    def get_platform_class(self, platform_name: str) -> Optional[Type]:
        """
        获取平台适配器类
        
        Args:
            platform_name: 平台名称
            
        Returns:
            平台适配器类或None
        """
        if platform_name in self._platform_classes:
            return self._platform_classes[platform_name]
        
        # 确保插件已加载
        if platform_name not in self._platform_plugins:
            self.scan_platform_plugins()
        
        if platform_name not in self._platform_plugins:
            logger.log_error(f"Platform plugin not found: {platform_name}")
            return None
        
        plugin_config = self._platform_plugins[platform_name]
        class_path = plugin_config["adapter_class"]
        
        try:
            cls = self._import_class(class_path)
            self._platform_classes[platform_name] = cls
            return cls
        except Exception as e:
            logger.log_error(f"Failed to import platform class {class_path}: {e}")
            return None
    
    def get_strategy_class(self, strategy_name: str) -> Optional[Type]:
        """
        获取策略类
        
        Args:
            strategy_name: 策略名称
            
        Returns:
            策略类或None
        """
        if strategy_name in self._strategy_classes:
            return self._strategy_classes[strategy_name]
        
        # 确保插件已加载
        if strategy_name not in self._strategy_plugins:
            self.scan_strategy_plugins()
        
        if strategy_name not in self._strategy_plugins:
            logger.log_error(f"Strategy plugin not found: {strategy_name}")
            return None
        
        plugin_config = self._strategy_plugins[strategy_name]
        class_path = plugin_config["strategy_class"]
        
        try:
            cls = self._import_class(class_path)
            self._strategy_classes[strategy_name] = cls
            return cls
        except Exception as e:
            logger.log_error(f"Failed to import strategy class {class_path}: {e}")
            return None
    
    def get_platform_config(self, platform_name: str) -> Optional[Dict[str, Any]]:
        """获取平台插件配置"""
        if platform_name not in self._platform_plugins:
            self.scan_platform_plugins()
        return self._platform_plugins.get(platform_name)
    
    def get_strategy_config(self, strategy_name: str) -> Optional[Dict[str, Any]]:
        """获取策略插件配置"""
        if strategy_name not in self._strategy_plugins:
            self.scan_strategy_plugins()
        return self._strategy_plugins.get(strategy_name)
    
    def list_available_platforms(self) -> List[str]:
        """列出所有可用平台"""
        self.scan_platform_plugins()
        return list(self._platform_plugins.keys())
    
    def list_available_strategies(self) -> List[str]:
        """列出所有可用策略"""
        self.scan_strategy_plugins()
        return list(self._strategy_plugins.keys())
    
    def reload_plugins(self):
        """重新加载所有插件"""
        logger.log_info("🔄 Reloading all plugins...")
        self._platform_plugins.clear()
        self._strategy_plugins.clear()
        self._platform_classes.clear()
        self._strategy_classes.clear()
        self._file_mtimes.clear()
        
        self.scan_platform_plugins()
        self.scan_strategy_plugins()
        logger.log_info("✅ Plugin reload completed")
    
    def check_plugin_updates(self) -> bool:
        """检查插件文件是否有更新（用于热重载）"""
        has_updates = False
        
        # 检查平台插件文件
        if self.platform_plugins_path.exists():
            for plugin_file in self.platform_plugins_path.glob("*.json"):
                file_path = str(plugin_file)
                current_mtime = plugin_file.stat().st_mtime
                cached_mtime = self._file_mtimes.get(file_path, 0)
                
                if current_mtime > cached_mtime:
                    has_updates = True
                    self._file_mtimes[file_path] = current_mtime
        
        # 检查策略插件文件
        if self.strategy_plugins_path.exists():
            for plugin_file in self.strategy_plugins_path.glob("*.json"):
                file_path = str(plugin_file)
                current_mtime = plugin_file.stat().st_mtime
                cached_mtime = self._file_mtimes.get(file_path, 0)
                
                if current_mtime > cached_mtime:
                    has_updates = True
                    self._file_mtimes[file_path] = current_mtime
        
        return has_updates
    
    def _load_plugin_config(self, plugin_file: Path) -> Optional[Dict[str, Any]]:
        """加载插件配置文件"""
        try:
            with open(plugin_file, 'r', encoding='utf-8') as f:
                config = json.load(f)
            
            # 记录文件修改时间
            self._file_mtimes[str(plugin_file)] = plugin_file.stat().st_mtime
            
            return config
        except json.JSONDecodeError as e:
            logger.log_error(f"Invalid JSON in {plugin_file.name}: {e}")
            return None
        except Exception as e:
            logger.log_error(f"Failed to load plugin config {plugin_file.name}: {e}")
            return None
    
    def _import_class(self, class_path: str) -> Type:
        """
        动态导入类
        
        Args:
            class_path: 类路径，格式为 "module.path:ClassName"
            
        Returns:
            导入的类
        """
        try:
            module_path, class_name = class_path.split(":")
            module = importlib.import_module(module_path)
            cls = getattr(module, class_name)
            return cls
        except ValueError:
            raise PluginLoadError(f"Invalid class path format: {class_path}. Expected 'module.path:ClassName'")
        except ModuleNotFoundError:
            raise PluginLoadError(f"Module not found: {module_path}")
        except AttributeError:
            raise PluginLoadError(f"Class not found: {class_name} in {module_path}")

# 全局插件加载器实例
_plugin_loader = None

def get_plugin_loader() -> PluginLoader:
    """获取全局插件加载器实例"""
    global _plugin_loader
    if _plugin_loader is None:
        _plugin_loader = PluginLoader()
    return _plugin_loader