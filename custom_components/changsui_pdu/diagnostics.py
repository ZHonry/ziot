"""Changsui PDU 诊断支持"""
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from . import DOMAIN


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """返回诊断信息"""
    data = hass.data[DOMAIN][entry.entry_id]
    client = data["client"]
    coordinator = data["coordinator"]

    return {
        "entry": {
            "title": entry.title,
            "version": entry.version,
            "data": {
                "host": entry.data.get("host"),
                "username": entry.data.get("username"),
                "outlets": entry.data.get("outlets"),
                "pdu_name": entry.data.get("pdu_name"),
                "show_outlet_current": entry.data.get("show_outlet_current"),
                "show_outlet_power": entry.data.get("show_outlet_power"),
            },
        },
        "client": {
            "host": client.host,
            "logged_in": client._logged_in,
            "session_active": client.session is not None,
        },
        "coordinator": {
            "last_update_success": coordinator.last_update_success,
            "last_update_time": (
                coordinator.last_update_success_time.isoformat()
                if coordinator.last_update_success_time
                else None
            ),
            "update_interval_seconds": coordinator.update_interval.total_seconds(),
        },
        "data": {
            "outlets_count": len(coordinator.data.get("outlets", [])),
            "has_overview": "overview" in coordinator.data,
            "has_daily": "daily" in coordinator.data,
            "overview_keys": (
                list(coordinator.data["overview"].keys())
                if "overview" in coordinator.data
                else []
            ),
        },
    }
