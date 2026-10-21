from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfVolume
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import TgnVodaCoordinator
from .entity import TgnVodaCounterEntity, TgnVodaEntity
from .helpers import to_float


@dataclass(frozen=True, kw_only=True)
class TgnVodaSensorDescription(SensorEntityDescription):
    """Describe a value from the account billing summary."""


BILLING_SENSORS = (
    TgnVodaSensorDescription(
        key="to_pay_now",
        translation_key="to_pay_now",
        icon="mdi:cash",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
    TgnVodaSensorDescription(
        key="accrued_in_period",
        translation_key="accrued_in_period",
        icon="mdi:receipt",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
    TgnVodaSensorDescription(
        key="paid_amount",
        translation_key="paid_amount",
        icon="mdi:cash-check",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
    TgnVodaSensorDescription(
        key="opening_debt_amount",
        translation_key="opening_debt_amount",
        icon="mdi:cash-minus",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
    TgnVodaSensorDescription(
        key="recalculation",
        translation_key="recalculation",
        icon="mdi:calculator",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
    TgnVodaSensorDescription(
        key="penalty_accrued_in_period",
        translation_key="penalty_accrued_in_period",
        icon="mdi:alert-circle-outline",
        device_class=SensorDeviceClass.MONETARY,
        native_unit_of_measurement="RUB",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, add_entities: AddEntitiesCallback
) -> None:
    coordinator: TgnVodaCoordinator = entry.runtime_data
    entities: list[SensorEntity] = [
        TgnVodaBillingSensor(coordinator, description) for description in BILLING_SENSORS
    ]
    entities.extend((TgnVodaLastUpdatedSensor(coordinator), TgnVodaCounterCountSensor(coordinator)))
    entities.extend(
        TgnVodaCounterReadingSensor(coordinator, counter)
        for counter in (coordinator.data or {}).get("counters", [])
    )
    add_entities(entities)


class TgnVodaBillingSensor(TgnVodaEntity, SensorEntity):
    entity_description: TgnVodaSensorDescription

    def __init__(
        self, coordinator: TgnVodaCoordinator, description: TgnVodaSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        legacy_keys = {
            "to_pay_now": "to_pay",
            "accrued_in_period": "accrued",
            "paid_amount": "paid",
        }
        if description.key in legacy_keys:
            account = (coordinator.data or {}).get("account", {})
            account_id = account.get(
                "current_account_id",
                coordinator.config_entry.data.get("account_id", ""),
            )
            self._attr_unique_id = (
                f"{coordinator.config_entry.entry_id}_{legacy_keys[description.key]}_{account_id}"
            )
        else:
            self._attr_unique_id = f"{coordinator.config_entry.entry_id}_{description.key}"

    @property
    def native_value(self):
        return to_float(
            (self.coordinator.data or {}).get("billing", {}).get(self.entity_description.key)
        )


class TgnVodaLastUpdatedSensor(TgnVodaEntity, SensorEntity):
    _attr_translation_key = "last_updated"
    _attr_icon = "mdi:clock-outline"
    _attr_device_class = SensorDeviceClass.TIMESTAMP

    def __init__(self, coordinator: TgnVodaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_last_updated"

    @property
    def native_value(self):
        return self.coordinator.last_update_success_time


class TgnVodaCounterCountSensor(TgnVodaEntity, SensorEntity):
    _attr_translation_key = "counters_ids"
    _attr_icon = "mdi:counter"

    def __init__(self, coordinator: TgnVodaCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.config_entry.entry_id}_counters_ids"

    @property
    def native_value(self) -> int:
        return len((self.coordinator.data or {}).get("counters", []))

    @property
    def extra_state_attributes(self) -> dict:
        counters = (self.coordinator.data or {}).get("counters", [])
        return {"counters": counters, "ids": [counter["id"] for counter in counters]}


class TgnVodaCounterReadingSensor(TgnVodaCounterEntity, SensorEntity):
    _attr_translation_key = "last_reading"
    _attr_icon = "mdi:counter"
    _attr_device_class = SensorDeviceClass.WATER
    _attr_state_class = SensorStateClass.TOTAL_INCREASING
    _attr_native_unit_of_measurement = UnitOfVolume.CUBIC_METERS

    def __init__(self, coordinator: TgnVodaCoordinator, counter: dict) -> None:
        super().__init__(coordinator, counter)
        self._attr_unique_id = (
            f"{coordinator.config_entry.entry_id}_counter_{self.counter_id}_last_reading"
        )

    @property
    def native_value(self):
        current = next(
            (
                item
                for item in (self.coordinator.data or {}).get("counters", [])
                if str(item.get("id")) == self.counter_id
            ),
            {},
        )
        return to_float(current.get("last_value"))
