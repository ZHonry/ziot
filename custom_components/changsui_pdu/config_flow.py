"""Changsui PDU 配置流程"""
import logging

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD
from homeassistant.core import callback

from . import DOMAIN
from .pdu_client import PDUClient

_LOGGER = logging.getLogger(__name__)


class ChangsuiPDUFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    """Changsui PDU 配置流程"""

    VERSION = 1

    async def async_step_user(self, user_input=None):
        """初始配置步骤"""
        errors = {}
        if user_input is not None:
            # 同一主机只允许添加一次，防止重复配置
            await self.async_set_unique_id(f"changsui_{user_input[CONF_HOST]}")
            self._abort_if_unique_id_configured()

            # 提交前先验证能否连接/登录
            client = PDUClient(
                user_input[CONF_HOST],
                user_input[CONF_USERNAME],
                user_input[CONF_PASSWORD],
                user_input["outlets"],
            )
            try:
                await client.login()
            except Exception:
                _LOGGER.exception("配置验证失败: 无法连接 %s", user_input[CONF_HOST])
                errors["base"] = "cannot_connect"
            finally:
                await client.close()

            if not errors:
                return self.async_create_entry(
                    title=user_input.get(
                        "pdu_name", f"Changsui PDU {user_input[CONF_HOST]}"
                    ),
                    data=user_input,
                )

        data_schema = vol.Schema({
            vol.Required(CONF_HOST): str,
            vol.Required(CONF_USERNAME, default="admin"): str,
            vol.Required(CONF_PASSWORD, default="admin"): str,
            vol.Required("outlets", default=16): int,
            vol.Required("pdu_name", default="昌遂PDU"): str,
            vol.Required("show_outlet_current", default=True): bool,
            vol.Required("show_outlet_power", default=True): bool,
            vol.Required("show_current_limits", default=False): bool,
            vol.Required("show_outlet_energy", default=False): bool,
            vol.Required("scan_interval", default=30): int,
        })
        return self.async_show_form(
            step_id="user", data_schema=data_schema, errors=errors
        )

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """返回选项流程处理器"""
        return ChangsuiPDUOptionsFlowHandler(config_entry)


class ChangsuiPDUOptionsFlowHandler(config_entries.OptionsFlow):
    """选项流程：无需删除重加即可调整轮询间隔与显示项"""

    def __init__(self, config_entry):
        # 注意：不要赋值给 self.config_entry（HA 2024.11+ 已弃用），用私有属性保存
        self._entry = config_entry

    async def async_step_init(self, user_input=None):
        """选项配置步骤"""
        if user_input is not None:
            # 配置统一保存在 entry.data 中（与现有读取逻辑一致），
            # 更新后由 update listener 触发集成重载生效
            self.hass.config_entries.async_update_entry(
                self._entry, data={**self._entry.data, **user_input}
            )
            return self.async_create_entry(title="", data={})

        data = self._entry.data
        schema = vol.Schema({
            vol.Required(
                "scan_interval", default=data.get("scan_interval", 30)
            ): vol.All(int, vol.Range(min=5, max=3600)),
            vol.Required(
                "show_outlet_current", default=data.get("show_outlet_current", True)
            ): bool,
            vol.Required(
                "show_outlet_power", default=data.get("show_outlet_power", True)
            ): bool,
            vol.Required(
                "show_current_limits", default=data.get("show_current_limits", False)
            ): bool,
            vol.Required(
                "show_outlet_energy", default=data.get("show_outlet_energy", False)
            ): bool,
        })
        return self.async_show_form(step_id="init", data_schema=schema)
