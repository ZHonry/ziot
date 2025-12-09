"""Changsui PDU 组件主入口 - 优化版本"""
import logging
from datetime import timedelta

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.const import Platform

from .pdu_client import PDUClient
from .energy_tracker import EnergyTracker

_LOGGER = logging.getLogger(__name__)

DOMAIN = "changsui_pdu"
PLATFORMS = [Platform.SWITCH, Platform.SENSOR]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """设置 Changsui PDU 集成"""
    hass.data.setdefault(DOMAIN, {})

    host = entry.data["host"]
    username = entry.data["username"]
    password = entry.data["password"]
    outlets = entry.data["outlets"]

    # 创建客户端
    client = PDUClient(host, username, password, outlets)
    
    try:
        await client.login()
    except Exception as e:
        _LOGGER.error("登录 PDU 失败: %s", e)
        return False

    # 数据更新方法
    async def async_update_data():
        """更新 PDU 数据"""
        try:
            overview = await client.get_pdu_overview()
            outlets_data = await client.get_outlet_status()
            daily_data = await client.get_daily_energy()
            
            # 如果启用了能耗统计，获取插座能耗数据
            outlet_energy_data = []
            if entry.data.get("show_outlet_energy", False):
                outlet_energy_data = await client.get_outlet_energy()
                # 更新能耗追踪器
                if "energy_tracker" in hass.data[DOMAIN].get(entry.entry_id, {}):
                    tracker = hass.data[DOMAIN][entry.entry_id]["energy_tracker"]
                    await tracker.update(outlet_energy_data)
            
            return {
                "overview": overview,
                "outlets": outlets_data,
                "daily": daily_data,
                "outlet_energy": outlet_energy_data,
            }
        except Exception as e:
            _LOGGER.error("更新 PDU 数据失败: %s", e)
            raise

    # 获取轮询间隔，默认为 30 秒
    scan_interval = entry.data.get("scan_interval", 30)

    # 创建协调器
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"changsui_pdu_{host}",
        update_method=async_update_data,
        update_interval=timedelta(seconds=scan_interval),
    )

    # 首次刷新
    await coordinator.async_config_entry_first_refresh()

    # 初始化能耗追踪器（如果启用）
    energy_tracker = None
    if entry.data.get("show_outlet_energy", False):
        energy_tracker = EnergyTracker(hass, entry.entry_id)
        await energy_tracker.async_load()
        _LOGGER.info("能耗追踪器已初始化")

    # 存储数据
    hass.data[DOMAIN][entry.entry_id] = {
        "client": client,
        "coordinator": coordinator,
        "outlets": outlets,
        "energy_tracker": energy_tracker,
    }

    # 设置平台
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """卸载 Changsui PDU 集成"""
    # 卸载平台
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unload_ok:
        # 关闭客户端
        client: PDUClient = hass.data[DOMAIN][entry.entry_id]["client"]
        await client.close()
        
        # 清理数据
        hass.data[DOMAIN].pop(entry.entry_id)

    return unload_ok


async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """重新加载集成"""
    _LOGGER.info("重新加载 Changsui PDU 集成: %s", entry.entry_id)
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)
