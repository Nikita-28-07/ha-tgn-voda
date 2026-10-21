from __future__ import annotations

import re
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import certifi
import requests
from bs4 import BeautifulSoup

API_RETRY_ATTEMPTS = 3
API_RETRY_DELAY = 1.0


class TgnVodaApiError(RuntimeError):
    """Raised for portal transport, HTTP, or response-format errors."""


class TgnVodaAuthenticationError(RuntimeError):
    """Raised when the portal rejects the configured credentials."""


def _money_to_float(value: str | None) -> float | None:
    if not value:
        return None
    match = re.search(r"[-+]?\d[\d\s\u00a0]*(?:[.,]\d+)?", value)
    if not match:
        return None
    try:
        return float(match.group().replace(" ", "").replace("\u00a0", "").replace(",", "."))
    except ValueError:
        return None


def _text(element) -> str | None:
    return element.get_text(" ", strip=True) if element is not None else None


def _csrf_from_html(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    element = soup.select_one('input[name="_token"]')
    if element and element.get("value"):
        return element["value"]
    element = soup.select_one('meta[name="csrf-token"]')
    return element.get("content") if element else None


class TgnVodaApi:
    def __init__(
        self,
        login: str,
        password: str,
        account_id: str,
        verify_ssl: bool | str | None = None,
        base_url: str = "https://lk.tgnvoda.ru",
    ) -> None:
        self.username = login
        self.password = password
        self.account_id = account_id
        self.base_url = base_url.rstrip("/")
        self.login_url = f"{self.base_url}/login"
        self.session = requests.Session()
        self.session.headers.update(
            {
                "User-Agent": "HomeAssistant/2025.8 tgn_voda",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": self.base_url,
                "Referer": self.login_url,
            }
        )
        if verify_ssl in (None, True):
            self.verify = certifi.where()
        elif verify_ssl in (False, "false", "False", 0, "0"):
            self.verify = False
            requests.packages.urllib3.disable_warnings()
        else:
            self.verify = str(verify_ssl)

    def _request(self, method: str, url: str, **kwargs):
        kwargs.setdefault("timeout", 20)
        kwargs.setdefault("verify", self.verify)
        method = method.upper()
        retryable_method = method == "GET"

        for attempt in range(API_RETRY_ATTEMPTS):
            try:
                response = self.session.request(method, url, **kwargs)
            except requests.exceptions.SSLError as err:
                raise TgnVodaApiError(f"TLS request to {url} failed: {err}") from err
            except (requests.Timeout, requests.ConnectionError) as err:
                if retryable_method and attempt < API_RETRY_ATTEMPTS - 1:
                    time.sleep(API_RETRY_DELAY * (2**attempt))
                    continue
                raise TgnVodaApiError(f"Request to {url} failed: {err}") from err
            except requests.RequestException as err:
                raise TgnVodaApiError(f"Request to {url} failed: {err}") from err

            status = getattr(response, "status_code", None)
            if retryable_method and status in {408, 429, 500, 502, 503, 504}:
                if attempt < API_RETRY_ATTEMPTS - 1:
                    time.sleep(self._retry_delay(response, attempt))
                    continue

            try:
                response.raise_for_status()
            except requests.HTTPError as err:
                raise TgnVodaApiError(f"Portal returned HTTP {status} for {url}") from err
            except requests.RequestException as err:
                raise TgnVodaApiError(f"Request to {url} failed: {err}") from err
            return response

        raise TgnVodaApiError(f"Request to {url} failed after {API_RETRY_ATTEMPTS} attempts")

    @staticmethod
    def _retry_delay(response, attempt: int) -> float:
        """Honor a numeric Retry-After header or use exponential backoff."""
        retry_after = getattr(response, "headers", {}).get("Retry-After")
        if retry_after:
            try:
                return max(0.0, min(float(retry_after), 30.0))
            except (TypeError, ValueError):
                try:
                    retry_at = parsedate_to_datetime(str(retry_after))
                    if retry_at.tzinfo is None:
                        retry_at = retry_at.replace(tzinfo=UTC)
                    delay = (retry_at - datetime.now(UTC)).total_seconds()
                    return max(0.0, min(delay, 30.0))
                except (TypeError, ValueError, OverflowError):
                    pass
        return min(API_RETRY_DELAY * (2**attempt), 30.0)

    @staticmethod
    def _json_response(response, url: str) -> Any:
        try:
            return response.json()
        except (ValueError, requests.RequestException) as err:
            raise TgnVodaApiError(f"Portal returned invalid JSON for {url}") from err

    @classmethod
    def _json_list_response(cls, response, url: str) -> list[Any]:
        """Parse an AJAX response that must contain a list of rows."""
        payload = cls._json_response(response, url)
        if not isinstance(payload, list):
            raise TgnVodaApiError(f"Portal returned an unexpected JSON structure for {url}")
        return payload

    def authenticate(self) -> None:
        response = self._request("GET", self.login_url, timeout=15)
        token = _csrf_from_html(response.text)
        if not token:
            raise TgnVodaApiError("CSRF token not found on login page")
        result = self._request(
            "POST",
            self.login_url,
            data={
                "_token": token,
                "login": self.username,
                "password": self.password,
            },
            allow_redirects=True,
        )
        if urlsplit(result.url).path.rstrip("/") == urlsplit(self.login_url).path.rstrip("/"):
            raise TgnVodaAuthenticationError("Authentication failed; check login and password")

    def fetch_account_and_billing(self) -> dict[str, Any]:
        response = self._request("GET", f"{self.base_url}/account/{self.account_id}")
        soup = BeautifulSoup(response.text, "html.parser")

        def icon_value(icon_class: str) -> str | None:
            icon = soup.select_one(f".mdi.{icon_class}")
            return _text(icon.parent) if icon and icon.parent else None

        pairs = {}
        for row in soup.select(".widget-right .widget-section2 .row"):
            key = _text(row.select_one(".text-col-left"))
            value = _text(row.select_one(".text-col-right"))
            if key and value:
                pairs[key] = value
        opening_label = next((key for key in pairs if key.lower().startswith("долг на")), None)
        opening_period = None
        if opening_label:
            match = re.search(r"начало\s+(.+)", opening_label, flags=re.I)
            if match:
                opening_period = match.group(1).strip().replace("'", " 20")
        payable = _money_to_float(_text(soup.select_one(".widget-right .widget-section3 a")))
        account = {
            "current_account_id": self.account_id,
            "holder_name": icon_value("mdi-account"),
            "address": icon_value("mdi-map-marker"),
            "phone": icon_value("mdi-phone"),
            "email": icon_value("mdi-email")
            or _text(soup.select_one(".navbar-user .profile-link span")),
        }
        billing = {
            "to_pay_now": payable,
            "currency": "RUB" if payable is not None else None,
            "opening_debt_period_label": opening_label,
            "opening_debt_amount": _money_to_float(pairs.get(opening_label))
            if opening_label
            else None,
            "accrued_in_period": _money_to_float(
                next(
                    (
                        v
                        for k, v in pairs.items()
                        if "начислено" in k.lower() and "пени" not in k.lower()
                    ),
                    None,
                )
            ),
            "recalculation": _money_to_float(
                next(
                    (
                        v
                        for k, v in pairs.items()
                        if "перерасчет" in k.lower() or "перерасчёт" in k.lower()
                    ),
                    None,
                )
            ),
            "penalty_accrued_in_period": _money_to_float(
                next((v for k, v in pairs.items() if "начислено пени" in k.lower()), None)
            ),
            "paid_amount": _money_to_float(
                next((v for k, v in pairs.items() if k.lower() == "оплачено"), None)
            ),
            "period": opening_period,
        }
        counters_response = self._request(
            "GET", f"{self.base_url}/account/{self.account_id}/counters"
        )
        counters = self._parse_counters(BeautifulSoup(counters_response.text, "html.parser"))
        return {"account": account, "billing": billing, "counters": counters}

    @staticmethod
    def _parse_counters(soup: BeautifulSoup) -> list[dict[str, Any]]:
        counters = []
        for block in soup.select("div[id^='counter_'].block-sch"):
            match = re.search(r"counter_(\d+)", block.get("id", ""))
            if not match:
                continue
            last = _text(block.select_one(".block-note.ml-auto.text-right"))
            number = re.search(r"\d+[.,]?\d*", last or "")
            counters.append(
                {
                    "id": match.group(1),
                    "name": _text(block.select_one(".block-title")) or match.group(1),
                    "last_value": float(number.group().replace(",", ".")) if number else None,
                }
            )
        return counters

    def _counters_form(self) -> dict[str, Any]:
        url = f"{self.base_url}/account/{self.account_id}/counters"
        response = self._request("GET", url)
        soup = BeautifulSoup(response.text, "html.parser")
        form = soup.select_one("form#sendCountersValues")
        if not form:
            raise TgnVodaApiError("Counters form not found in portal response")
        token_input = form.select_one('input[name="_token"]')
        token = token_input.get("value") if token_input else _csrf_from_html(response.text)
        if not token:
            raise TgnVodaApiError("Counters form CSRF token missing")
        result = []
        for block in soup.select("div[id^='counter_'].block-sch"):
            match = re.search(r"counter_(\d+)", block.get("id", ""))
            value = block.select_one("input[name^='counters'][name$='[value]']")
            row_id = block.select_one("input[name^='counters'][name$='[rowId]']")
            tariff = block.select_one("input[name^='counters'][name$='[tarif]']")
            result.append(
                {
                    "id": match.group(1) if match else None,
                    "value_name": value.get("name") if value else None,
                    "row_name": row_id.get("name") if row_id else None,
                    "tariff_name": tariff.get("name") if tariff else None,
                    "tariff": tariff.get("value", "0") if tariff else "0",
                    "last_value": None,
                }
            )
        return {"url": url, "token": token, "counters": result}

    def submit_readings(self, readings: dict[str, float]) -> dict[str, Any]:
        form = self._counters_form()
        payload = {"_token": form["token"]}
        applied = []
        for counter in form["counters"]:
            counter_id = counter["id"]
            if counter_id not in readings:
                continue
            value = str(readings[counter_id]).replace(",", ".")
            if counter["value_name"]:
                payload[counter["value_name"]] = value
            if counter["row_name"]:
                payload[counter["row_name"]] = counter_id
            if counter["tariff_name"]:
                payload[counter["tariff_name"]] = counter["tariff"]
            applied.append({"rowId": counter_id, "value": value})
        if not applied:
            raise ValueError("No counter IDs matched provided readings")
        response = self._request(
            "POST",
            form["url"],
            data=payload,
            allow_redirects=True,
            headers={"Referer": form["url"]},
        )
        alerts = [
            _text(alert) or ""
            for alert in BeautifulSoup(response.text, "html.parser").select(".alerts .alert")
        ]
        success = not any(
            any(word in message.lower() for word in ("ошиб", "некоррект")) for message in alerts
        )
        return {
            "applied": applied,
            "success": success,
            "messages": [message for message in alerts if message],
        }

    def get_history(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/ajax/{self.account_id}/countersHistory"
        response = self._request(
            "GET",
            url,
            params={"from": date_from, "to": date_to},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/account/{self.account_id}/counters",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        rows = self._json_list_response(response, url)
        output = []
        for row in rows:
            if not isinstance(row, (list, tuple)) or len(row) < 7:
                continue
            date_text = _text(BeautifulSoup(str(row[2]), "html.parser")) or str(row[2])
            period = _text(BeautifulSoup(str(row[3]), "html.parser")) or str(row[3])
            try:
                date_text = datetime.strptime(date_text, "%d.%m.%Y").date().isoformat()
            except ValueError:
                pass
            month = re.match(r"(\d{2})\.(\d{4})", period)
            output.append(
                {
                    "name": row[1],
                    "date": date_text,
                    "billing_month": f"{month.group(2)}-{month.group(1)}" if month else period,
                    "value": self._number(row[4]),
                    "consumption": self._number(row[5]),
                    "source": row[6],
                }
            )
        return output

    def get_payments_history(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/ajax/{self.account_id}/paymentsHistory"
        response = self._request(
            "GET",
            url,
            params={"from": date_from, "to": date_to},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/account/{self.account_id}",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        output = []
        for row in self._json_list_response(response, url):
            if not isinstance(row, (list, tuple)) or len(row) < 3:
                continue
            date_html, amount_text, source = row[0], row[1], row[2]
            date_text = _text(BeautifulSoup(str(date_html), "html.parser")) or str(date_html)
            date_match = re.search(r"\d{2}\.\d{2}\.\d{2,4}", date_text)
            amount = _money_to_float(str(amount_text))
            if not date_match or amount is None:
                continue
            try:
                fmt = "%d.%m.%y" if len(date_match.group().rsplit(".", 1)[-1]) == 2 else "%d.%m.%Y"
                iso_date = datetime.strptime(date_match.group(), fmt).date().isoformat()
            except ValueError:
                continue
            output.append({"date": iso_date, "amount": amount, "source": str(source)})
        return output

    def get_accruals_history(self, date_from: str, date_to: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/ajax/{self.account_id}/accrualsHistory"
        response = self._request(
            "GET",
            url,
            params={"from": date_from, "to": date_to},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/account/{self.account_id}",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        output = []
        for row in self._json_list_response(response, url):
            if not isinstance(row, (list, tuple)) or len(row) < 5:
                continue
            month_html = BeautifulSoup(str(row[0]), "html.parser")
            month_link = month_html.select_one("[data-sort]")
            sort_date = month_link.get("data-sort") if month_link else None
            try:
                billing_month = datetime.strptime(sort_date, "%d.%m.%Y").strftime("%Y-%m")
            except (TypeError, ValueError):
                continue
            amounts = [_money_to_float(str(value)) for value in row[1:5]]
            if any(amount is None for amount in amounts):
                continue
            output.append(
                {
                    "billing_month": billing_month,
                    "opening_balance": amounts[0],
                    "accrued_amount": amounts[1],
                    "payment_amount": amounts[2],
                    "closing_balance": amounts[3],
                }
            )
        return output

    def get_accruals_detalization(self, month: str) -> list[dict[str, Any]]:
        url = f"{self.base_url}/ajax/{self.account_id}/accrualsDetalization"
        response = self._request(
            "GET",
            url,
            params={"month": month},
            headers={
                "X-Requested-With": "XMLHttpRequest",
                "Referer": f"{self.base_url}/account/{self.account_id}",
                "Accept": "application/json, text/javascript, */*; q=0.01",
            },
        )
        output = []
        for row in self._json_list_response(response, url):
            if not isinstance(row, (list, tuple)) or len(row) < 9:
                continue
            amounts = [_money_to_float(str(value)) for value in row[1:8]]
            volume = self._number(row[8])
            if any(amount is None for amount in amounts) or not isinstance(volume, (int, float)):
                continue
            output.append(
                {
                    "service": str(row[0]),
                    "tariff": amounts[0],
                    "opening_balance": amounts[1],
                    "accrued_amount": amounts[2],
                    "recalculation": amounts[3],
                    "quality_writeoff": amounts[4],
                    "payments": amounts[5],
                    "closing_balance": amounts[6],
                    "volume": volume,
                }
            )
        return output

    def get_counter_info(self, counter_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/account/{self.account_id}/counters/{counter_id}/info"
        response = self._request("GET", url)
        soup = BeautifulSoup(response.text, "html.parser")
        device_info = soup.select_one("#device_info")
        values = soup.select_one("#device_values")
        if not device_info or not values:
            raise TgnVodaApiError(
                f"Counter information page is incomplete for counter {counter_id}"
            )

        normalized = {}
        label_keys = {
            "тип прибора учета": "meter_type",
            "разрядность": "digit_capacity",
            "тарифность": "tariff_count",
            "коэффициент трансформации": "transformation_coefficient",
            "дата поверки": "verification_date",
            "дата следующей поверки": "next_verification_date",
        }
        for row in values.select(".new-device-value"):
            label = _text(row.select_one(".device-value-label"))
            value = _text(row.select_one(".device-value-text"))
            if not label or not value:
                continue
            key = label_keys.get(label.casefold())
            if key:
                if key.endswith("_date"):
                    normalized[key] = self._date_to_iso(value)
                else:
                    normalized[key] = self._number(value)
        return normalized

    @staticmethod
    def _date_to_iso(value: str) -> str:
        for date_format in ("%d.%m.%Y", "%d.%m.%y"):
            try:
                return datetime.strptime(value, date_format).date().isoformat()
            except ValueError:
                continue
        return value

    @staticmethod
    def _number(value: Any) -> Any:
        try:
            return float(str(value).replace(",", "."))
        except (ValueError, TypeError):
            return value
