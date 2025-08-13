from __future__ import annotations
from typing import Any, Dict, Optional
import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.const import CONF_NAME
from .const import (
    DOMAIN, CONF_LOGIN, CONF_PASSWORD, CONF_ACCOUNT_ID,
    CONF_VERIFY_SSL, CONF_SCAN_INTERVAL, CONF_CA_BUNDLE,
    DEFAULT_VERIFY_SSL, DEFAULT_SCAN_INTERVAL, DEFAULT_CA_BUNDLE
)

STEP_USER_DATA_SCHEMA = vol.Schema({
    vol.Required(CONF_LOGIN): str,
    vol.Required(CONF_PASSWORD): str,
    vol.Required(CONF_ACCOUNT_ID): str,
    vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
    vol.Optional(CONF_CA_BUNDLE, default=DEFAULT_CA_BUNDLE): str,
    vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): int,
})

class TgnVodaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        errors: Dict[str, str] = {}
        if user_input is not None:
            unique_id = f"{user_input[CONF_LOGIN]}::{user_input[CONF_ACCOUNT_ID]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=f"TGN Voda {user_input[CONF_ACCOUNT_ID]}",
                data={
                    CONF_LOGIN: user_input[CONF_LOGIN],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                    CONF_ACCOUNT_ID: user_input[CONF_ACCOUNT_ID],
                    CONF_VERIFY_SSL: user_input.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL),
                    CONF_CA_BUNDLE: user_input.get(CONF_CA_BUNDLE, DEFAULT_CA_BUNDLE),
                    CONF_SCAN_INTERVAL: user_input.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                },
            )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_DATA_SCHEMA, errors=errors)

    async def async_step_import(self, import_config: Dict[str, Any]) -> FlowResult:
        return await self.async_step_user(import_config)

class TgnVodaOptionsFlowHandler(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        return await self.async_step_options(user_input)

    async def async_step_options(self, user_input: Optional[Dict[str, Any]] = None) -> FlowResult:
        if user_input is not None:
            return self.async_create_entry(title="", data=user_input)

        data = self.config_entry.data
        options = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_VERIFY_SSL, default=options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL))): bool,
            vol.Optional(CONF_CA_BUNDLE, default=options.get(CONF_CA_BUNDLE, data.get(CONF_CA_BUNDLE, DEFAULT_CA_BUNDLE))): str,
            vol.Optional(CONF_SCAN_INTERVAL, default=options.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL))): int,
        })
        return self.async_show_form(step_id="options", data_schema=schema)

def get_options_flow(config_entry):
    return TgnVodaOptionsFlowHandler(config_entry)