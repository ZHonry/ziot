import voluptuous as vol
from homeassistant import config_entries
from homeassistant.const import CONF_HOST, CONF_USERNAME, CONF_PASSWORD

from . import DOMAIN

class ChangsuiPDUFlowHandler(config_entries.ConfigFlow, domain=DOMAIN):
    async def async_step_user(self, user_input=None):
        errors = {}
        if user_input is not None:
            return self.async_create_entry(
                title=user_input.get("pdu_name", f"Changsui PDU {user_input[CONF_HOST]}"),
                data=user_input
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
        return self.async_show_form(step_id="user", data_schema=data_schema, errors=errors)
