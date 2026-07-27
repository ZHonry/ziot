"""能耗追踪器 - 基于设备累计能耗读数计算每插座今日/昨日用电

设备只上报"累计能耗"，本模块负责：
1. 记录每日 0 点基线（当天首次读数），今日用电 = 最新读数 - 基线
2. 跨天时结算昨日用电并固化保存
3. 数据定期持久化到 .storage，HA 白天重启不丢基线
4. 跨天重启（如夜间停机）也能正确结算/放弃过期数据

持久化结构:
    date:            当前基线对应的日期 (ISO)
    today_start:     {outlet_id: 今日基线读数}
    today_last:      {outlet_id: 今日最新读数}
    yesterday_usage: {outlet_id: 昨日用电量(已结算)}
"""
import logging
from datetime import date, timedelta
from typing import Dict, Optional

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

_LOGGER = logging.getLogger(__name__)

STORAGE_VERSION = 1
STORAGE_KEY = "changsui_pdu_energy"
SAVE_DELAY = 60  # 秒；合并写盘，避免每个轮询周期都写磁盘


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

    async def async_load(self):
        """加载存储的能耗数据（自动迁移旧格式）"""
        data = await self._store.async_load()

        if data and "snapshots" in data:
            data = self._migrate_legacy(data)
            _LOGGER.info("能耗数据已从旧格式迁移")

        self._data = data or {
            "date": None,
            "today_start": {},
            "today_last": {},
            "yesterday_usage": {},
        }
        _LOGGER.debug("加载能耗数据: %s", self._data)

    def _migrate_legacy(self, old: Dict) -> Dict:
        """迁移旧版数据结构（snapshots 全量快照 → 精简结构）"""
        today = date.today().isoformat()
        new = {
            "date": today,
            "today_start": dict(old.get("today_start") or {}),
            "today_last": {},
            "yesterday_usage": {},
        }

        # 旧快照中今天的读数 → today_last
        for outlet_id, per_day in (old.get("snapshots") or {}).items():
            if isinstance(per_day, dict) and today in per_day:
                new["today_last"][outlet_id] = per_day[today]

        # 旧的昨日起止值 → 直接结算为昨日用量
        ys = old.get("yesterday_start") or {}
        ye = old.get("yesterday_end") or {}
        for outlet_id, end_val in ye.items():
            start_val = ys.get(outlet_id)
            if start_val is not None:
                new["yesterday_usage"][outlet_id] = max(0, end_val - start_val)

        return new

    async def async_save(self):
        """立即保存能耗数据"""
        await self._store.async_save(self._data)

    def _schedule_save(self):
        """延迟合并保存（HA 正常停止时会自动落盘未保存数据）"""
        self._store.async_delay_save(lambda: self._data, SAVE_DELAY)

    async def update(self, outlet_energies: list):
        """更新能耗数据，必要时执行跨天结算

        Args:
            outlet_energies: 插座能耗列表 [{"name": "Outlet1", "energy": 6.08}, ...]
        """
        today = date.today().isoformat()

        if self._data.get("date") != today:
            self._rollover(today)

        for idx, outlet in enumerate(outlet_energies, start=1):
            outlet_id = f"outlet_{idx}"
            energy = outlet.get("energy", 0)

            # 今日首个读数作为基线（跨天结算后基线已预置为昨日末读数）
            self._data["today_start"].setdefault(outlet_id, energy)
            self._data["today_last"][outlet_id] = energy

        self._schedule_save()

    def _rollover(self, today: str):
        """跨天结算

        Args:
            today: 今天的 ISO 日期
        """
        stored_date = self._data.get("date")
        yesterday = (date.today() - timedelta(days=1)).isoformat()

        if stored_date == yesterday:
            # 正常跨天：昨日用量 = 昨日最后读数 - 昨日基线
            usage = {}
            for outlet_id, last in (self._data.get("today_last") or {}).items():
                start = (self._data.get("today_start") or {}).get(outlet_id)
                if start is not None:
                    usage[outlet_id] = max(0, last - start)
            self._data["yesterday_usage"] = usage

            # 今日基线预置为昨日末读数（近似 0 点值），
            # 这样 HA 停机期间凌晨的用电也会计入今日
            self._data["today_start"] = dict(self._data.get("today_last") or {})
            _LOGGER.info("跨天结算完成，昨日用电已固化: %s 个插座", len(usage))
        else:
            # 断档超过一天（长时间停机）：昨日数据不可信，全部重置
            self._data["yesterday_usage"] = {}
            self._data["today_start"] = {}
            if stored_date is not None:
                _LOGGER.warning(
                    "能耗数据断档（%s → %s），今日/昨日统计重新开始", stored_date, today
                )

        self._data["today_last"] = {}
        self._data["date"] = today

    def get_today_usage(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的今日用电量

        Args:
            outlet_idx: 插座编号 (1-based)

        Returns:
            今日用电量 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        start = self._data.get("today_start", {}).get(outlet_id)
        last = self._data.get("today_last", {}).get(outlet_id)

        if start is not None and last is not None:
            return max(0, round(last - start, 3))
        return None

    def get_yesterday_usage(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的昨日用电量

        Args:
            outlet_idx: 插座编号 (1-based)

        Returns:
            昨日用电量 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        usage = self._data.get("yesterday_usage", {}).get(outlet_id)
        if usage is not None:
            return round(usage, 3)
        return None

    def get_total_energy(self, outlet_idx: int) -> Optional[float]:
        """获取指定插座的累计总能耗（设备原始读数）

        Args:
            outlet_idx: 插座编号 (1-based)

        Returns:
            总能耗 (kWh)，如果数据不足则返回 None
        """
        outlet_id = f"outlet_{outlet_idx}"
        return self._data.get("today_last", {}).get(outlet_id)
