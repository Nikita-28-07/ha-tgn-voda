from __future__ import annotations
from datetime import timedelta
import logging
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.const import CONF_SCAN_INTERVAL
from .const import (
    DOMAIN, PLATFORMS, CONF_LOGIN, CONF_PASSWORD, CONF_ACCOUNT_ID,
    CONF_VERIFY_SSL, CONF_SCAN_INTERVAL, CONF_CA_BUNDLE, DEFAULT_SCAN_INTERVAL
)
from .api import TgnVodaApi

_LOGGER = logging.getLogger(__name__)

async def async_setup(hass: HomeAssistant, config) -> bool:
    return True

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hass.data.setdefault(DOMAIN, {})

    data = entry.data
    options = entry.options

    verify_opt = options.get(CONF_VERIFY_SSL, data.get(CONF_VERIFY_SSL, True))
    ca_bundle = options.get(CONF_CA_BUNDLE, data.get(CONF_CA_BUNDLE, ""))
    verify_ssl = verify_opt if isinstance(verify_opt, bool) else True
    if ca_bundle:
        verify_ssl = ca_bundle

    api = TgnVodaApi(
        login=data[CONF_LOGIN],
        password=data[CONF_PASSWORD],
        account_id=str(data[CONF_ACCOUNT_ID]),
        verify_ssl=verify_ssl
    )

    def _login_and_fetch():
        api.authenticate()
        return api.fetch_account_and_billing()

    scan_seconds = int(options.get(CONF_SCAN_INTERVAL, data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)))
    coordinator = DataUpdateCoordinator(
        hass,
        _LOGGER,
        name=f"{DOMAIN}_{entry.entry_id}",
        update_method=lambda: hass.async_add_executor_job(_login_and_fetch),
        update_interval=timedelta(seconds=scan_seconds),
    )

    await coordinator.async_config_entry_first_refresh()

    hass.data[DOMAIN][entry.entry_id] = {
        "api": api,
        "coordinator": coordinator,
    }

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def handle_submit(call: ServiceCall):
        readings = call.data.get("readings", {})
        def _do():
            api.authenticate()
            return api.submit_readings(readings)
        result = await hass.async_add_executor_job(_do)
        _LOGGER.info("tgn_voda.submit_readings(%s): %s", entry.entry_id, result)
    hass.services.async_register(DOMAIN, "submit_readings", handle_submit)

    async def handle_history(call: ServiceCall):
        date_from = call.data["date_from"]
        date_to = call.data["date_to"]
        def _do():
            api.authenticate()
            return api.get_history(date_from, date_to)
        result = await hass.async_add_executor_job(_do)
        hass.bus.async_fire("tgn_voda_history", {"items": result, "entry_id": entry.entry_id})
        _LOGGER.info("tgn_voda.get_history(%s) -> %d items", entry.entry_id, len(result))
    hass.services.async_register(DOMAIN, "get_history", handle_history)

    return True

async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        hass.data[DOMAIN].pop(entry.entry_id, None)
    return unload_ok

async def async_reload_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await async_unload_entry(hass, entry)
    await async_setup_entry(hass, entry)