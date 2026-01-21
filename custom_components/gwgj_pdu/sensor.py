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

SENSOR_TYPE_ENERGY = "energy"

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
    SENSOR_TYPE_ENERGY: {
        "name": "电能",
        "device_class": SensorDeviceClass.ENERGY,
        "unit": "kWh", # UnitOfEnergy.KILO_WATT_HOUR might not be imported or available in older HA? safe to use string or import
        "state_class": SensorStateClass.TOTAL_INCREASING,
        "icon": "mdi:lightning-bolt-circle",
    },
}


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """设置 PDU Sensor 平台"""
    """设置 PDU Sensor 平台"""
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator: PduCoordinator = data[DATA_COORDINATOR]
    device_registry: DeviceRegistry = data[DATA_DEVICE_REGISTRY]

    # 为所有已注册的设备创建传感器实体
    # 注意:初始时可能没有传感器数据,实体会在接收到数据后动态创建
    # 获取 Server 实例以读取配置 (Server/Client)
    # Server 和 Client 实例都对外提供 fetch_outlet_current 属性 (Client 并没有, 需小心)
    from .const import DATA_SERVER
    server = data.get(DATA_SERVER)
    
    fetch_outlet = False
    if server:
        fetch_outlet = getattr(server, "fetch_outlet_current", False)

    # 为所有已注册的设备创建传感器实体
    entities = []
    for pdu_id, device_config in device_registry.get_all_devices().items():
        # 1. 基础传感器 (总是创建)
        sensors_to_create = {"power", "current", "voltage"}
        
        # 2. 分口电流传感器 (如果配置启用)
        if fetch_outlet:
             for i in range(1, 9):
                 sensors_to_create.add(f"current_{i}")
                 
        # 3. 协调器中已知的其他传感器 (动态发现)
        available_sensors = coordinator.get_available_sensors(pdu_id)
        sensors_to_create = sensors_to_create.union(available_sensors)

        for sensor_type in sensors_to_create:
            # 允许 SENSOR_CONFIGS 中定义的类型，或者以 current_ 开头的动态类型
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

    # 存储添加实体的回调,用于动态添加新传感器
    hass.data[DOMAIN][entry.entry_id]["add_sensor_entities"] = async_add_entities


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
        base_type = sensor_type
        if sensor_type.startswith("current_"):
            base_type = SENSOR_TYPE_CURRENT
        
        config = SENSOR_CONFIGS.get(base_type, {})
        self._attr_device_class = config.get("device_class")
        self._attr_native_unit_of_measurement = config.get("unit")
        self._attr_state_class = config.get("state_class")
        self._attr_icon = config.get("icon")
        
        if sensor_type.startswith("current_"):
            # e.g. current_1 -> 插座 1 电流
            idx = sensor_type.split("_")[1]
            self._sensor_name = f"插座 {idx} 电流"
        else:
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
