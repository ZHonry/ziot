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

    # ❗ 取出本 entry 对应的数据
    entry_data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PduCoordinator = entry_data[DATA_COORDINATOR]
    device_registry: DeviceRegistry = entry_data[DATA_DEVICE_REGISTRY]

    # 为所有已注册的设备创建传感器实体
    entities = []
    for pdu_id, device_config in device_registry.get_all_devices().items():
        available_sensors = coordinator.get_available_sensors(pdu_id)
        for sensor_type in available_sensors:
            if sensor_type in SENSOR_CONFIGS or sensor_type.startswith("current_"):
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

    # 保存回调供 coordinator 动态添加使用
    if DOMAIN not in hass.data:
        hass.data[DOMAIN] = {}
    if entry.entry_id not in hass.data[DOMAIN]:
        hass.data[DOMAIN][entry.entry_id] = {}
    hass.data[DOMAIN][entry.entry_id]["add_sensor_entities"] = async_add_entities




class PduSensor(CoordinatorEntity, SensorEntity):
    """PDU 传感器实体"""

    def __init__(
        self,
        coordinator: PduCoordinator,
        device_registry: DeviceRegistry, # 确保参数在这里
        pdu_id: str,
        sensor_type: str,
    ):
        """初始化传感器实体"""
        super().__init__(coordinator)
        # --- 关键修复：确保这两行存在且赋值正确 ---
        self._device_registry = device_registry 
        self._pdu_id = pdu_id
        # ------------------------------------
        
        self._sensor_type = sensor_type
        self._attr_has_entity_name = True

        # 下面是你提供的逻辑：处理分口电流名称
        config = SENSOR_CONFIGS.get(sensor_type)
        if not config and sensor_type.startswith("current_"):
            config = SENSOR_CONFIGS.get(SENSOR_TYPE_CURRENT, {})
            try:
                idx = int(sensor_type.split("_")[1])
                self._sensor_name = f"插座 {idx} 电流"
            except Exception:
                self._sensor_name = "插座 电流"
        else:
            self._sensor_name = config.get("name", sensor_type) if config else sensor_type

        # 设置属性
        self._attr_device_class = config.get("device_class") if config else None
        self._attr_native_unit_of_measurement = config.get("unit") if config else None
        self._attr_state_class = config.get("state_class") if config else None
        self._attr_icon = config.get("icon") if config else None
        
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
