"""PDU 设备注册管理器"""
import os
import json
import logging
from typing import Dict, Optional, Any
from datetime import datetime

from homeassistant.core import HomeAssistant

from .const import (
    CONF_IDENTIFIERS,
    CONF_MANUFACTURER,
    CONF_MODEL,
    CONF_NAME,
    CONF_SW_VERSION,
    CONF_CONFIGURATION_URL,
    CONF_NUM_SWITCHES,
    DEFAULT_MANUFACTURER,
    DEFAULT_MODEL,
    DEFAULT_SW_VERSION,
    DEFAULT_NUM_SWITCHES,
)

_LOGGER = logging.getLogger(__name__)


class DeviceRegistry:
    """管理 PDU 设备注册信息"""

    def __init__(self, hass: HomeAssistant, data_dir: str):
        """初始化设备注册表
        
        Args:
            hass: Home Assistant 实例
            data_dir: 数据存储目录
        """
        self.hass = hass
        self.data_dir = data_dir
        self.devices_file = os.path.join(data_dir, "devices.json")
        self.devices: Dict[str, Dict[str, Any]] = {}

    async def async_load_devices(self):
        """从文件加载设备配置"""
        if os.path.exists(self.devices_file):
            try:
                def _load():
                    with open(self.devices_file, "r", encoding="utf-8") as f:
                        return json.load(f)
                
                self.devices = await self.hass.async_add_executor_job(_load)
                _LOGGER.info(f"已加载 {len(self.devices)} 个 PDU 设备配置")
            except Exception as e:
                _LOGGER.error(f"加载设备配置失败: {e}")
                self.devices = {}
        else:
            self.devices = {}

    async def _async_save_devices(self):
        """保存设备配置到文件"""
        try:
            def _save():
                os.makedirs(self.data_dir, exist_ok=True)
                with open(self.devices_file, "w", encoding="utf-8") as f:
                    json.dump(self.devices, f, indent=2, ensure_ascii=False)
            
            await self.hass.async_add_executor_job(_save)
            _LOGGER.debug("设备配置已保存")
        except Exception as e:
            _LOGGER.error(f"保存设备配置失败: {e}")

    async def async_register_device(self, pdu_id: str, auto_create: bool = True) -> bool:
        """注册新设备
        
        Args:
            pdu_id: PDU 设备 ID
            auto_create: 是否自动创建默认配置
            
        Returns:
            是否成功注册
        """
        if pdu_id in self.devices:
            # 更新连接状态和最后在线时间
            self.devices[pdu_id]["connected"] = True
            self.devices[pdu_id]["last_seen"] = datetime.now().isoformat()
            await self._async_save_devices()
            _LOGGER.info(f"PDU {pdu_id} 重新连接")
            return True

        if auto_create:
            # 创建默认配置
            self.devices[pdu_id] = self._create_default_config(pdu_id)
            await self._async_save_devices()
            _LOGGER.info(f"PDU {pdu_id} 已自动注册")
            return True

        return False

    def _create_default_config(self, pdu_id: str) -> Dict[str, Any]:
        """创建默认设备配置
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            默认配置字典
        """
        return {
            CONF_IDENTIFIERS: pdu_id,
            CONF_MANUFACTURER: DEFAULT_MANUFACTURER,
            CONF_MODEL: DEFAULT_MODEL,
            CONF_NAME: f"PDU {pdu_id}",
            CONF_SW_VERSION: DEFAULT_SW_VERSION,
            CONF_CONFIGURATION_URL: None,
            "connected": True,
            "last_seen": datetime.now().isoformat(),
            CONF_NUM_SWITCHES: DEFAULT_NUM_SWITCHES,
        }

    async def async_update_device(self, pdu_id: str, config: Dict[str, Any]) -> bool:
        """更新设备配置
        
        Args:
            pdu_id: PDU 设备 ID
            config: 新的配置信息
            
        Returns:
            是否成功更新
        """
        if pdu_id not in self.devices:
            _LOGGER.warning(f"尝试更新不存在的设备: {pdu_id}")
            return False

        # 更新配置,保留连接状态
        self.devices[pdu_id].update(config)
        await self._async_save_devices()
        _LOGGER.info(f"PDU {pdu_id} 配置已更新")
        return True

    def get_device(self, pdu_id: str) -> Optional[Dict[str, Any]]:
        """获取设备配置
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            设备配置字典,如果不存在则返回 None
        """
        return self.devices.get(pdu_id)

    def get_all_devices(self) -> Dict[str, Dict[str, Any]]:
        """获取所有设备配置
        
        Returns:
            所有设备配置字典
        """
        return self.devices.copy()

    async def async_set_device_connected(self, pdu_id: str, connected: bool):
        """设置设备连接状态
        
        Args:
            pdu_id: PDU 设备 ID
            connected: 是否已连接
        """
        if pdu_id in self.devices:
            self.devices[pdu_id]["connected"] = connected
            if connected:
                self.devices[pdu_id]["last_seen"] = datetime.now().isoformat()
            await self._async_save_devices()

    def is_device_registered(self, pdu_id: str) -> bool:
        """检查设备是否已注册
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            是否已注册
        """
        return pdu_id in self.devices

    async def async_remove_device(self, pdu_id: str) -> bool:
        """移除设备
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            是否成功移除
        """
        if pdu_id in self.devices:
            del self.devices[pdu_id]
            await self._async_save_devices()
            _LOGGER.info(f"PDU {pdu_id} 已移除")
            return True
        return False
