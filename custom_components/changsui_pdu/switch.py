"""PDU Switch 平台 - 优化版本"""
import logging
from homeassistant.components.switch import SwitchEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.exceptions import HomeAssistantError

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """设置 PDU Switch 平台"""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]
    outlets = data["outlets"]
    pdu_name = entry.data.get("pdu_name", "Changsui PDU")
    show_current_limits = entry.data.get("show_current_limits", False)
    energy_tracker = data.get("energy_tracker")  # 获取能耗追踪器

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=entry.data.get("manufacturer", "昌遂"),
        model=entry.data.get("model", "CAN"),
        name=pdu_name,
        sw_version=entry.data.get("sw_version", "V6.0.80"),
        configuration_url=f"http://{client.host}",
    )

    entities = [
        ChangsuiPDUSwitch(coordinator, client, device_info, i + 1, show_current_limits, energy_tracker)
        for i in range(outlets)
    ]
    async_add_entities(entities)


class ChangsuiPDUSwitch(CoordinatorEntity, SwitchEntity):
    """PDU 插座开关实体"""

    def __init__(self, coordinator, client, device_info, idx, show_current_limits=False, energy_tracker=None):
        """初始化开关实体
        
        Args:
            coordinator: 数据协调器
            client: PDU 客户端
            device_info: 设备信息
            idx: 插座编号 (1-based)
            show_current_limits: 是否显示负载上下限
            energy_tracker: 能耗追踪器（可选）
        """
        super().__init__(coordinator)
        self.client = client
        self.idx = idx
        self.show_current_limits = show_current_limits
        self.energy_tracker = energy_tracker
        self._attr_name = f"Outlet {idx}"
        self._attr_unique_id = f"{client.host}_outlet{idx}"
        self._attr_device_info = device_info
        self._attr_has_entity_name = True

    @property
    def is_on(self):
        """返回开关状态"""
        outlets = self.coordinator.data.get("outlets", [])
        if self.idx <= len(outlets):
            return outlets[self.idx - 1]["state"] == 1
        return None

    @property
    def available(self):
        """返回实体是否可用"""
        return self.coordinator.last_update_success and self.coordinator.data.get(
            "outlets"
        )

    @property
    def extra_state_attributes(self):
        """返回额外的状态属性"""
        outlets = self.coordinator.data.get("outlets", [])
        if self.idx <= len(outlets):
            outlet_data = outlets[self.idx - 1]
            attrs = {
                "插座名称": outlet_data.get("name", f"Outlet {self.idx}"),
                "电流": outlet_data.get("current", 0),
                "功率": outlet_data.get("power", 0),
            }
            
            # 根据配置决定是否显示负载上下限
            if self.show_current_limits:
                attrs["电流下限"] = outlet_data.get("current_min", 0)
                attrs["电流上限"] = outlet_data.get("current_max", 0)
            
            # 如果有能耗追踪器，添加能耗数据
            if self.energy_tracker:
                total_energy = self.energy_tracker.get_total_energy(self.idx)
                today_usage = self.energy_tracker.get_today_usage(self.idx)
                yesterday_usage = self.energy_tracker.get_yesterday_usage(self.idx)
                
                if total_energy is not None:
                    attrs["总能耗"] = round(total_energy, 2)
                if today_usage is not None:
                    attrs["今日用电"] = round(today_usage, 2)
                if yesterday_usage is not None:
                    attrs["昨日用电"] = round(yesterday_usage, 2)
            
            return attrs
        return {}

    async def async_turn_on(self, **kwargs):
        """打开插座"""
        success = await self.client.set_outlet_state(self.idx, 1)

        if success:
            _LOGGER.info("插座 %s 已打开", self.idx)
            # 请求刷新以获取最新状态
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("打开插座 %s 失败", self.idx)
            raise HomeAssistantError(f"无法打开插座 {self.idx}")

    async def async_turn_off(self, **kwargs):
        """关闭插座"""
        success = await self.client.set_outlet_state(self.idx, 0)

        if success:
            _LOGGER.info("插座 %s 已关闭", self.idx)
            # 请求刷新以获取最新状态
            await self.coordinator.async_request_refresh()
        else:
            _LOGGER.error("关闭插座 %s 失败", self.idx)
            raise HomeAssistantError(f"无法关闭插座 {self.idx}")
