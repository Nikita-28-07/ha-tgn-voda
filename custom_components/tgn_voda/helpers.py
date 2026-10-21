"""Common config-entry and device lookup helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr

from .const import DOMAIN

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigEntry

    from .coordinator import TgnVodaCoordinator


def to_float(value: object) -> float | None:
    """Convert portal values to floats, returning None for absent/invalid data."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError, OverflowError):
        return None


def account_device_identifier(entry_id: str) -> tuple[str, str]:
    """Return the stable device identifier for an account config entry."""
    return (DOMAIN, f"account_{entry_id}")


def account_device_identifiers(entry_id: str, account_id: str) -> set[tuple[str, str]]:
    """Include the original account identifier so HA can reuse the old device."""
    return {
        account_device_identifier(entry_id),
        (DOMAIN, f"acc_{account_id}"),
    }


def counter_device_identifier(entry_id: str, counter_id: str) -> tuple[str, str]:
    """Return the stable device identifier for a meter."""
    return (DOMAIN, f"counter_{entry_id}_{counter_id}")


def account_device_reference(hass: HomeAssistant, entry_id: str) -> dict[str, str]:
    """Return a parent-device reference compatible with the installed HA API."""
    identifier = account_device_identifier(entry_id)
    get_device_id = getattr(dr, "async_get_device_id_by_identifier", None)
    if get_device_id is not None:
        device_id = get_device_id(hass, identifier, config_entry_id=entry_id)
        if device_id is not None:
            return {"via_device_id": device_id}
    return {"via_device": identifier}


def get_counter_id_for_device(hass: HomeAssistant, device_id: str, entry_id: str) -> str | None:
    """Get a counter id when a service targets one of this entry's meter devices."""
    device = dr.async_get(hass).async_get(device_id)
    if device is None:
        return None
    prefix = f"counter_{entry_id}_"
    for domain, identifier in device.identifiers:
        if domain == DOMAIN and identifier.startswith(prefix):
            return identifier[len(prefix) :]
    return None


def get_config_entry_for_device(
    hass: HomeAssistant,
    device_id: str,
    *,
    entry_id: str | None = None,
) -> ConfigEntry[TgnVodaCoordinator]:
    """Resolve an HA device id to its loaded TGN Voda config entry."""
    registry = dr.async_get(hass)
    device = registry.async_get(device_id)
    if device is None:
        raise ValueError(f"Device {device_id} was not found")

    owner_id = getattr(device, "config_entry_id", None)
    linked_entry_ids = {owner_id} if owner_id is not None else device.config_entries
    if entry_id is not None:
        if entry_id not in linked_entry_ids:
            raise ValueError(f"Device {device_id} is not linked to config entry {entry_id}")
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is not None
            and entry.domain == DOMAIN
            and entry.state is ConfigEntryState.LOADED
            and getattr(entry, "runtime_data", None) is not None
        ):
            return entry
        raise ValueError(f"TGN Voda config entry {entry_id} is not loaded")

    entries = [
        entry
        for linked_entry_id in linked_entry_ids
        if (entry := hass.config_entries.async_get_entry(linked_entry_id)) is not None
        and entry.domain == DOMAIN
        and entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is not None
    ]
    if len(entries) == 1:
        return entries[0]
    if len(entries) > 1:
        raise ValueError(
            f"Device {device_id} is linked to multiple TGN Voda config entries; specify entry_id"
        )
    raise ValueError(f"No loaded TGN Voda config entry for device {device_id}")


def get_coordinator(
    hass: HomeAssistant,
    *,
    entry_id: str | None = None,
    device_id: str | None = None,
) -> tuple[str, TgnVodaCoordinator]:
    """Resolve a coordinator explicitly or use the sole loaded account."""
    if device_id is not None:
        entry = get_config_entry_for_device(hass, device_id, entry_id=entry_id)
        return entry.entry_id, entry.runtime_data

    if entry_id is not None:
        entry = hass.config_entries.async_get_entry(entry_id)
        if (
            entry is None
            or entry.domain != DOMAIN
            or entry.state is not ConfigEntryState.LOADED
            or getattr(entry, "runtime_data", None) is None
        ):
            raise ValueError(f"TGN Voda config entry {entry_id} is not loaded")
        return entry.entry_id, entry.runtime_data

    entries = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
        and getattr(entry, "runtime_data", None) is not None
    ]
    if not entries:
        raise ValueError("No TGN Voda accounts are loaded")
    if len(entries) > 1:
        raise ValueError(
            "Select a TGN Voda device or entry_id when multiple accounts are configured"
        )
    return entries[0].entry_id, entries[0].runtime_data
