from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import Mock

import pytest
import requests
import tgn_voda_api as api_module
from tgn_voda_api import TgnVodaApi, _csrf_from_html, _money_to_float


class FakeResponse:
    def __init__(
        self,
        text="",
        *,
        url="",
        payload=None,
        status_code=200,
        headers=None,
        json_error=None,
    ):
        self.text = text
        self.url = url
        self.payload = payload
        self.status_code = status_code
        self.headers = headers or {}
        self.json_error = json_error

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"HTTP {self.status_code}")

    def json(self):
        if self.json_error is not None:
            raise self.json_error
        return self.payload


@pytest.fixture
def api():
    client = TgnVodaApi("login", "password", "12345", base_url="https://example.test")
    try:
        yield client
    finally:
        client.session.close()


def test_money_parser_handles_russian_amounts():
    assert _money_to_float("1 234,56 ₽") == 1234.56
    assert _money_to_float("1\u00a0234.00") == 1234.0
    assert _money_to_float("нет данных") is None


def test_csrf_parser_reads_form_token():
    assert _csrf_from_html('<input name="_token" value="abc123">') == "abc123"
    assert _csrf_from_html('<meta name="csrf-token" content="meta123">') == "meta123"
    assert _csrf_from_html("<html></html>") is None


def test_authenticate_gets_token_and_posts_credentials(api):
    api._request = Mock(
        side_effect=[
            FakeResponse('<input name="_token" value="csrf">'),
            FakeResponse(url="https://example.test/account/12345"),
        ]
    )
    api.authenticate()
    assert api._request.call_count == 2
    assert api._request.call_args_list[1].kwargs["data"] == {
        "_token": "csrf",
        "login": "login",
        "password": "password",
    }


def test_authenticate_classifies_missing_csrf_as_portal_error(api):
    api._request = Mock(return_value=FakeResponse("<html>Maintenance</html>"))

    with pytest.raises(api_module.TgnVodaApiError, match="CSRF token not found"):
        api.authenticate()


def test_authenticate_rejects_login_redirect(api):
    api._request = Mock(
        side_effect=[
            FakeResponse('<input name="_token" value="csrf">'),
            FakeResponse(url="https://example.test/login"),
        ]
    )
    with pytest.raises(api_module.TgnVodaAuthenticationError, match="Authentication failed"):
        api.authenticate()


def test_authenticate_accepts_account_url_with_login_query_parameter(api):
    api._request = Mock(
        side_effect=[
            FakeResponse('<input name="_token" value="csrf">'),
            FakeResponse(url="https://example.test/account/12345?next=/login"),
        ]
    )

    api.authenticate()


def test_get_request_wraps_non_transient_request_errors(api, monkeypatch):
    api.session.request = Mock(side_effect=requests.TooManyRedirects("redirect loop"))
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    with pytest.raises(api_module.TgnVodaApiError, match="redirect loop"):
        api._request("GET", "https://example.test/data")

    api.session.request.assert_called_once()


def test_get_request_retries_transient_connection_failure(api, monkeypatch):
    api.session.request = Mock(side_effect=[requests.Timeout("temporary"), FakeResponse("ok")])
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    response = api._request("GET", "https://example.test/data")

    assert response.text == "ok"
    assert api.session.request.call_count == 2


def test_get_request_retries_temporary_server_error(api, monkeypatch):
    api.session.request = Mock(
        side_effect=[
            FakeResponse(status_code=503),
            FakeResponse("ok"),
        ]
    )
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    response = api._request("GET", "https://example.test/data")

    assert response.text == "ok"
    assert api.session.request.call_count == 2


def test_get_request_retries_http_timeout_status(api, monkeypatch):
    api.session.request = Mock(side_effect=[FakeResponse(status_code=408), FakeResponse("ok")])
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    response = api._request("GET", "https://example.test/data")

    assert response.text == "ok"
    assert api.session.request.call_count == 2


def test_get_request_honors_retry_after_header(api, monkeypatch):
    api.session.request = Mock(
        side_effect=[
            FakeResponse(status_code=429, headers={"Retry-After": "7"}),
            FakeResponse("ok"),
        ]
    )
    sleep = Mock()
    monkeypatch.setattr(api_module.time, "sleep", sleep)

    api._request("GET", "https://example.test/data")

    sleep.assert_called_once_with(7.0)


@pytest.mark.parametrize(
    ("retry_after", "expected_delay"),
    [
        ("Wed, 21 Oct 2015 07:28:00 GMT", 0.0),
        ("Thu, 01 Jan 2026 00:00:31 GMT", 30.0),
    ],
)
def test_get_request_honors_http_date_retry_after(api, monkeypatch, retry_after, expected_delay):
    class FrozenDateTime:
        @staticmethod
        def now(tz=None):
            return datetime(2026, 1, 1, tzinfo=UTC)

    monkeypatch.setattr(api_module, "datetime", FrozenDateTime)
    api.session.request = Mock(
        side_effect=[
            FakeResponse(status_code=429, headers={"Retry-After": retry_after}),
            FakeResponse("ok"),
        ]
    )
    sleep = Mock()
    monkeypatch.setattr(api_module.time, "sleep", sleep)

    api._request("GET", "https://example.test/data")

    sleep.assert_called_once_with(expected_delay)


def test_get_request_retries_are_bounded(api, monkeypatch):
    api.session.request = Mock(return_value=FakeResponse(status_code=503))
    sleep = Mock()
    monkeypatch.setattr(api_module.time, "sleep", sleep)

    with pytest.raises(api_module.TgnVodaApiError, match="HTTP 503"):
        api._request("GET", "https://example.test/data")

    assert api.session.request.call_count == api_module.API_RETRY_ATTEMPTS
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]


def test_post_request_is_not_retried(api, monkeypatch):
    api.session.request = Mock(side_effect=requests.ConnectionError("temporary"))
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    with pytest.raises(api_module.TgnVodaApiError):
        api._request("POST", "https://example.test/submit")

    api.session.request.assert_called_once()


def test_get_request_does_not_retry_client_error(api, monkeypatch):
    api.session.request = Mock(return_value=FakeResponse(status_code=404))
    monkeypatch.setattr(api_module.time, "sleep", Mock())

    with pytest.raises(api_module.TgnVodaApiError, match="HTTP 404"):
        api._request("GET", "https://example.test/missing")

    api.session.request.assert_called_once()


def test_fetch_account_billing_and_counters(api):
    account_html = """
      <i class="mdi mdi-account"></i><span>Иван Иванов</span>
      <div class="widget-right"><div class="widget-section3"><a>1 234,50 ₽</a></div>
      <div class="widget-section2"><div class="row">
        <span class="text-col-left">Начислено</span><span class="text-col-right">500,00</span>
      </div></div></div>
    """
    counters_html = """<div id="counter_789" class="block-sch">
      <span class="block-title">Холодная вода</span>
      <span class="block-note ml-auto text-right">Последнее показание 15,2</span></div>"""
    api._request = Mock(side_effect=[FakeResponse(account_html), FakeResponse(counters_html)])
    result = api.fetch_account_and_billing()
    assert result["account"]["current_account_id"] == "12345"
    assert result["billing"]["to_pay_now"] == 1234.5
    assert result["billing"]["accrued_in_period"] == 500
    assert result["counters"][0]["id"] == "789"


def test_counters_form_extracts_submission_fields(api):
    html = """<form id="sendCountersValues"><input name="_token" value="token"></form>
      <div id="counter_789" class="block-sch">
      <input name="counters[0][value]"><input name="counters[0][rowId]" value="789">
      <input name="counters[0][tarif]" value="1"></div>"""
    api._request = Mock(return_value=FakeResponse(html))
    form = api._counters_form()
    assert form["token"] == "token"
    assert form["counters"][0]["id"] == "789"
    assert form["counters"][0]["value_name"] == "counters[0][value]"


def test_counters_form_reports_unexpected_portal_response(api):
    api._request = Mock(return_value=FakeResponse("<html>Unavailable</html>"))

    with pytest.raises(api_module.TgnVodaApiError, match="Counters form not found"):
        api._counters_form()


def test_submit_readings_posts_only_matching_counter(api):
    api._counters_form = Mock(
        return_value={
            "url": "https://example.test/counters",
            "token": "csrf",
            "counters": [
                {
                    "id": "789",
                    "value_name": "counters[0][value]",
                    "row_name": "counters[0][rowId]",
                    "tariff_name": "counters[0][tarif]",
                    "tariff": "1",
                }
            ],
        }
    )
    api._request = Mock(
        return_value=FakeResponse('<div class="alerts"><div class="alert">Сохранено</div></div>')
    )
    result = api.submit_readings({"789": 16.4, "other": 10})
    assert result["success"] is True
    assert result["applied"] == [{"rowId": "789", "value": "16.4"}]
    assert api._request.call_args.kwargs["data"] == {
        "_token": "csrf",
        "counters[0][value]": "16.4",
        "counters[0][rowId]": "789",
        "counters[0][tarif]": "1",
    }


def test_submit_readings_rejects_unmatched_counter(api):
    api._counters_form = Mock(return_value={"url": "x", "token": "t", "counters": []})
    with pytest.raises(ValueError, match="No counter IDs matched"):
        api.submit_readings({"missing": 1})


def test_get_history_parses_portal_rows(api):
    rows = [
        [
            0,
            "Холодная вода",
            "<span>10.07.2025</span>",
            "<span>07.2025</span>",
            "123,4",
            "5,2",
            "Личный кабинет",
        ]
    ]
    api._request = Mock(return_value=FakeResponse(payload=rows))
    result = api.get_history("01.07.2025", "31.07.2025")
    assert result == [
        {
            "name": "Холодная вода",
            "date": "2025-07-10",
            "billing_month": "2025-07",
            "value": 123.4,
            "consumption": 5.2,
            "source": "Личный кабинет",
        }
    ]


def test_get_history_classifies_invalid_json_as_api_error(api):
    api._request = Mock(return_value=FakeResponse(json_error=ValueError("invalid JSON")))

    with pytest.raises(api_module.TgnVodaApiError, match="invalid JSON"):
        api.get_history("01.07.2025", "31.07.2025")


def test_get_history_skips_malformed_rows(api):
    api._request = Mock(return_value=FakeResponse(payload=[{"unexpected": "row structure"}]))

    assert api.get_history("01.07.2025", "31.07.2025") == []


@pytest.mark.parametrize(
    ("method", "args"),
    [
        ("get_history", ("01.01.2026", "31.01.2026")),
        ("get_payments_history", ("01.01.2026", "31.01.2026")),
        ("get_accruals_history", ("01.01.2026", "31.01.2026")),
        ("get_accruals_detalization", ("01.01.2026",)),
    ],
)
def test_history_methods_reject_unexpected_json_structure(api, method, args):
    api._request = Mock(return_value=FakeResponse(payload={"error": "session expired"}))

    with pytest.raises(api_module.TgnVodaApiError, match="unexpected JSON structure"):
        getattr(api, method)(*args)


def test_get_payments_history_parses_table(api):
    rows = [
        ['<span data-sort="14.10.26">14.10.26</span>', "584.90", "Личный кабинет"],
        ['<span data-sort="05.10.25">05.10.25</span>', "1 052.46", "Личный кабинет"],
    ]
    api._request = Mock(return_value=FakeResponse(payload=rows))
    result = api.get_payments_history("01.08.2025", "31.08.2025")
    assert result == [
        {"date": "2026-10-14", "amount": 584.9, "source": "Личный кабинет"},
        {"date": "2025-10-05", "amount": 1052.46, "source": "Личный кабинет"},
    ]
    method, url = api._request.call_args.args
    assert method == "GET"
    assert url.endswith("/ajax/12345/paymentsHistory")
    assert api._request.call_args.kwargs["params"] == {"from": "01.08.2025", "to": "31.08.2025"}


def test_get_accruals_history_parses_month_and_balances(api):
    rows = [
        ['<a data-sort="01.10.2026">Октябрь\'26</a>', "209.26", "375.64", "584.90", "0.00"],
        ['<a data-sort="01.09.2026">Сентябрь\'26</a>', "0.00", "760.12", "550.86", "209.26"],
        ['<a data-sort="01.06.2026">Июнь\'26</a>', "-99.69", "267.08", "241.44", "-74.05"],
    ]
    api._request = Mock(return_value=FakeResponse(payload=rows))
    result = api.get_accruals_history("01.10.2025", "31.10.2026")
    assert result == [
        {
            "billing_month": "2026-10",
            "opening_balance": 209.26,
            "accrued_amount": 375.64,
            "payment_amount": 584.9,
            "closing_balance": 0.0,
        },
        {
            "billing_month": "2026-09",
            "opening_balance": 0.0,
            "accrued_amount": 760.12,
            "payment_amount": 550.86,
            "closing_balance": 209.26,
        },
        {
            "billing_month": "2026-06",
            "opening_balance": -99.69,
            "accrued_amount": 267.08,
            "payment_amount": 241.44,
            "closing_balance": -74.05,
        },
    ]
    method, url = api._request.call_args.args
    assert method == "GET"
    assert url.endswith("/ajax/12345/accrualsHistory")
    assert api._request.call_args.kwargs["params"] == {"from": "01.10.2025", "to": "31.10.2026"}


def test_get_accruals_detalization_parses_service_row(api):
    rows = [
        [
            "Холодное водоснабжение",
            "73.31",
            "209.26",
            "375.64",
            "0.00",
            "0.00",
            "584.90",
            "0.00",
            5.123999999999999666,
        ]
    ]
    api._request = Mock(return_value=FakeResponse(payload=rows))
    result = api.get_accruals_detalization("01.10.2026")
    assert result == [
        {
            "service": "Холодное водоснабжение",
            "tariff": 73.31,
            "opening_balance": 209.26,
            "accrued_amount": 375.64,
            "recalculation": 0.0,
            "quality_writeoff": 0.0,
            "payments": 584.9,
            "closing_balance": 0.0,
            "volume": 5.124,
        }
    ]
    method, url = api._request.call_args.args
    assert method == "GET"
    assert url.endswith("/ajax/12345/accrualsDetalization")
    assert api._request.call_args.kwargs["params"] == {"month": "01.10.2026"}


def test_get_counter_info_parses_meter_metadata(api):
    html = """
      <div id="device_info">
        <span class="device-info-text">Холодное водоснабжение</span>
        <span class="device-info-text">№5022841 16</span>
        <form name="sendCounterValue"><input name="counters[484441_0][value]"
          data-indication="27075285" data-tariff="0"></form>
        <div class="block-note">от 25.09.26</div>
        <div class="block-note ml-auto text-right">668</div>
      </div>
      <div id="device_values">
        <div class="row new-device-value">
          <span class="device-value-label">Тип прибора учета</span>
          <span class="device-value-text">НОРМА СВКМ</span></div>
        <div class="row new-device-value">
          <span class="device-value-label">Разрядность</span>
          <span class="device-value-text">6</span></div>
        <div class="row new-device-value">
          <span class="device-value-label">Тарифность</span>
          <span class="device-value-text">1</span></div>
        <div class="row new-device-value">
          <span class="device-value-label">Коэффициент трансформации</span>
          <span class="device-value-text">1</span></div>
        <div class="row new-device-value">
          <span class="device-value-label">Дата поверки</span>
          <span class="device-value-text">01.02.2023</span></div>
        <div class="row new-device-value">
          <span class="device-value-label">Дата следующей поверки</span>
          <span class="device-value-text">01.02.2029</span></div>
      </div>
    """
    api._request = Mock(return_value=FakeResponse(html))
    result = api.get_counter_info("484441")
    assert result == {
        "meter_type": "НОРМА СВКМ",
        "digit_capacity": 6.0,
        "tariff_count": 1.0,
        "transformation_coefficient": 1.0,
        "verification_date": "2023-02-01",
        "next_verification_date": "2029-02-01",
    }
    assert api._request.call_args.args == (
        "GET",
        "https://example.test/account/12345/counters/484441/info",
    )


def test_get_counter_info_reports_incomplete_portal_page(api):
    api._request = Mock(return_value=FakeResponse("<html>Unavailable</html>"))

    with pytest.raises(api_module.TgnVodaApiError, match="page is incomplete"):
        api.get_counter_info("484441")
