"""能耗追踪器 - 处理插座能耗历史数据"""
import logging
from datetime import datetime, timedelta
from typing import Dict, Optional
from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "changsui_pdu_energy"


class EnergyTracker:
    """能耗追踪器，负责存储和计算插座能耗数据"""

    def __init__(self, hass: HomeAssistant, entry_id: str):
        """初始化能耗追踪器
        
        Args:
            hass: Home Assistant 实例
            entry_id: 配置条目 ID
        """
        self.hass = hass
        self.entry_id = entry_id
        self._store = Store(hass, STORAGE_VERSION, f"{STORAGE_KEY}_{entry_id}")
        self._data: Dict = {}
        self._last_midnight_check = None

    async def async_load(self):
        """加载存储的能耗数据"""
        data = await self._store.async_load()
        if data:
            self._data = data
            _LOGGER.debug("加载能耗数据: %s", self._data)
        else:
            self._data = {
                "snapshots": {},  # {outlet_id: {date: energy_value}}
                "today_start": {},  # {outlet_id: energy_value}
                "yesterday_start": {},  # {outlet_id: energy_value}
                "yesterday_end": {},  # {outlet_id: energy_value}
            }

    async def async_save(self):
        """保存能耗数据"""
        await self._store.async_save(self._data)

    async def update(self, outlet_energies: list):
        """更新能耗数据并检查是否需要保存快照
        
        Args:
            outlet_energies: 插座能耗列表 [{"name": "Outlet1", "energy": 6.08}, ...]
        """
        now = datetime.now()
        today = now.date().isoformat()
        
        # 检查是否跨越了午夜
        await self._check_midnight(now)
        
        # 更新每个插座的数据
        for idx, outlet in enumerate(outlet_energies, start=1):
            outlet_id = f"outlet_{idx}"
            energy = outlet.get("energy", 0)
            
            # 如果是今天第一次更新，记录今日起始值
            if outlet_id not in self._data["today_start"]:
                self._data["today_start"][outlet_id] = energy
                _LOGGER.debug(f"记录 {outlet_id} 今日起始能耗: {energy} kWh")
            
            # 保存快照
            if outlet_id not in self._data["snapshots"]:
                self._data["snapshots"][outlet_id] = {}
            self._data["snapshots"][outlet_id][today] = energy

    async def _check_midnight(self, now: datetime):
        """检查是否跨越午夜，如果是则保存昨日数据"""
        current_date = now.date()
        
        # 如果是首次检查或日期发生变化
        if self._last_midnight_check is None or self._last_midnight_check != current_date:
            if self._last_midnight_check is not None:
                # 跨越了午夜
                _LOGGER.info("检测到日期变化，保存昨日能耗数据")
                
                # 将今日起始值保存为昨日结束值
                self._data["yesterday_end"] = self._data["today_start"].copy()
                
                # 获取昨日起始值（如果有的话）
                yesterday = (current_date - timedelta(days=1)).isoformat()
                for outlet_id in self._data["snapshots"]:
                    if yesterday in self._data["snapshots"][outlet_id]:
                        if "yesterday_start" not in self._data:
                            self._data["yesterday_start"] = {}
                        # 查找昨日最早的快照作为起始值
                        self._data["yesterday_start"][outlet_id] = self._data["snapshots"][outlet_id][yesterday]
                
                # 清空今日起始值，等待新的一天的第一次更新
                self._data["today_start"] = {}
                
                # 保存数据
                await self.async_save()
            
            self._last_midnight_check = current_date

    def get_today_usage(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的今日用电量
        
        Args:
            outlet_idx: 插座编号 (1-based)
            
        Returns:
            今日用电量 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        today = datetime.now().date().isoformat()
        
        # 获取今日起始值
        today_start = self._data["today_start"].get(outlet_id)
        
        # 获取当前值（最新快照）
        if outlet_id in self._data["snapshots"] and today in self._data["snapshots"][outlet_id]:
            current = self._data["snapshots"][outlet_id][today]
            
            if today_start is not None:
                usage = current - today_start
                return max(0, usage)  # 确保不为负数
        
        return None

    def get_yesterday_usage(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的昨日用电量
        
        Args:
            outlet_idx: 插座编号 (1-based)
            
        Returns:
            昨日用电量 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        
        yesterday_start = self._data.get("yesterday_start", {}).get(outlet_id)
        yesterday_end = self._data.get("yesterday_end", {}).get(outlet_id)
        
        if yesterday_start is not None and yesterday_end is not None:
            usage = yesterday_end - yesterday_start
            return max(0, usage)  # 确保不为负数
        
        return None

    def get_total_energy(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的总能耗
        
        Args:
            outlet_idx: 插座编号 (1-based)
            
        Returns:
            总能耗 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        today = datetime.now().date().isoformat()
        
        if outlet_id in self._data["snapshots"] and today in self._data["snapshots"][outlet_id]:
            return self._data["snapshots"][outlet_id][today]
        
        return None
