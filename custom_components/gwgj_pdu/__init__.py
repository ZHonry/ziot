"""PDU 组件主入口"""
import logging
import os

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform

from .const import (
    DOMAIN,
    DATA_COORDINATOR,
    DATA_DEVICE_REGISTRY,
    DATA_SERVER,
    DATA_UNSUB,
)
from .coordinator import PduCoordinator
from .device_registry import DeviceRegistry
from .pdu_server import PduServer

_LOGGER = logging.getLogger(__name__)

PLATFORMS = [Platform.SWITCH, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置 PDU 集成"""
    # 初始化数据存储
    hass.data.setdefault(DOMAIN, {})

    # 创建数据目录
    data_dir = os.path.join(os.path.dirname(__file__), "gwgj_pdu_ids")
    os.makedirs(data_dir, exist_ok=True)

    # 创建协调器
    coordinator = PduCoordinator(hass)
    
    # 创建设备注册表
    device_registry = DeviceRegistry(hass, data_dir)
    await device_registry.async_load_devices()

    # 创建 PDU Server
    server = PduServer(hass, coordinator, device_registry, entry.data)

    # 存储到 hass.data
    hass.data[DOMAIN][DATA_COORDINATOR] = coordinator
    hass.data[DOMAIN][DATA_DEVICE_REGISTRY] = device_registry
    hass.data[DOMAIN][DATA_SERVER] = server

    # 启动 PDU Server
    await server.start()
    _LOGGER.info("PDU Server 已启动")

    # 设置平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 注册更新监听器
    unsub = entry.add_update_listener(async_reload_entry)
    hass.data[DOMAIN][DATA_UNSUB] = unsub

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载 PDU 集成"""
    # 卸载平台
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # 停止 PDU Server
        server: PduServer = hass.data[DOMAIN].get(DATA_SERVER)
        if server:
            await server.stop()
            _LOGGER.info("PDU Server 已停止")

        # 移除更新监听器
        unsub = hass.data[DOMAIN].get(DATA_UNSUB)
        if unsub:
            unsub()

        # 清理数据
        hass.data[DOMAIN].clear()

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """重新加载配置条目"""
    await hass.config_entries.async_reload(entry.entry_id)
