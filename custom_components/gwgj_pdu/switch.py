"""PDU Switch 平台"""
import logging
from typing import Any, Optional

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    DATA_COORDINATOR,
    DATA_DEVICE_REGISTRY,
    DATA_SERVER,
    CONF_NUM_SWITCHES,
)
from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置 PDU Switch 平台"""
    """设置 PDU Switch 平台"""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PduCoordinator = data[DATA_COORDINATOR]
    device_registry: DeviceRegistry = data[DATA_DEVICE_REGISTRY]
    server = data[DATA_SERVER]

    # 为所有已注册的设备创建开关实体
    entities = []
    for pdu_id, device_config in device_registry.get_all_devices().items():
        num_switches = device_config.get(CONF_NUM_SWITCHES, 8)
        for i in range(1, num_switches + 1):
            entities.append(
                PduSwitch(
                    coordinator=coordinator,
                    device_registry=device_registry,
                    server=server,
                    pdu_id=pdu_id,
                    switch_number=i,
                )
            )

    if entities:
        async_add_entities(entities)
        _LOGGER.info(f"已添加 {len(entities)} 个 PDU 开关实体")

    # 存储添加实体的回调,用于动态添加新设备
    # 存储添加实体的回调,用于动态添加新设备
    hass.data[DOMAIN][entry.entry_id]["add_switch_entities"] = async_add_entities


class PduSwitch(CoordinatorEntity, SwitchEntity):
    """PDU 开关实体"""

    def __init__(
        self,
        coordinator: PduCoordinator,
        device_registry: DeviceRegistry,
        server,
        pdu_id: str,
        switch_number: int,
    ):
        """初始化开关实体
        
        Args:
            coordinator: 数据协调器
            device_registry: 设备注册表
            server: PDU Server 实例
            pdu_id: PDU 设备 ID
            switch_number: 开关编号(1-based)
        """
        super().__init__(coordinator)
        self._device_registry = device_registry
        self._server = server
        self._pdu_id = pdu_id
        self._switch_number = switch_number
        self._attr_has_entity_name = True

    @property
    def unique_id(self) -> str:
        """返回唯一 ID"""
        return f"{self._pdu_id}_switch_{self._switch_number}"

    @property
    def name(self) -> str:
        """返回实体名称"""
        return f"开关 {self._switch_number}"

    @property
    def device_info(self):
        """返回设备信息"""
        device_config = self._device_registry.get_device(self._pdu_id)
        if not device_config:
            return None

        return {
            "identifiers": {(DOMAIN, self._pdu_id)},
            "name": device_config.get("name", f"PDU {self._pdu_id}"),
            "manufacturer": device_config.get("manufacturer", "Generic"),
            "model": device_config.get("model", "PDU"),
            "sw_version": device_config.get("sw_version", "1.0.0"),
            "configuration_url": device_config.get("configuration_url"),
        }

    @property
    def is_on(self) -> Optional[bool]:
        """返回开关状态"""
        state = self.coordinator.get_switch_state(self._pdu_id, self._switch_number)
        if state is None:
            return None
        return state == "on"

    @property
    def available(self) -> bool:
        """返回实体是否可用"""
        device_config = self._device_registry.get_device(self._pdu_id)
        if not device_config:
            return False
        return device_config.get("connected", False)

    async def async_turn_on(self, **kwargs: Any) -> None:
        """打开开关"""
        io_value = 2 ** (self._switch_number - 1)
        await self._server.send_control_command(self._pdu_id, "open", io_value)
        
        # 乐观更新状态
        self.coordinator.update_switch_state(
            self._pdu_id, self._switch_number, "on", debounce_sec=0
        )

    async def async_turn_off(self, **kwargs: Any) -> None:
        """关闭开关"""
        io_value = 2 ** (self._switch_number - 1)
        await self._server.send_control_command(self._pdu_id, "close", io_value)
        
        # 乐观更新状态
        self.coordinator.update_switch_state(
            self._pdu_id, self._switch_number, "off", debounce_sec=0
        )

    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器更新"""
        self.async_write_ha_state()
