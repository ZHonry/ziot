"""PDU 数据协调器"""
import logging
from typing import Dict, Any, Optional
from datetime import timedelta
import time

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


class PduCoordinator(DataUpdateCoordinator):
    """PDU 数据协调器,管理所有 PDU 的状态数据"""

    def __init__(self, hass: HomeAssistant):
        """初始化协调器
        
        Args:
            hass: Home Assistant 实例
        """
        super().__init__(
            hass,
            _LOGGER,
            name="PDU Coordinator",
            update_interval=timedelta(seconds=30),  # 定期更新间隔(用于检查超时等)
        )
        # 存储每个 PDU 的状态数据
        # 格式: {pdu_id: {switch_1: "on", switch_2: "off", ..., power: 100, ...}}
        self.data: Dict[str, Dict[str, Any]] = {}
        
        # 防抖缓存
        self._last_state: Dict[tuple, tuple] = {}  # (pdu_id, entity_id) -> (state, timestamp)

    async def _async_update_data(self):
        """定期更新数据(可选实现)"""
        # 这里可以实现定期检查连接状态等逻辑
        # 主要的状态更新通过 update_switch_state 等方法直接推送
        return self.data

    def get_pdu_data(self, pdu_id: str) -> Optional[Dict[str, Any]]:
        """获取指定 PDU 的数据
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            PDU 数据字典,如果不存在则返回 None
        """
        return self.data.get(pdu_id)

    def init_pdu(self, pdu_id: str, num_switches: int = 8):
        """初始化 PDU 数据结构
        
        Args:
            pdu_id: PDU 设备 ID
            num_switches: 开关数量
        """
        if pdu_id not in self.data:
            self.data[pdu_id] = {}
            # 初始化开关状态
            for i in range(1, num_switches + 1):
                self.data[pdu_id][f"switch_{i}"] = "off"
            # 记录可用的传感器类型(根据实际接收到的数据动态添加)
            self.data[pdu_id]["_available_sensors"] = set()
            _LOGGER.debug(f"初始化 PDU {pdu_id} 数据结构")

    def remove_pdu(self, pdu_id: str):
        """移除 PDU 数据
        
        Args:
            pdu_id: PDU 设备 ID
        """
        if pdu_id in self.data:
            del self.data[pdu_id]
            _LOGGER.debug(f"移除 PDU {pdu_id} 数据")

    def update_switch_state(
        self, 
        pdu_id: str, 
        switch_number: int, 
        state: str,
        debounce_sec: float = 0.5
    ) -> bool:
        """更新开关状态(带防抖)
        
        Args:
            pdu_id: PDU 设备 ID
            switch_number: 开关编号(1-based)
            state: 状态 "on" 或 "off"
            debounce_sec: 防抖时间(秒)
            
        Returns:
            是否更新成功(如果被防抖则返回 False)
        """
        if pdu_id not in self.data:
            self.init_pdu(pdu_id)

        # 防抖检查
        key = (pdu_id, f"switch_{switch_number}")
        now = time.time()
        last = self._last_state.get(key)
        
        if last and last[0] == state and (now - last[1]) < debounce_sec:
            return False  # 防抖,忽略此次更新
        
        # 更新状态
        self._last_state[key] = (state, now)
        self.data[pdu_id][f"switch_{switch_number}"] = state
        
        # 通知订阅者
        self.async_set_updated_data(self.data)
        _LOGGER.debug(f"PDU {pdu_id} Switch {switch_number} -> {state}")
        return True

    def update_sensor_data(
        self,
        pdu_id: str,
        sensor_type: str,
        value: Any
    ):
        """更新传感器数据
        
        Args:
            pdu_id: PDU 设备 ID
            sensor_type: 传感器类型 (power, current, voltage, temperature)
            value: 传感器值
        """
        if pdu_id not in self.data:
            self.init_pdu(pdu_id)

        # 记录传感器类型(用于动态创建实体)
        if "_available_sensors" in self.data[pdu_id]:
            self.data[pdu_id]["_available_sensors"].add(sensor_type)

        self.data[pdu_id][sensor_type] = value
        
        # 通知订阅者
        self.async_set_updated_data(self.data)
        _LOGGER.debug(f"PDU {pdu_id} {sensor_type} -> {value}")

    def update_all_switches(self, pdu_id: str, io_value: int):
        """根据 IO 值更新所有开关状态
        
        Args:
            pdu_id: PDU 设备 ID
            io_value: IO 状态值(位掩码)
        """
        if pdu_id not in self.data:
            self.init_pdu(pdu_id)

        for i in range(1, 9):  # 假设最多 8 个开关
            state = "on" if (io_value & (1 << (i - 1))) else "off"
            key = f"switch_{i}"
            if key in self.data[pdu_id]:
                self.data[pdu_id][key] = state

        # 通知订阅者
        self.async_set_updated_data(self.data)
        _LOGGER.debug(f"PDU {pdu_id} 批量更新开关状态: {io_value}")

    def get_switch_state(self, pdu_id: str, switch_number: int) -> Optional[str]:
        """获取开关状态
        
        Args:
            pdu_id: PDU 设备 ID
            switch_number: 开关编号
            
        Returns:
            状态 "on" 或 "off",如果不存在则返回 None
        """
        if pdu_id in self.data:
            return self.data[pdu_id].get(f"switch_{switch_number}")
        return None

    def get_sensor_value(self, pdu_id: str, sensor_type: str) -> Optional[Any]:
        """获取传感器值
        
        Args:
            pdu_id: PDU 设备 ID
            sensor_type: 传感器类型
            
        Returns:
            传感器值,如果不存在则返回 None
        """
        if pdu_id in self.data:
            return self.data[pdu_id].get(sensor_type)
        return None

    def get_available_sensors(self, pdu_id: str) -> set:
        """获取 PDU 可用的传感器类型
        
        Args:
            pdu_id: PDU 设备 ID
            
        Returns:
            传感器类型集合
        """
        if pdu_id in self.data and "_available_sensors" in self.data[pdu_id]:
            return self.data[pdu_id]["_available_sensors"].copy()
        return set()
