"""PDU Sensor 平台 - 优化版本"""
import logging
from homeassistant.components.sensor import (
    SensorEntity,
    SensorDeviceClass,
    SensorStateClass,
)
from homeassistant.const import (
    UnitOfElectricCurrent,
    UnitOfElectricPotential,
    UnitOfPower,
    UnitOfEnergy,
)
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.helpers.device_registry import DeviceInfo

from . import DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, entry, async_add_entities):
    """设置 PDU Sensor 平台"""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]
    outlets = data["outlets"]
    pdu_name = entry.data.get("pdu_name", "Changsui PDU")

    device_info = DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer=entry.data.get("manufacturer", "昌遂"),
        model=entry.data.get("model", "CAN"),
        name=pdu_name,
        sw_version=entry.data.get("sw_version", "V6.0.80"),
        configuration_url=f"http://{client.host}",
    )

    entities = []

    # 整机电参传感器
    main_sensors = [
        ("voltage", "电压", UnitOfElectricPotential.VOLT, SensorDeviceClass.VOLTAGE),
        ("current", "电流", UnitOfElectricCurrent.AMPERE, SensorDeviceClass.CURRENT),
        ("total_power", "总功率", UnitOfPower.WATT, SensorDeviceClass.POWER),
        ("power_factor", "功率因数", None, SensorDeviceClass.POWER_FACTOR),
        ("total_energy", "总能耗", UnitOfEnergy.KILO_WATT_HOUR, SensorDeviceClass.ENERGY),
    ]

    for key, name, unit, device_class in main_sensors:
        entities.append(
            PDUMainSensor(coordinator, device_info, key, name, unit, device_class)
        )

    # 插座传感器(可选)
    show_current = entry.data.get("show_outlet_current", True)
    show_power = entry.data.get("show_outlet_power", True)

    for i in range(outlets):
        idx = i + 1
        if show_power:
            entities.append(
                PDUOutletSensor(
                    coordinator,
                    device_info,
                    idx,
                    "power",
                    "功率",
                    UnitOfPower.WATT,
                    SensorDeviceClass.POWER,
                )
            )
        if show_current:
            entities.append(
                PDUOutletSensor(
                    coordinator,
                    device_info,
                    idx,
                    "current",
                    "电流",
                    UnitOfElectricCurrent.AMPERE,
                    SensorDeviceClass.CURRENT,
                )
            )

    # 今日总能耗
    entities.append(
        PDUDailyEnergySensor(
            coordinator,
            device_info,
            "energy_today",
            "今日能耗",
            UnitOfEnergy.KILO_WATT_HOUR,
        )
    )

    # 插座能耗传感器（如果启用）
    show_outlet_energy = entry.data.get("show_outlet_energy", False)
    energy_tracker = data.get("energy_tracker")
    
    if show_outlet_energy and energy_tracker:
        for i in range(outlets):
            idx = i + 1
            # 总能耗
            entities.append(
                PDUOutletEnergySensor(
                    coordinator,
                    device_info,
                    energy_tracker,
                    idx,
                    "total",
                    "总能耗",
                )
            )
            # 今日用电
            entities.append(
                PDUOutletEnergySensor(
                    coordinator,
                    device_info,
                    energy_tracker,
                    idx,
                    "today",
                    "今日用电",
                )
            )
            # 昨日用电
            entities.append(
                PDUOutletEnergySensor(
                    coordinator,
                    device_info,
                    energy_tracker,
                    idx,
                    "yesterday",
                    "昨日用电",
                )
            )

    async_add_entities(entities)


class PDUMainSensor(CoordinatorEntity, SensorEntity):
    """PDU 主传感器(整机电参)"""

    def __init__(self, coordinator, device_info, key, name, unit, device_class):
        """初始化传感器
        
        Args:
            coordinator: 数据协调器
            device_info: 设备信息
            key: 数据键
            name: 传感器名称
            unit: 单位
            device_class: 设备类别
        """
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{device_info['identifiers']}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = (
            SensorStateClass.MEASUREMENT if device_class != SensorDeviceClass.POWER_FACTOR else None
        )
        self._attr_device_info = device_info
        self._attr_has_entity_name = True

    @property
    def native_value(self):
        """返回传感器值"""
        overview = self.coordinator.data.get("overview", {})
        return overview.get(self.key)

    @property
    def available(self):
        """返回实体是否可用"""
        return self.coordinator.last_update_success and self.coordinator.data.get(
            "overview"
        )


class PDUOutletSensor(CoordinatorEntity, SensorEntity):
    """PDU 插座传感器"""

    def __init__(
        self, coordinator, device_info, idx, key, name, unit, device_class
    ):
        """初始化插座传感器
        
        Args:
            coordinator: 数据协调器
            device_info: 设备信息
            idx: 插座编号
            key: 数据键
            name: 传感器名称
            unit: 单位
            device_class: 设备类别
        """
        super().__init__(coordinator)
        self.idx = idx
        self.key = key
        self._attr_name = f"Outlet {idx} {name}"
        self._attr_unique_id = f"{device_info['identifiers']}_outlet{idx}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = device_class
        self._attr_state_class = SensorStateClass.MEASUREMENT
        self._attr_device_info = device_info
        self._attr_has_entity_name = True

    @property
    def native_value(self):
        """返回传感器值"""
        outlets = self.coordinator.data.get("outlets", [])
        if self.idx <= len(outlets):
            return outlets[self.idx - 1].get(self.key)
        return None

    @property
    def available(self):
        """返回实体是否可用"""
        return self.coordinator.last_update_success and self.coordinator.data.get(
            "outlets"
        )


class PDUDailyEnergySensor(CoordinatorEntity, SensorEntity):
    """PDU 每日能耗传感器"""

    def __init__(self, coordinator, device_info, key, name, unit):
        """初始化每日能耗传感器
        
        Args:
            coordinator: 数据协调器
            device_info: 设备信息
            key: 数据键
            name: 传感器名称
            unit: 单位
        """
        super().__init__(coordinator)
        self.key = key
        self._attr_name = name
        self._attr_unique_id = f"{device_info['identifiers']}_{key}"
        self._attr_native_unit_of_measurement = unit
        self._attr_device_class = SensorDeviceClass.ENERGY
        self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        self._attr_device_info = device_info
        self._attr_has_entity_name = True

    @property
    def native_value(self):
        """返回传感器值"""
        daily = self.coordinator.data.get("daily", {})
        total = daily.get("total", {})
        return total.get("today")

    @property
    def available(self):
        """返回实体是否可用"""
        return self.coordinator.last_update_success and self.coordinator.data.get(
            "daily"
        )


class PDUOutletEnergySensor(CoordinatorEntity, SensorEntity):
    """PDU 插座能耗传感器（总能耗、今日、昨日）"""

    def __init__(
        self, coordinator, device_info, energy_tracker, idx, energy_type, name
    ):
        """初始化插座能耗传感器
        
        Args:
            coordinator: 数据协调器
            device_info: 设备信息
            energy_tracker: 能耗追踪器
            idx: 插座编号
            energy_type: 能耗类型 (total/today/yesterday)
            name: 传感器名称
        """
        super().__init__(coordinator)
        self.idx = idx
        self.energy_type = energy_type
        self.energy_tracker = energy_tracker
        self._attr_name = f"Outlet {idx} {name}"
        self._attr_unique_id = f"{device_info['identifiers']}_outlet{idx}_energy_{energy_type}"
        self._attr_native_unit_of_measurement = UnitOfEnergy.KILO_WATT_HOUR
        self._attr_device_class = SensorDeviceClass.ENERGY
        
        # 根据类型设置状态类别
        if energy_type == "total":
            self._attr_state_class = SensorStateClass.TOTAL_INCREASING
        else:
            self._attr_state_class = SensorStateClass.TOTAL
            
        self._attr_device_info = device_info
        self._attr_has_entity_name = True

    @property
    def native_value(self):
        """返回传感器值"""
        if self.energy_type == "total":
            return self.energy_tracker.get_total_energy(self.idx)
        elif self.energy_type == "today":
            return self.energy_tracker.get_today_usage(self.idx)
        elif self.energy_type == "yesterday":
            return self.energy_tracker.get_yesterday_usage(self.idx)
        return None

    @property
    def available(self):
        """返回实体是否可用"""
        return self.coordinator.last_update_success

