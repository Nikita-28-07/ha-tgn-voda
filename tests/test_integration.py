from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    HomeAssistantError,
)
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.tgn_voda.api import (
    TgnVodaApiError,
    TgnVodaAuthenticationError,
)
from custom_components.tgn_voda.const import (
    CONF_ACCOUNT_ID,
    CONF_BASE_URL,
    CONF_CA_BUNDLE,
    CONF_LOGIN,
    CONF_PASSWORD,
    CONF_SCAN_INTERVAL,
    CONF_VERIFY_SSL,
    DOMAIN,
    EVENT_ACCRUALS,
    EVENT_ACCRUALS_DETALIZATION,
    EVENT_HISTORY,
    EVENT_PAYMENTS,
)
from custom_components.tgn_voda.coordinator import TgnVodaCoordinator
from custom_components.tgn_voda.helpers import (
    account_device_identifier,
    account_device_reference,
    get_coordinator,
    to_float,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Allow Home Assistant to load the integration from this repository."""


@pytest.fixture
def config_entry() -> MockConfigEntry:
    """Create a TGN Voda config entry for HA tests."""
    return MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda 12345",
        unique_id="test-login::12345",
        data={
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "12345",
        },
    )


@pytest.fixture
def portal_api(monkeypatch: pytest.MonkeyPatch) -> Mock:
    """Mock the synchronous portal client without making network requests."""
    api = Mock()
    api.authenticate.return_value = None
    api.fetch_account_and_billing.return_value = {
        "account": {
            "current_account_id": "12345",
            "holder_name": "Example User",
            "address": "Example address",
            "phone": "+7 900 000-00-00",
            "email": "example@example.test",
        },
        "billing": {"to_pay_now": 123.45, "accrued_in_period": 100.0},
        "counters": [{"id": "67890", "name": "Холодная вода", "last_value": 42.0}],
    }
    api.get_history.return_value = [{"date": "2026-01-01", "value": 42.0}]
    api.get_payments_history.return_value = []
    api.get_accruals_history.return_value = []
    api.get_accruals_detalization.return_value = []
    api.get_counter_info.return_value = {
        "meter_type": "Meter type",
        "digit_capacity": 6.0,
        "tariff_count": 1.0,
        "transformation_coefficient": 1.0,
        "verification_date": "2023-02-01",
        "next_verification_date": "2029-02-01",
    }
    api.submit_readings.return_value = {"success": True, "applied": []}
    monkeypatch.setattr("custom_components.tgn_voda.coordinator.TgnVodaApi", lambda **kwargs: api)
    return api


async def _setup_entry(hass: HomeAssistant, entry: MockConfigEntry) -> None:
    if hass.config_entries.async_get_entry(entry.entry_id) is None:
        entry.add_to_hass(hass)
    await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()


async def test_setup_entry_creates_runtime_and_entities(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)

    assert config_entry.state is ConfigEntryState.LOADED
    assert isinstance(config_entry.runtime_data, TgnVodaCoordinator)
    assert config_entry.runtime_data.data["billing"]["to_pay_now"] == 123.45
    assert portal_api.authenticate.called

    registry = er.async_get(hass)
    billing_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{config_entry.entry_id}_to_pay_12345"
    )
    meter_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{config_entry.entry_id}_counter_67890_last_reading"
    )
    assert billing_entity_id is not None
    assert meter_entity_id is not None
    assert hass.states.get(billing_entity_id).attributes["device_class"] == "monetary"
    assert hass.states.get(meter_entity_id).attributes["device_class"] == "water"
    timestamp_entity_id = registry.async_get_entity_id(
        "sensor", DOMAIN, f"{config_entry.entry_id}_last_updated"
    )
    assert timestamp_entity_id is not None
    assert hass.states.get(timestamp_entity_id) is not None
    assert hass.states.get(timestamp_entity_id).state not in {"unknown", "unavailable"}


async def test_debt_sensor_and_refresh_button_use_coordinator(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)

    debt_entity_id = er.async_get(hass).async_get_entity_id(
        "binary_sensor", DOMAIN, f"{config_entry.entry_id}_has_debt"
    )
    refresh_button_id = er.async_get(hass).async_get_entity_id(
        "button", DOMAIN, f"{config_entry.entry_id}_refresh"
    )
    assert debt_entity_id is not None
    assert refresh_button_id is not None
    assert hass.states.get(debt_entity_id).state == "on"

    await hass.services.async_call(
        "button", "press", {"entity_id": refresh_button_id}, blocking=True
    )

    assert portal_api.authenticate.call_count == 2
    assert portal_api.fetch_account_and_billing.call_count == 2


async def test_unload_closes_portal_session(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)

    assert await hass.config_entries.async_unload(config_entry.entry_id)
    await hass.async_block_till_done()

    assert config_entry.state is ConfigEntryState.NOT_LOADED
    portal_api.session.close.assert_called_once()


async def test_failed_initial_refresh_closes_portal_session(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    portal_api.authenticate.side_effect = TgnVodaAuthenticationError("invalid credentials")
    config_entry.add_to_hass(hass)

    await hass.config_entries.async_setup(config_entry.entry_id)

    portal_api.session.close.assert_called_once()
    assert getattr(config_entry, "runtime_data", None) is None


async def test_devices_are_registered_under_account_and_stale_devices_removed(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    config_entry.add_to_hass(hass)
    registry = dr.async_get(hass)
    entry_account = f"account_{config_entry.entry_id}"
    legacy_account = registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, "acc_12345")},
        name="Legacy TGN Voda account",
    )
    registry.async_get_or_create(
        config_entry_id=config_entry.entry_id,
        identifiers={(DOMAIN, f"counter_{config_entry.entry_id}_old-meter")},
        name="Old meter",
    )

    await _setup_entry(hass, config_entry)

    account = registry.async_get_device_by_identifier(
        (DOMAIN, entry_account), config_entry.entry_id
    )
    counter = registry.async_get_device_by_identifier(
        (DOMAIN, f"counter_{config_entry.entry_id}_67890"), config_entry.entry_id
    )
    stale = registry.async_get_device_by_identifier(
        (DOMAIN, f"counter_{config_entry.entry_id}_old-meter"), config_entry.entry_id
    )
    assert account is not None
    assert account.id == legacy_account.id
    assert counter is not None
    assert counter.via_device_id == account.id
    assert stale is None


def test_account_device_reference_falls_back_on_older_home_assistant(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.tgn_voda import helpers

    monkeypatch.setattr(
        helpers.dr,
        "async_get_device_id_by_identifier",
        None,
        raising=False,
    )

    assert account_device_reference(hass, config_entry.entry_id) == {
        "via_device": account_device_identifier(config_entry.entry_id)
    }


async def test_account_without_counters_still_loads_account_sensors(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    portal_api.fetch_account_and_billing.return_value["counters"] = []

    await _setup_entry(hass, config_entry)

    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{config_entry.entry_id}"), config_entry.entry_id
    )
    billing_entity_id = er.async_get(hass).async_get_entity_id(
        "sensor", DOMAIN, f"{config_entry.entry_id}_to_pay_12345"
    )
    assert account is not None
    assert billing_entity_id is not None
    assert hass.states.get(billing_entity_id).state == "123.45"
    assert not any(
        identifier[1].startswith(f"counter_{config_entry.entry_id}_")
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), config_entry.entry_id)
        for identifier in device.identifiers
        if identifier[0] == DOMAIN
    )


async def test_legacy_billing_sensor_unique_ids_are_preserved(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)

    unique_ids = {
        entity.unique_id
        for entity in er.async_entries_for_config_entry(er.async_get(hass), config_entry.entry_id)
    }

    assert f"{config_entry.entry_id}_to_pay_12345" in unique_ids
    assert f"{config_entry.entry_id}_accrued_12345" in unique_ids
    assert f"{config_entry.entry_id}_paid_12345" in unique_ids


async def test_history_service_fires_compatible_event(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    received = []
    hass.bus.async_listen(EVENT_HISTORY, lambda event: received.append(event.data))
    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{config_entry.entry_id}"), config_entry.entry_id
    )
    assert account is not None

    await hass.services.async_call(
        DOMAIN,
        "get_history",
        {
            "device_id": account.id,
            "date_from": "01.01.2026",
            "date_to": "31.01.2026",
        },
        blocking=True,
    )

    portal_api.get_history.assert_called_once_with("01.01.2026", "31.01.2026")
    assert received == [
        {
            "items": [{"date": "2026-01-01", "value": 42.0}],
            "entry_id": config_entry.entry_id,
        }
    ]


@pytest.mark.parametrize(
    ("service", "event_name", "method", "service_data", "expected_args", "extra"),
    [
        (
            "get_payments_history",
            EVENT_PAYMENTS,
            "get_payments_history",
            {"date_from": "01.01.2026", "date_to": "31.01.2026"},
            ("01.01.2026", "31.01.2026"),
            {},
        ),
        (
            "get_accruals_history",
            EVENT_ACCRUALS,
            "get_accruals_history",
            {"date_from": "01.01.2026", "date_to": "31.01.2026"},
            ("01.01.2026", "31.01.2026"),
            {},
        ),
        (
            "get_accruals_detalization",
            EVENT_ACCRUALS_DETALIZATION,
            "get_accruals_detalization",
            {"month": "01.01.2026"},
            ("01.01.2026",),
            {"month": "01.01.2026"},
        ),
    ],
)
async def test_history_services_preserve_event_payloads(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    service: str,
    event_name: str,
    method: str,
    service_data: dict[str, str],
    expected_args: tuple[str, ...],
    extra: dict[str, str],
) -> None:
    await _setup_entry(hass, config_entry)
    received = []
    hass.bus.async_listen(event_name, lambda event: received.append(event.data))
    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{config_entry.entry_id}"), config_entry.entry_id
    )
    assert account is not None

    await hass.services.async_call(
        DOMAIN,
        service,
        {"device_id": account.id, **service_data},
        blocking=True,
    )

    getattr(portal_api, method).assert_called_once_with(*expected_args)
    assert received == [{"items": [], **extra, "entry_id": config_entry.entry_id}]


async def test_counter_info_service_accepts_counter_device(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    counter = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"counter_{config_entry.entry_id}_67890"), config_entry.entry_id
    )
    assert counter is not None
    received = []
    hass.bus.async_listen("tgn_voda_counter_info", lambda event: received.append(event.data))

    await hass.services.async_call(
        DOMAIN,
        "get_counter_info",
        {"device_id": counter.id},
        blocking=True,
    )

    portal_api.get_counter_info.assert_called_once_with("67890")
    assert received[0]["counter"] == portal_api.get_counter_info.return_value


async def test_counter_info_service_accepts_explicit_entry_and_counter_ids(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    received = []
    hass.bus.async_listen("tgn_voda_counter_info", lambda event: received.append(event.data))

    await hass.services.async_call(
        DOMAIN,
        "get_counter_info",
        {"entry_id": config_entry.entry_id, "counter_id": "67890"},
        blocking=True,
    )

    portal_api.get_counter_info.assert_called_once_with("67890")
    assert received == [
        {
            "counter": portal_api.get_counter_info.return_value,
            "entry_id": config_entry.entry_id,
        }
    ]


async def test_counter_info_service_rejects_mismatched_counter_and_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
) -> None:
    await _setup_entry(hass, config_entry)
    counter = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"counter_{config_entry.entry_id}_67890"), config_entry.entry_id
    )
    assert counter is not None

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_counter_info",
            {"device_id": counter.id, "counter_id": "different-meter"},
            blocking=True,
        )

    portal_api.get_counter_info.assert_not_called()


async def test_counter_info_service_rejects_mismatched_entry_and_device(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
) -> None:
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda 54321",
        unique_id="test-login::54321",
        data={
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "54321",
        },
    )
    first_data = portal_api.fetch_account_and_billing.return_value
    second_data = {
        **first_data,
        "account": {**first_data["account"], "current_account_id": "54321"},
        "billing": dict(first_data["billing"]),
        "counters": [dict(counter) for counter in first_data["counters"]],
    }
    portal_api.fetch_account_and_billing.side_effect = [first_data, second_data]
    await _setup_entry(hass, config_entry)
    await _setup_entry(hass, second_entry)
    second_counter = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"counter_{second_entry.entry_id}_67890"), second_entry.entry_id
    )
    assert second_counter is not None

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_counter_info",
            {"entry_id": config_entry.entry_id, "device_id": second_counter.id},
            blocking=True,
        )

    portal_api.get_counter_info.assert_not_called()


async def test_submit_readings_service_targets_selected_account(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{config_entry.entry_id}"), config_entry.entry_id
    )
    assert account is not None

    await hass.services.async_call(
        DOMAIN,
        "submit_readings",
        {"device_id": account.id, "readings": {"67890": 43.5}},
        blocking=True,
    )

    portal_api.submit_readings.assert_called_once_with({"67890": 43.5})
    assert portal_api.fetch_account_and_billing.call_count == 2


async def test_submit_readings_service_reports_portal_rejection(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    portal_api.submit_readings.return_value = {
        "success": False,
        "messages": ["Reading rejected"],
    }
    await _setup_entry(hass, config_entry)
    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{config_entry.entry_id}"), config_entry.entry_id
    )
    assert account is not None

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "submit_readings",
            {"device_id": account.id, "readings": {"67890": 43.5}},
            blocking=True,
        )

    portal_api.submit_readings.assert_called_once_with({"67890": 43.5})
    portal_api.fetch_account_and_billing.assert_called_once()


async def test_services_require_account_selection_when_multiple_are_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda 54321",
        unique_id="test-login::54321",
        data={
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "54321",
        },
    )
    await _setup_entry(hass, config_entry)
    await _setup_entry(hass, second_entry)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_history",
            {"date_from": "01.01.2026", "date_to": "31.01.2026"},
            blocking=True,
        )


async def test_merged_legacy_account_device_requires_explicit_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Legacy identifiers can merge existing duplicate-account devices."""
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda duplicate 12345",
        unique_id="another-login::12345",
        data={
            CONF_LOGIN: "another-login",
            CONF_PASSWORD: "another-password",
            CONF_ACCOUNT_ID: "12345",
        },
    )
    await _setup_entry(hass, config_entry)
    await _setup_entry(hass, second_entry)

    account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "acc_12345"), config_entry.entry_id
    )
    assert account is not None
    # Modern HA keeps each device under a single config entry. Model the
    # legacy registry API explicitly to exercise compatibility with older HA.
    legacy_device = Mock(
        spec=["id", "config_entries"],
        id=account.id,
        config_entries={config_entry.entry_id, second_entry.entry_id},
    )
    legacy_registry = Mock()
    legacy_registry.async_get.return_value = legacy_device
    with monkeypatch.context() as legacy_patch:
        legacy_patch.setattr(dr, "async_get", lambda hass: legacy_registry)

        with pytest.raises(ValueError, match="multiple TGN Voda config entries"):
            get_coordinator(hass, device_id=account.id)

        with pytest.raises(HomeAssistantError):
            await hass.services.async_call(
                DOMAIN,
                "get_history",
                {
                    "device_id": account.id,
                    "date_from": "01.01.2026",
                    "date_to": "31.01.2026",
                },
                blocking=True,
            )

        await hass.services.async_call(
            DOMAIN,
            "get_history",
            {
                "device_id": account.id,
                "entry_id": second_entry.entry_id,
                "date_from": "01.01.2026",
                "date_to": "31.01.2026",
            },
            blocking=True,
        )
        portal_api.get_history.assert_called_once_with("01.01.2026", "31.01.2026")

        first_id, first_coordinator = get_coordinator(
            hass, device_id=account.id, entry_id=config_entry.entry_id
        )
        second_id, second_coordinator = get_coordinator(
            hass, device_id=account.id, entry_id=second_entry.entry_id
        )
        assert first_id == config_entry.entry_id
        assert first_coordinator is config_entry.runtime_data
        assert second_id == second_entry.entry_id
        assert second_coordinator is second_entry.runtime_data


@pytest.mark.parametrize("selector", ["device_id", "entry_id"])
async def test_services_route_to_selected_account_when_multiple_are_loaded(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    selector: str,
) -> None:
    second_entry = MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda 54321",
        unique_id="test-login::54321",
        data={
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "54321",
        },
    )
    first_data = portal_api.fetch_account_and_billing.return_value
    second_data = {
        **first_data,
        "account": {**first_data["account"], "current_account_id": "54321"},
        "billing": dict(first_data["billing"]),
        "counters": [dict(counter) for counter in first_data["counters"]],
    }
    portal_api.fetch_account_and_billing.side_effect = [first_data, second_data]
    await _setup_entry(hass, config_entry)
    await _setup_entry(hass, second_entry)

    second_account = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, f"account_{second_entry.entry_id}"), second_entry.entry_id
    )
    assert second_account is not None
    selector_value = second_account.id if selector == "device_id" else second_entry.entry_id
    received = []
    hass.bus.async_listen(EVENT_HISTORY, lambda event: received.append(event.data))

    await hass.services.async_call(
        DOMAIN,
        "get_history",
        {
            selector: selector_value,
            "date_from": "01.01.2026",
            "date_to": "31.01.2026",
        },
        blocking=True,
    )

    assert received == [
        {
            "items": [{"date": "2026-01-01", "value": 42.0}],
            "entry_id": second_entry.entry_id,
        }
    ]


async def test_services_fail_clearly_when_no_account_is_loaded(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    assert await hass.config_entries.async_unload(config_entry.entry_id)

    with pytest.raises(HomeAssistantError):
        await hass.services.async_call(
            DOMAIN,
            "get_history",
            {"date_from": "01.01.2026", "date_to": "31.01.2026"},
            blocking=True,
        )


async def test_diagnostics_redact_account_personal_data(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    await _setup_entry(hass, config_entry)
    from custom_components.tgn_voda.diagnostics import async_get_config_entry_diagnostics

    result = await async_get_config_entry_diagnostics(hass, config_entry)

    assert result["entry"][CONF_LOGIN] == "**REDACTED**"
    assert result["entry"][CONF_PASSWORD] == "**REDACTED**"
    assert result["data"]["account"]["email"] == "**REDACTED**"
    assert result["data"]["account"]["phone"] == "**REDACTED**"
    assert result["data"]["account"]["address"] == "**REDACTED**"
    assert result["data"]["account"]["current_account_id"] == "**REDACTED**"
    assert result["data"]["counters"][0]["id"] == "**REDACTED**"


async def test_coordinator_maps_bad_credentials_to_reauth(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    coordinator = TgnVodaCoordinator(hass, config_entry)
    portal_api.authenticate.side_effect = TgnVodaAuthenticationError("invalid credentials")

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_coordinator_maps_portal_errors_to_update_failed(
    hass: HomeAssistant, config_entry: MockConfigEntry, portal_api
) -> None:
    coordinator = TgnVodaCoordinator(hass, config_entry)
    portal_api.fetch_account_and_billing.side_effect = TgnVodaApiError("portal unavailable")

    with pytest.raises(UpdateFailed, match="API request failed"):
        await coordinator._async_update_data()


async def test_config_validation_closes_session_after_success(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.tgn_voda.config_flow import _validate

    api = Mock()
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow.TgnVodaApi",
        lambda *args, **kwargs: api,
    )

    await _validate(hass, config_entry.data)

    api.authenticate.assert_called_once_with()
    api.fetch_account_and_billing.assert_called_once_with()
    api.session.close.assert_called_once_with()


async def test_config_validation_closes_session_after_authentication_error(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from custom_components.tgn_voda.config_flow import _validate

    api = Mock()
    api.authenticate.side_effect = TgnVodaAuthenticationError("invalid credentials")
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow.TgnVodaApi",
        lambda *args, **kwargs: api,
    )

    with pytest.raises(TgnVodaAuthenticationError):
        await _validate(hass, config_entry.data)

    api.authenticate.assert_called_once_with()
    api.fetch_account_and_billing.assert_not_called()
    api.session.close.assert_called_once_with()


async def test_user_flow_validates_and_creates_entry(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch, portal_api
) -> None:
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate", AsyncMock(return_value=None)
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    assert result["type"] == "form"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "create_entry"
    assert result["title"] == "TGN Voda 12345"
    await hass.async_block_till_done()


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (TgnVodaAuthenticationError("invalid credentials"), "invalid_auth"),
        (TgnVodaApiError("portal unavailable"), "cannot_connect"),
    ],
)
async def test_user_flow_reports_validation_errors(
    hass: HomeAssistant,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_error: str,
) -> None:
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(side_effect=error),
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "wrong-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == expected_error


async def test_user_flow_reports_unexpected_errors_as_unknown(
    hass: HomeAssistant, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(side_effect=RuntimeError("unexpected implementation error")),
    )
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "unknown"


async def test_user_flow_aborts_duplicate_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_entry.add_to_hass(hass)
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr("custom_components.tgn_voda.config_flow._validate", validate)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "test-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    validate.assert_not_awaited()


async def test_user_flow_rejects_account_configured_with_another_login(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_entry.add_to_hass(hass)
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr("custom_components.tgn_voda.config_flow._validate", validate)

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "another-login",
            CONF_PASSWORD: "another-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "abort"
    assert result["reason"] == "already_configured"
    validate.assert_not_awaited()


async def test_reconfigure_updates_entry_and_reloads(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup_entry(hass, config_entry)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(return_value=None),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": config_entry.entry_id},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "updated-login",
            CONF_PASSWORD: "updated-password",
            CONF_ACCOUNT_ID: "54321",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 900,
        },
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reconfigure_successful"
    assert config_entry.unique_id == "updated-login::54321"
    assert config_entry.data[CONF_PASSWORD] == "updated-password"
    assert config_entry.options == {}
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (TgnVodaAuthenticationError("invalid credentials"), "invalid_auth"),
        (TgnVodaApiError("portal unavailable"), "cannot_connect"),
    ],
)
async def test_reconfigure_reports_validation_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_error: str,
) -> None:
    config_entry.add_to_hass(hass)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(side_effect=error),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": config_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "test-login",
            CONF_PASSWORD: "new-password",
            CONF_ACCOUNT_ID: "12345",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 900,
        },
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == expected_error
    assert config_entry.data[CONF_PASSWORD] == "test-password"


async def test_reconfigure_rejects_an_account_configured_by_another_entry(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_entry.add_to_hass(hass)
    duplicate = MockConfigEntry(
        domain=DOMAIN,
        title="TGN Voda 54321",
        unique_id="other-login::different-id",
        data={
            CONF_LOGIN: "other-login",
            CONF_PASSWORD: "other-password",
            CONF_ACCOUNT_ID: "54321",
        },
    )
    duplicate.add_to_hass(hass)
    validate = AsyncMock(return_value=None)
    monkeypatch.setattr("custom_components.tgn_voda.config_flow._validate", validate)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reconfigure", "entry_id": config_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_LOGIN: "other-login",
            CONF_PASSWORD: "other-password",
            CONF_ACCOUNT_ID: "54321",
            CONF_BASE_URL: "https://example.test",
            CONF_VERIFY_SSL: True,
            CONF_CA_BUNDLE: "",
            CONF_SCAN_INTERVAL: 1800,
        },
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == "already_configured"
    validate.assert_not_awaited()


async def test_reauth_flow_updates_password_and_reloads(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup_entry(hass, config_entry)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(return_value=None),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": config_entry.entry_id},
    )
    assert result["type"] == "form"
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "refreshed-password"}
    )

    assert result["type"] == "abort"
    assert result["reason"] == "reauth_successful"
    assert config_entry.data[CONF_PASSWORD] == "refreshed-password"
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (TgnVodaAuthenticationError("invalid credentials"), "invalid_auth"),
        (TgnVodaApiError("portal unavailable"), "cannot_connect"),
    ],
)
async def test_reauth_flow_reports_validation_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_error: str,
) -> None:
    await _setup_entry(hass, config_entry)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(side_effect=error),
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "reauth", "entry_id": config_entry.entry_id},
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "wrong-password"}
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == expected_error
    assert config_entry.data[CONF_PASSWORD] == "test-password"


async def test_options_flow_validates_and_saves_options(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    await _setup_entry(hass, config_entry)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(return_value=None),
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] == "form"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 900}
    )

    assert result["type"] == "create_entry"
    assert config_entry.options[CONF_SCAN_INTERVAL] == 900
    assert config_entry.state is ConfigEntryState.LOADED


@pytest.mark.parametrize(
    ("error", "expected_error"),
    [
        (TgnVodaAuthenticationError("invalid credentials"), "invalid_auth"),
        (TgnVodaApiError("portal unavailable"), "cannot_connect"),
    ],
)
async def test_options_flow_reports_validation_errors(
    hass: HomeAssistant,
    config_entry: MockConfigEntry,
    portal_api,
    monkeypatch: pytest.MonkeyPatch,
    error: Exception,
    expected_error: str,
) -> None:
    await _setup_entry(hass, config_entry)
    monkeypatch.setattr(
        "custom_components.tgn_voda.config_flow._validate",
        AsyncMock(side_effect=error),
    )

    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {CONF_SCAN_INTERVAL: 900}
    )

    assert result["type"] == "form"
    assert result["errors"]["base"] == expected_error
    assert config_entry.options == {}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(12, 12.0), ("12.5", 12.5), (None, None), ("not a number", None)],
)
def test_to_float_handles_portal_values(value, expected) -> None:
    assert to_float(value) == expected
