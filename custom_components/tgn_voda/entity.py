from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import TgnVodaCoordinator
from .helpers import (
    account_device_identifiers,
    account_device_reference,
    counter_device_identifier,
)


class TgnVodaEntity(CoordinatorEntity[TgnVodaCoordinator]):
    """Common coordinator entity and account device metadata."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: TgnVodaCoordinator) -> None:
        super().__init__(coordinator)

    @property
    def account_identifiers(self) -> set[tuple[str, str]]:
        account = (self.coordinator.data or {}).get("account", {})
        account_id = str(
            account.get(
                "current_account_id",
                self.coordinator.config_entry.data.get("account_id", ""),
            )
        )
        return account_device_identifiers(self.coordinator.config_entry.entry_id, account_id)

    @property
    def device_info(self) -> DeviceInfo:
        account = (self.coordinator.data or {}).get("account", {})
        return DeviceInfo(
            identifiers=self.account_identifiers,
            name=f"TGN Voda {account.get('current_account_id', '')}".strip(),
            manufacturer="MUP Vodokanal",
            model="Личный кабинет",
        )


class TgnVodaCounterEntity(TgnVodaEntity):
    """Entity attached to an individual meter device."""

    def __init__(self, coordinator: TgnVodaCoordinator, counter: dict) -> None:
        super().__init__(coordinator)
        self.counter = counter
        self.counter_id = str(counter["id"])

    @property
    def device_info(self) -> DeviceInfo:
        return DeviceInfo(
            identifiers={
                counter_device_identifier(self.coordinator.config_entry.entry_id, self.counter_id)
            },
            name=self.counter.get("name", f"Счётчик {self.counter_id}"),
            manufacturer="MUP Vodokanal",
            model="Счётчик воды",
            **account_device_reference(
                self.coordinator.hass,
                self.coordinator.config_entry.entry_id,
            ),
        )
