from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN, PLATFORMS
from .coordinator import TgnVodaCoordinator
from .helpers import (
    account_device_identifiers,
    account_device_reference,
    counter_device_identifier,
)
from .services import async_setup_services

TgnVodaConfigEntry = ConfigEntry[TgnVodaCoordinator]
CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: dict) -> bool:
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: TgnVodaConfigEntry) -> bool:
    coordinator = TgnVodaCoordinator(hass, entry)
    try:
        await coordinator.async_config_entry_first_refresh()
        entry.runtime_data = coordinator
        _register_devices(hass, entry, coordinator)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except Exception:
        await hass.async_add_executor_job(coordinator.api.session.close)
        raise
    return True


def _register_devices(
    hass: HomeAssistant,
    entry: TgnVodaConfigEntry,
    coordinator: TgnVodaCoordinator,
) -> None:
    registry = dr.async_get(hass)
    data = coordinator.data or {}
    account = data.get("account", {})
    account_id = str(account.get("current_account_id", entry.data.get("account_id", "")))
    registry.async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers=account_device_identifiers(entry.entry_id, account_id),
        name=f"TGN Voda {entry.data.get('account_id', '')}",
        manufacturer="MUP Vodokanal",
        model="Личный кабинет",
    )
    counters = {str(counter["id"]): counter for counter in data.get("counters", [])}
    for counter_id, counter in counters.items():
        registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={counter_device_identifier(entry.entry_id, counter_id)},
            name=counter.get("name", counter_id),
            manufacturer="MUP Vodokanal",
            model="Счётчик воды",
            **account_device_reference(hass, entry.entry_id),
        )
    for device in dr.async_entries_for_config_entry(registry, entry.entry_id):
        stale = {
            identifier
            for identifier in device.identifiers
            if identifier[0] == DOMAIN
            and identifier[1].startswith(f"counter_{entry.entry_id}_")
            and identifier[1][len(f"counter_{entry.entry_id}_") :] not in counters
        }
        if stale:
            registry.async_remove_device(device.id)


async def async_unload_entry(hass: HomeAssistant, entry: TgnVodaConfigEntry) -> bool:
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unload_ok:
        await hass.async_add_executor_job(entry.runtime_data.api.session.close)
    return unload_ok
