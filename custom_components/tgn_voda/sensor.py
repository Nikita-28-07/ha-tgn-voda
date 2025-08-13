from __future__ import annotations
from homeassistant.components.sensor import SensorEntity
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.config_entries import ConfigEntry
from .const import DOMAIN

async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    coordinator = data["coordinator"]
    api = data["api"]

    add_entities([
        TgnVodaToPaySensor(coordinator, api, entry),
        TgnVodaAccruedSensor(coordinator, api, entry),
        TgnVodaPaidSensor(coordinator, api, entry),
    ])

class _BaseSensor(SensorEntity):
    _attr_should_poll = False

    def __init__(self, coordinator, api, entry: ConfigEntry):
        self._coordinator = coordinator
        self._api = api
        self._entry = entry
        self._attr_available = True

    async def async_added_to_hass(self) -> None:
        self._coordinator.async_add_listener(self.async_write_ha_state)

    @property
    def extra_state_attributes(self):
        d = self._coordinator.data or {}
        return d

    @property
    def device_info(self):
        acc = (self._coordinator.data or {}).get("account", {})
        return {
            "identifiers": {(DOMAIN, f"acc_{acc.get('current_account_id')}")},
            "name": f"TGN Voda {acc.get('current_account_id')}",
            "manufacturer": "MUP Vodokanal",
            "model": "LK",
        }

class TgnVodaToPaySensor(_BaseSensor):
    @property
    def name(self): return "Vodokanal To Pay"
    @property
    def unique_id(self):
        acc = (self._coordinator.data or {}).get("account", {})
        return f"{self._entry.entry_id}_to_pay_{acc.get('current_account_id')}"
    @property
    def native_value(self):
        bill = (self._coordinator.data or {}).get("billing", {})
        return bill.get("to_pay_now")
    @property
    def native_unit_of_measurement(self): return "RUB"
    @property
    def icon(self): return "mdi:cash"

class TgnVodaAccruedSensor(_BaseSensor):
    @property
    def name(self): return "Vodokanal Accrued"
    @property
    def unique_id(self):
        acc = (self._coordinator.data or {}).get("account", {})
        return f"{self._entry.entry_id}_accrued_{acc.get('current_account_id')}"
    @property
    def native_value(self):
        bill = (self._coordinator.data or {}).get("billing", {})
        return bill.get("accrued_in_period")
    @property
    def native_unit_of_measurement(self): return "RUB"
    @property
    def icon(self): return "mdi:receipt"

class TgnVodaPaidSensor(_BaseSensor):
    @property
    def name(self): return "Vodokanal Paid"
    @property
    def unique_id(self):
        acc = (self._coordinator.data or {}).get("account", {})
        return f"{self._entry.entry_id}_paid_{acc.get('current_account_id')}"
    @property
    def native_value(self):
        bill = (self._coordinator.data or {}).get("billing", {})
        return bill.get("paid_amount")
    @property
    def native_unit_of_measurement(self): return "RUB"
    @property
    def icon(self): return "mdi:cash-check"