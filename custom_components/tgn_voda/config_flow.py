from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.data_entry_flow import FlowResult

from .api import TgnVodaApi, TgnVodaApiError, TgnVodaAuthenticationError
from .const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_CA_BUNDLE,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DEFAULT_BASE_URL,
    DEFAULT_CA_BUNDLE,
    DEFAULT_SCAN_INTERVAL,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


async def _validate(hass: HomeAssistant, data: dict[str, Any]) -> None:
    verify = data.get(CONF_CA_BUNDLE) or data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
    api = TgnVodaApi(
        data[CONF_LOGIN],
        data[CONF_PASSWORD],
        str(data[CONF_ACCOUNT_ID]),
        verify_ssl=verify,
        base_url=data.get(CONF_BASE_URL, DEFAULT_BASE_URL),
    )

    def validate() -> None:
        try:
            api.authenticate()
            api.fetch_account_and_billing()
        finally:
            api.session.close()

    await hass.async_add_executor_job(validate)


def _account_is_configured(
    hass: HomeAssistant,
    account_id: str,
    *,
    exclude_entry_id: str | None = None,
) -> bool:
    return any(
        entry.entry_id != exclude_entry_id
        and str(entry.data.get(CONF_ACCOUNT_ID, "")) == account_id
        for entry in hass.config_entries.async_entries(DOMAIN)
    )


class TgnVodaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        errors = {}
        if user_input is not None:
            unique_id = f"{user_input[CONF_LOGIN]}::{user_input[CONF_ACCOUNT_ID]}"
            await self.async_set_unique_id(unique_id)
            self._abort_if_unique_id_configured()
            if _account_is_configured(self.hass, str(user_input[CONF_ACCOUNT_ID])):
                return self.async_abort(reason="already_configured")
            try:
                await _validate(self.hass, user_input)
            except TgnVodaAuthenticationError:
                errors["base"] = "invalid_auth"
            except TgnVodaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating user configuration")
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title=f"TGN Voda {user_input[CONF_ACCOUNT_ID]}",
                    data=user_input,
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_LOGIN): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT_ID): str,
                vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
                vol.Optional(CONF_VERIFY_SSL, default=DEFAULT_VERIFY_SSL): bool,
                vol.Optional(CONF_CA_BUNDLE, default=DEFAULT_CA_BUNDLE): str,
                vol.Optional(CONF_SCAN_INTERVAL, default=DEFAULT_SCAN_INTERVAL): vol.All(
                    int, vol.Range(min=60)
                ),
            }
        )
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_import(self, import_config: dict[str, Any]) -> FlowResult:
        return await self.async_step_user(import_config)

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        entry = self._get_reconfigure_entry()
        current = {**entry.data, **entry.options}
        errors = {}
        if user_input is not None:
            candidate = {**current, **user_input}
            new_unique_id = f"{candidate[CONF_LOGIN]}::{candidate[CONF_ACCOUNT_ID]}"
            if _account_is_configured(
                self.hass,
                str(candidate[CONF_ACCOUNT_ID]),
                exclude_entry_id=entry.entry_id,
            ):
                errors["base"] = "already_configured"
            else:
                try:
                    await _validate(self.hass, candidate)
                except TgnVodaAuthenticationError:
                    errors["base"] = "invalid_auth"
                except TgnVodaApiError:
                    errors["base"] = "cannot_connect"
                except Exception:  # noqa: BLE001
                    _LOGGER.exception("Unexpected error while reconfiguring entry")
                    errors["base"] = "unknown"
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        unique_id=new_unique_id,
                        data=candidate,
                        options={},
                        title=f"TGN Voda {candidate[CONF_ACCOUNT_ID]}",
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
        schema = vol.Schema(
            {
                vol.Required(CONF_LOGIN, default=current[CONF_LOGIN]): str,
                vol.Required(CONF_PASSWORD): str,
                vol.Required(CONF_ACCOUNT_ID, default=current[CONF_ACCOUNT_ID]): str,
                vol.Optional(
                    CONF_BASE_URL, default=current.get(CONF_BASE_URL, DEFAULT_BASE_URL)
                ): str,
                vol.Optional(
                    CONF_VERIFY_SSL, default=current.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                ): bool,
                vol.Optional(
                    CONF_CA_BUNDLE, default=current.get(CONF_CA_BUNDLE, DEFAULT_CA_BUNDLE)
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL,
                    default=current.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
                ): vol.All(int, vol.Range(min=60)),
            }
        )
        return self.async_show_form(step_id="reconfigure", data_schema=schema, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> FlowResult:
        reauth_entry = self._get_reauth_entry()
        errors = {}
        if user_input is not None:
            candidate = {**reauth_entry.data, **reauth_entry.options, **user_input}
            try:
                await _validate(self.hass, candidate)
            except TgnVodaAuthenticationError:
                errors["base"] = "invalid_auth"
            except TgnVodaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during reauthentication")
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    data_updates={CONF_PASSWORD: user_input[CONF_PASSWORD]},
                )
        schema = vol.Schema(
            {
                vol.Required(CONF_PASSWORD): str,
            }
        )
        return self.async_show_form(step_id="reauth_confirm", data_schema=schema, errors=errors)

    @staticmethod
    @callback
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> TgnVodaOptionsFlowHandler:
        return TgnVodaOptionsFlowHandler()


class TgnVodaOptionsFlowHandler(config_entries.OptionsFlowWithReload):
    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        data = {**self.config_entry.data, **self.config_entry.options}
        if user_input is not None:
            candidate = {**data, **user_input}
            try:
                await _validate(self.hass, candidate)
            except TgnVodaAuthenticationError:
                return self.async_show_form(
                    step_id="init", data_schema=self._schema(data), errors={"base": "invalid_auth"}
                )
            except TgnVodaApiError:
                return self.async_show_form(
                    step_id="init",
                    data_schema=self._schema(data),
                    errors={"base": "cannot_connect"},
                )
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error while validating options")
                return self.async_show_form(
                    step_id="init", data_schema=self._schema(data), errors={"base": "unknown"}
                )
            return self.async_create_entry(title="", data=user_input)
        return self.async_show_form(step_id="init", data_schema=self._schema(data))

    @staticmethod
    def _schema(data: dict[str, Any]) -> vol.Schema:
        return vol.Schema(
            {
                vol.Optional(CONF_BASE_URL, default=data.get(CONF_BASE_URL, DEFAULT_BASE_URL)): str,
                vol.Optional(
                    CONF_VERIFY_SSL, default=data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)
                ): bool,
                vol.Optional(
                    CONF_CA_BUNDLE, default=data.get(CONF_CA_BUNDLE, DEFAULT_CA_BUNDLE)
                ): str,
                vol.Optional(
                    CONF_SCAN_INTERVAL, default=data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
                ): vol.All(int, vol.Range(min=60)),
            }
        )
