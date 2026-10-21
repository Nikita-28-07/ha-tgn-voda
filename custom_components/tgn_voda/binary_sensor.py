from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import TgnVodaEntity
from .helpers import to_float


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    add_entities([TgnVodaDebtSensor(entry.runtime_data)])


class TgnVodaDebtSensor(TgnVodaEntity, BinarySensorEntity):
    _attr_translation_key = "has_debt"
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_has_debt"

    @property
    def is_on(self) -> bool | None:
        amount = to_float((self.coordinator.data or {}).get("billing", {}).get("to_pay_now"))
        return None if amount is None else amount > 0
