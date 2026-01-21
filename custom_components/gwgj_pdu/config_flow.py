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
    CONF_PROTOCOL,
    PROTOCOL_SERVER,
    PROTOCOL_CLIENT,
    CONF_PASSWORD,
    DEFAULT_PASSWORD,
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
        """选择协议模式"""
        return self.async_show_menu(
            step_id="user",
            menu_options=["server_config", "client_config"]
        )

    async def async_step_server_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """配置 TCP Server 模式"""
        errors = {}
        if user_input is not None:
            # 对于 Server 模式，只能有一个实例
            # 检查是否已有 Server 实例 (unique_id='server' 或 DOMAIN)
            # 这里我们使用 'server' 作为新的标准 ID
            await self.async_set_unique_id("gwgj_pdu_server")
            self._abort_if_unique_id_configured()

            user_input[CONF_PROTOCOL] = PROTOCOL_SERVER
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

        return self.async_show_form(
            step_id="server_config", 
            data_schema=data_schema,
            errors=errors
        )

    async def async_step_client_config(
        self, user_input: Optional[Dict[str, Any]] = None
    ) -> FlowResult:
        """配置 HTTP Client 模式"""
        errors = {}
        if user_input is not None:
            # 对于 Client 模式，唯一 ID 为 host:port
            unique_id = f"client_{user_input[CONF_HOST]}_{user_input[CONF_PORT]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()

            user_input[CONF_PROTOCOL] = PROTOCOL_CLIENT
            return self.async_create_entry(
                title=f"PDU Client {user_input[CONF_HOST]}",
                data=user_input,
            )

        data_schema = vol.Schema(
            {
                vol.Required(CONF_HOST): str,
                vol.Required(CONF_PORT, default=80): int,
                vol.Required(CONF_PASSWORD, default=DEFAULT_PASSWORD): str,
                vol.Required(CONF_LOG_LEVEL, default=DEFAULT_LOG_LEVEL): vol.In(
                    ["debug", "info", "warning", "error", "critical"]
                ),
            }
        )

        return self.async_show_form(
            step_id="client_config", 
            data_schema=data_schema,
            errors=errors
        )
