from __future__ import annotations

import json
from pathlib import Path

import pytest
from tgn_voda_api import TgnVodaApi

CONFIG_PATH = Path(__file__).with_name("live_config.json")


def print_result(title: str, result) -> None:
    print(f"\n--- {title} ---")
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))


@pytest.fixture(scope="module")
def settings():
    if not CONFIG_PATH.exists():
        pytest.skip("Create tests/live_config.json to run live portal checks")
    data = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    required = ("login", "password", "account_id")
    missing = [key for key in required if not data.get(key) or str(data[key]).startswith("YOUR_")]
    if missing:
        pytest.skip("Fill in tests/live_config.json: " + ", ".join(missing))
    return data


@pytest.fixture
def live_api(settings):
    api = TgnVodaApi(
        login=settings["login"],
        password=settings["password"],
        account_id=str(settings["account_id"]),
        verify_ssl=settings.get("verify_ssl", True),
        base_url=settings.get("base_url", "https://lk.tgnvoda.ru"),
    )
    try:
        api.authenticate()
        yield api
    finally:
        api.session.close()


def test_live_account_billing_and_counters(live_api, settings):
    result = live_api.fetch_account_and_billing()
    print_result("Данные аккаунта", result["account"])
    print_result("Начисления", result["billing"])
    print_result("Счётчики", result["counters"])
    assert result["account"]["current_account_id"] == str(settings["account_id"])
    assert set(result["billing"]) >= {
        "to_pay_now",
        "accrued_in_period",
        "paid_amount",
        "opening_debt_amount",
        "recalculation",
        "penalty_accrued_in_period",
    }
    assert isinstance(result["counters"], list)
    for counter in result["counters"]:
        assert counter.get("id")


def test_live_counters_form(live_api):
    form = live_api._counters_form()
    # Не выводим CSRF-токен: он является частью активной сессии сайта.
    print_result("Форма счётчиков", {"counters": form["counters"]})
    assert form["token"]
    assert isinstance(form["counters"], list)


def test_live_meter_history(live_api, settings):
    result = live_api.get_history(
        settings.get("date_from", "01.07.2025"), settings.get("date_to", "31.08.2025")
    )
    print_result("История показаний", result)
    assert isinstance(result, list)
    for item in result:
        assert {"name", "date", "billing_month", "value", "consumption", "source"} <= item.keys()


def test_live_payments_history(live_api, settings):
    result = live_api.get_payments_history(
        settings.get("date_from", "01.07.2025"), settings.get("date_to", "31.08.2025")
    )
    print_result("История оплат", result)
    assert isinstance(result, list)
    for item in result:
        assert {"date", "amount", "source"} <= item.keys()


def test_live_accruals_history(live_api, settings):
    result = live_api.get_accruals_history(
        settings.get("accruals_date_from", "01.10.2025"),
        settings.get("accruals_date_to", "31.10.2026"),
    )
    print_result("История начислений", result)
    assert isinstance(result, list)
    for item in result:
        assert {
            "billing_month",
            "opening_balance",
            "accrued_amount",
            "payment_amount",
            "closing_balance",
        } <= item.keys()


def test_live_accruals_detalization(live_api, settings):
    month = settings.get("accruals_month", "01.10.2026")
    result = live_api.get_accruals_detalization(month)
    print_result(f"Детализация начислений за {month}", result)
    assert isinstance(result, list)
    for item in result:
        assert {
            "service",
            "tariff",
            "opening_balance",
            "accrued_amount",
            "recalculation",
            "quality_writeoff",
            "payments",
            "closing_balance",
            "volume",
        } <= item.keys()


def test_live_counter_info(live_api, settings):
    counter_id = settings.get("counter_id")
    if isinstance(counter_id, str) and counter_id.startswith("YOUR_"):
        counter_id = None
    if not counter_id:
        account = live_api.fetch_account_and_billing()
        if not account["counters"]:
            pytest.skip("No meter IDs found; set counter_id in tests/live_config.json")
        counter_id = account["counters"][0]["id"]
    result = live_api.get_counter_info(str(counter_id))
    print_result(f"Информация о счётчике {counter_id}", result)
    assert set(result) == {
        "meter_type",
        "digit_capacity",
        "tariff_count",
        "transformation_coefficient",
        "verification_date",
        "next_verification_date",
    }


def test_live_submit_readings(live_api, settings):
    if not settings.get("allow_submit_readings"):
        pytest.skip("Set allow_submit_readings=true and configure readings to send meter values")
    readings = settings.get("readings") or {}
    if not readings:
        pytest.skip("Add meter IDs and values to readings before enabling submission")
    result = live_api.submit_readings({str(key): value for key, value in readings.items()})
    print_result("Результат отправки показаний", result)
    assert result["applied"], "No configured meter IDs matched the account"
    assert result["success"], result.get("messages")
