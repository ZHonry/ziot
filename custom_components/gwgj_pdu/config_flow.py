"""PDU 配置流程"""
import voluptuous as vol
from typing import Any, Dict, Optional

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResult

from .const import (
    DOMAIN,
    CONF_HOST,
    CONF_PORT,
    CONF_LOG_LEVEL,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_LOG_LEVEL,
)
from .const import (
    CONF_FETCH_OUTLET_CURRENT,
    CONF_WEB_USERNAME,
    CONF_WEB_PASSWORD,
    DEFAULT_FETCH_OUTLET_CURRENT,
    DEFAULT_WEB_USERNAME,
    DEFAULT_WEB_PASSWORD,
)


class ConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """PDU 配置流程"""

    VERSION = 1

    async def async_step_user(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """处理用户初始配置"""
        if user_input is not None:
            # 检查是否已存在配置
            await self.async_set_unique_id(DOMAIN)
            self._abort_if_unique_id_configured()

            return self.async_create_entry(
                title="PDU Server",
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST, default=DEFAULT_HOST): str,
                vol.Required(CONF_PORT, default=DEFAULT_PORT): int,
                vol.Required(CONF_LOG_LEVEL, default=DEFAULT_LOG_LEVEL): vol.In(
                    ["debug", "info", "warning", "error", "critical"]
                ),
                vol.Required(
                    CONF_FETCH_OUTLET_CURRENT, default=DEFAULT_FETCH_OUTLET_CURRENT
                ): bool,
                vol.Required(CONF_WEB_USERNAME, default=DEFAULT_WEB_USERNAME): str,
                vol.Required(CONF_WEB_PASSWORD, default=DEFAULT_WEB_PASSWORD): str,
            }
        )

        return self.async_show_form(step_id="user", data_schema=data_schema)
