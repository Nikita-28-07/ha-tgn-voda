from __future__ import annotations

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import TgnVodaConfigEntry

TO_REDACT = {
    "login",
    "password",
    "email",
    "phone",
    "holder_name",
    "address",
    "account_id",
    "current_account_id",
    "counter_id",
    "id",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: TgnVodaConfigEntry
) -> dict:
    coordinator = entry.runtime_data
    return {
        "entry": async_redact_data({**entry.data, **entry.options}, TO_REDACT),
        "last_update_success": coordinator.last_update_success,
        "last_update_success_time": coordinator.last_update_success_time,
        "data": async_redact_data(coordinator.data, TO_REDACT),
    }
