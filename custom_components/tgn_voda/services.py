from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError

from .const import (
    DOMAIN,
    EVENT_ACCRUALS,
    EVENT_ACCRUALS_DETALIZATION,
    EVENT_COUNTER_INFO,
    EVENT_HISTORY,
    EVENT_PAYMENTS,
)
from .helpers import get_coordinator, get_counter_id_for_device

if TYPE_CHECKING:
    from .coordinator import TgnVodaCoordinator

_LOGGER = logging.getLogger(__name__)
SERVICE_SUBMIT_READINGS = "submit_readings"
SERVICE_GET_HISTORY = "get_history"
SERVICE_GET_PAYMENTS_HISTORY = "get_payments_history"
SERVICE_GET_ACCRUALS_HISTORY = "get_accruals_history"
SERVICE_GET_ACCRUALS_DETALIZATION = "get_accruals_detalization"
SERVICE_GET_COUNTER_INFO = "get_counter_info"


def _schema(*required: tuple[str, Any]) -> vol.Schema:
    schema = {
        vol.Optional("entry_id"): str,
        vol.Optional("device_id"): str,
    }
    schema.update({vol.Required(key): value for key, value in required})
    return vol.Schema(schema)


def _counter_info_schema() -> vol.Schema:
    def validate(data: dict[str, Any]) -> dict[str, Any]:
        if not data.get("counter_id") and not data.get("device_id"):
            raise vol.Invalid("Provide counter_id or select a meter device")
        return data

    return vol.Schema(
        vol.All(
            {
                vol.Optional("entry_id"): str,
                vol.Optional("device_id"): str,
                vol.Optional("counter_id"): str,
            },
            validate,
        )
    )


SERVICE_SCHEMAS = {
    SERVICE_SUBMIT_READINGS: _schema(("readings", dict)),
    SERVICE_GET_HISTORY: _schema(("date_from", str), ("date_to", str)),
    SERVICE_GET_PAYMENTS_HISTORY: _schema(("date_from", str), ("date_to", str)),
    SERVICE_GET_ACCRUALS_HISTORY: _schema(("date_from", str), ("date_to", str)),
    SERVICE_GET_ACCRUALS_DETALIZATION: _schema(("month", str)),
    SERVICE_GET_COUNTER_INFO: _counter_info_schema(),
}


@dataclass(frozen=True)
class ServiceDescription:
    """Describe a service handler and its accepted call schema."""

    handler: Callable[[HomeAssistant, ServiceCall], Awaitable[None]]
    schema: vol.Schema


def async_setup_services(hass: HomeAssistant) -> None:
    """Register service handlers once, supporting multiple configured accounts."""
    for name, description in SERVICE_DESCRIPTIONS.items():
        if not hass.services.has_service(DOMAIN, name):

            async def handle_call(call: ServiceCall, service_handler=description.handler) -> None:
                await service_handler(hass, call)

            hass.services.async_register(DOMAIN, name, handle_call, schema=description.schema)


def _service_error(key: str, **placeholders: str) -> HomeAssistantError:
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=key,
        translation_placeholders=placeholders,
    )


async def _execute(
    hass: HomeAssistant, call: ServiceCall, method: str, *args: Any
) -> tuple[str, TgnVodaCoordinator, Any]:
    try:
        entry_id, coordinator = get_coordinator(
            hass,
            entry_id=call.data.get("entry_id"),
            device_id=call.data.get("device_id"),
        )
    except ValueError as err:
        raise _service_error("account_selection_required", error=str(err)) from err
    try:
        result = await coordinator.async_api_call(method, *args)
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise _service_error("service_failed", service=method, error=str(err)) from err
    return entry_id, coordinator, result


async def _submit_readings(hass: HomeAssistant, call: ServiceCall) -> None:
    entry_id, coordinator, result = await _execute(
        hass, call, "submit_readings", call.data["readings"]
    )
    if not isinstance(result, dict) or not result.get("success"):
        messages = result.get("messages") if isinstance(result, dict) else None
        detail = (
            "; ".join(str(message) for message in messages)
            if isinstance(messages, list)
            else str(result)
        )
        raise _service_error(
            "service_failed",
            service=SERVICE_SUBMIT_READINGS,
            error=detail or "Portal rejected readings",
        )
    _LOGGER.info("Readings submission for %s: %s", entry_id, result)
    await coordinator.async_request_refresh()


async def _get_history(hass: HomeAssistant, call: ServiceCall) -> None:
    entry_id, _, result = await _execute(
        hass, call, "get_history", call.data["date_from"], call.data["date_to"]
    )
    hass.bus.async_fire(EVENT_HISTORY, {"items": result, "entry_id": entry_id})


async def _get_payments_history(hass: HomeAssistant, call: ServiceCall) -> None:
    entry_id, _, result = await _execute(
        hass,
        call,
        "get_payments_history",
        call.data["date_from"],
        call.data["date_to"],
    )
    hass.bus.async_fire(EVENT_PAYMENTS, {"items": result, "entry_id": entry_id})


async def _get_accruals_history(hass: HomeAssistant, call: ServiceCall) -> None:
    entry_id, _, result = await _execute(
        hass,
        call,
        "get_accruals_history",
        call.data["date_from"],
        call.data["date_to"],
    )
    hass.bus.async_fire(EVENT_ACCRUALS, {"items": result, "entry_id": entry_id})


async def _get_accruals_detalization(hass: HomeAssistant, call: ServiceCall) -> None:
    month = call.data["month"]
    entry_id, _, result = await _execute(hass, call, "get_accruals_detalization", month)
    hass.bus.async_fire(
        EVENT_ACCRUALS_DETALIZATION,
        {"items": result, "month": month, "entry_id": entry_id},
    )


async def _get_counter_info(hass: HomeAssistant, call: ServiceCall) -> None:
    try:
        entry_id, coordinator = get_coordinator(
            hass,
            entry_id=call.data.get("entry_id"),
            device_id=call.data.get("device_id"),
        )
    except ValueError as err:
        raise _service_error("account_selection_required", error=str(err)) from err
    counter_id = call.data.get("counter_id")
    device_counter_id = None
    if call.data.get("device_id"):
        device_counter_id = get_counter_id_for_device(hass, call.data["device_id"], entry_id)
    if counter_id is None:
        counter_id = device_counter_id
    elif device_counter_id is not None and str(counter_id) != device_counter_id:
        raise _service_error("counter_device_mismatch")
    if counter_id is None:
        raise _service_error("counter_required")
    try:
        result = await coordinator.async_api_call("get_counter_info", str(counter_id))
    except ConfigEntryAuthFailed:
        raise
    except Exception as err:
        raise _service_error(
            "service_failed", service=SERVICE_GET_COUNTER_INFO, error=str(err)
        ) from err
    hass.bus.async_fire(EVENT_COUNTER_INFO, {"counter": result, "entry_id": entry_id})


SERVICE_DESCRIPTIONS: dict[str, ServiceDescription] = {
    SERVICE_SUBMIT_READINGS: ServiceDescription(
        _submit_readings, SERVICE_SCHEMAS[SERVICE_SUBMIT_READINGS]
    ),
    SERVICE_GET_HISTORY: ServiceDescription(_get_history, SERVICE_SCHEMAS[SERVICE_GET_HISTORY]),
    SERVICE_GET_PAYMENTS_HISTORY: ServiceDescription(
        _get_payments_history, SERVICE_SCHEMAS[SERVICE_GET_PAYMENTS_HISTORY]
    ),
    SERVICE_GET_ACCRUALS_HISTORY: ServiceDescription(
        _get_accruals_history, SERVICE_SCHEMAS[SERVICE_GET_ACCRUALS_HISTORY]
    ),
    SERVICE_GET_ACCRUALS_DETALIZATION: ServiceDescription(
        _get_accruals_detalization,
        SERVICE_SCHEMAS[SERVICE_GET_ACCRUALS_DETALIZATION],
    ),
    SERVICE_GET_COUNTER_INFO: ServiceDescription(
        _get_counter_info, SERVICE_SCHEMAS[SERVICE_GET_COUNTER_INFO]
    ),
}
