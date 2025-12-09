"""PDU Sensor 平台"""
import logging
from typing import Optional

from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    UnitOfPower,
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    DOMAIN,
    DATA_COORDINATOR,
    DATA_DEVICE_REGISTRY,
    SENSOR_TYPE_POWER,
    SENSOR_TYPE_CURRENT,
    SENSOR_TYPE_VOLTAGE,
    SENSOR_TYPE_TEMPERATURE,
)
from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry

_LOGGER = logging.getLogger(__name__)

# 传感器配置映射
SENSOR_CONFIGS = {
    SENSOR_TYPE_POWER: {
        "name": "功率",
        "device_class": SensorDeviceClass.POWER,
        "unit": UnitOfPower.WATT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:flash",
    },
    SENSOR_TYPE_CURRENT: {
        "name": "电流",
        "device_class": SensorDeviceClass.CURRENT,
        "unit": UnitOfElectricCurrent.AMPERE,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:current-ac",
    },
    SENSOR_TYPE_VOLTAGE: {
        "name": "电压",
        "device_class": SensorDeviceClass.VOLTAGE,
        "unit": UnitOfElectricPotential.VOLT,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:lightning-bolt",
    },
    SENSOR_TYPE_TEMPERATURE: {
        "name": "温度",
        "device_class": SensorDeviceClass.TEMPERATURE,
        "unit": UnitOfTemperature.CELSIUS,
        "state_class": SensorStateClass.MEASUREMENT,
        "icon": "mdi:thermometer",
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置 PDU Sensor 平台"""
    coordinator: PduCoordinator = hass.data[DOMAIN][DATA_COORDINATOR]
    device_registry: DeviceRegistry = hass.data[DOMAIN][DATA_DEVICE_REGISTRY]

    # 为所有已注册的设备创建传感器实体
    # 注意:初始时可能没有传感器数据,实体会在接收到数据后动态创建
    entities = []
    for pdu_id, device_config in device_registry.get_all_devices().items():
        # 获取已知的传感器类型
        available_sensors = coordinator.get_available_sensors(pdu_id)
        for sensor_type in available_sensors:
            if sensor_type in SENSOR_CONFIGS:
                entities.append(
                    PduSensor(
                        coordinator=coordinator,
                        device_registry=device_registry,
                        pdu_id=pdu_id,
                        sensor_type=sensor_type,
                    )
                )

    if entities:
        async_add_entities(entities)
        _LOGGER.info(f"已添加 {len(entities)} 个 PDU 传感器实体")

    # 存储添加实体的回调,用于动态添加新传感器
    hass.data[DOMAIN]["add_sensor_entities"] = async_add_entities


class PduSensor(CoordinatorEntity, SensorEntity):
    """PDU 传感器实体"""

    def __init__(
        self,
        coordinator: PduCoordinator,
        device_registry: DeviceRegistry,
        pdu_id: str,
        sensor_type: str,
    ):
        """初始化传感器实体
        
        Args:
            coordinator: 数据协调器
            device_registry: 设备注册表
            pdu_id: PDU 设备 ID
            sensor_type: 传感器类型
        """
        super().__init__(coordinator)
        self._device_registry = device_registry
        self._pdu_id = pdu_id
        self._sensor_type = sensor_type
        self._attr_has_entity_name = True

        # 从配置中获取传感器属性
        config = SENSOR_CONFIGS.get(sensor_type, {})
        self._attr_device_class = config.get("device_class")
        self._attr_native_unit_of_measurement = config.get("unit")
        self._attr_state_class = config.get("state_class")
        self._attr_icon = config.get("icon")
        self._sensor_name = config.get("name", sensor_type)

    @property
    def unique_id(self) -> str:
        """返回唯一 ID"""
        return f"{self._pdu_id}_sensor_{self._sensor_type}"

    @property
    def name(self) -> str:
        """返回实体名称"""
        return self._sensor_name

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
    def native_value(self) -> Optional[float]:
        """返回传感器值"""
        return self.coordinator.get_sensor_value(self._pdu_id, self._sensor_type)

    @property
    def available(self) -> bool:
        """返回实体是否可用"""
        device_config = self._device_registry.get_device(self._pdu_id)
        if not device_config:
            return False
        return device_config.get("connected", False)

    @callback
    def _handle_coordinator_update(self) -> None:
        """处理协调器更新"""
        self.async_write_ha_state()
