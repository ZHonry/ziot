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

    # 创建数据目录 (Shared? Or per entry? Let's keep it shared path but separate instance?)
    # Actually if we want separate registries, we need separate files?
    # Or just share the class but scopes are different?
    # DeviceRegistry(hass, data_dir) loads files from data_dir.
    # If we want ISOLATION, we should use different dirs or one registry per entry?
    # If we use one registry per entry, they write to same files if dir is same.
    # To avoid conflict, use entry_id in path?
    # Or just Accept proper isolation.
    # 数据存储路径优化：移动到 .storage 目录下，避免污染组件目录
    # entry_id 是配置条目的唯一 ID，重启后不会改变，因此 ID 是固定的
    storage_dir = hass.config.path(".storage", "gwgj_pdu_data", entry.entry_id)
    os.makedirs(storage_dir, exist_ok=True)
    
    # 向下兼容：如果旧目录存在数据，尝试迁移(可选，暂不实现，假设是新环境)
    data_dir = storage_dir

    # 创建协调器
    coordinator = PduCoordinator(hass)
    
    # 创建设备注册表
    device_registry = DeviceRegistry(hass, data_dir)
    await device_registry.async_load_devices()

    # 根据配置选择 Server 或 Client
    from .const import CONF_PROTOCOL, PROTOCOL_CLIENT
    from .pdu_client import PduClient
    
    protocol = entry.data.get(CONF_PROTOCOL)
    
    if protocol == PROTOCOL_CLIENT:
        _LOGGER.info(f"初始化 PDU Client模式: {entry.title}")
        server = PduClient(hass, coordinator, device_registry, entry.data, entry.entry_id)
    else:
        _LOGGER.info(f"初始化 PDU Server模式: {entry.title}")
        server = PduServer(hass, coordinator, device_registry, entry.data, entry.entry_id)

    # 存储到 hass.data，使用 entry_id 隔离
    hass.data[DOMAIN][entry.entry_id] = {
        DATA_COORDINATOR: coordinator,
        DATA_DEVICE_REGISTRY: device_registry,
        DATA_SERVER: server,
    }

    # 启动
    await server.start()
    
    # 设置平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    # 注册更新监听器
    unsub = entry.add_update_listener(async_reload_entry)
    hass.data[DOMAIN][entry.entry_id][DATA_UNSUB] = unsub

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载 PDU 集成"""
    # 卸载平台
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok and entry.entry_id in hass.data[DOMAIN]:
        data = hass.data[DOMAIN][entry.entry_id]
        
        # 停止 Server/Client
        server = data.get(DATA_SERVER)
        if server:
            await server.stop()
            _LOGGER.info("PDU 服务已停止")

        # 移除更新监听器
        unsub = data.get(DATA_UNSUB)
        if unsub:
            unsub()

        # 清理数据
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """重新加载配置条目"""
    await hass.config_entries.async_reload(entry.entry_id)
