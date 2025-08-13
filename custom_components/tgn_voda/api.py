from __future__ import annotations
import re, requests
from datetime import datetime
from bs4 import BeautifulSoup
import certifi
from typing import Optional

BASE = "https://lk.tgnvoda.ru"
LOGIN_URL = f"{BASE}/login"

def _money_to_float(s: str | None) -> Optional[float]:
    if not s: return None
    m = re.search(r"[-+]?\d[\d\s]*[.,]?\d*", s)
    if not m: return None
    num = m.group(0).replace(" ", "").replace("\xa0", "").replace(",", ".")
    try: return float(num)
    except ValueError: return None

def _text(el):
    return el.get_text(strip=True) if el is not None else None

def _csrf_from_html(html: str) -> Optional[str]:
    soup = BeautifulSoup(html, "html.parser")
    inp = soup.select_one('input[name="_token"]')
    if inp and inp.get("value"): return inp["value"]
    meta = soup.find("meta", attrs={"name":"csrf-token"})
    if meta and meta.get("content"): return meta["content"]
    return None

class TgnVodaApi:
    def __init__(self, login: str, password: str, account_id: str, verify_ssl: bool | str | None = None) -> None:
        self.username = login
        self.password = password
        self.account_id = account_id
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": "HomeAssistant/2025.8 tgn_voda",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Origin": BASE,
            "Referer": LOGIN_URL,
        })
        if verify_ssl in (None, True):
            self.verify = certifi.where()
        elif verify_ssl in (False, "false", "False", 0, "0"):
            self.verify = False
            requests.packages.urllib3.disable_warnings()  # type: ignore
        else:
            self.verify = str(verify_ssl)

    def _rq(self, method: str, url: str, **kw):
        kw.setdefault("timeout", 20)
        kw.setdefault("verify", self.verify)
        resp = self.session.request(method, url, **kw)
        resp.raise_for_status()
        return resp

    def authenticate(self) -> None:
        r = self._rq("GET", LOGIN_URL, timeout=15)
        token = _csrf_from_html(r.text)
        if not token:
            raise RuntimeError("CSRF token not found on login page")
        self._rq("POST", LOGIN_URL, data={"_token": token, "login": self.username, "password": self.password}, allow_redirects=True)

    def fetch_account_and_billing(self) -> dict:
        res = self._rq("GET", f"{BASE}/account/{self.account_id}")
        soup = BeautifulSoup(res.text, "html.parser")

        def v_icon(cls):
            i = soup.select_one(f".mdi.{cls}")
            return _text(i.parent) if i and i.parent else None

        to_pay_link = soup.select_one(".widget-right .widget-section3 a")
        to_pay_now = _money_to_float(_text(to_pay_link))
        pairs = {}
        for row in soup.select(".widget-right .widget-section2 .row"):
            k = _text(row.select_one(".text-col-left"))
            v = _text(row.select_one(".text-col-right"))
            if k and v: pairs[k] = v

        opening_label = next((k for k in pairs if k.startswith("Долг на")), None)
        period = None
        if opening_label:
            m = re.search(r"начало\\s+(.+)", opening_label, flags=re.I)
            if m: period = m.group(1).strip().replace("'", " 20")

        account = {
            "current_account_id": self.account_id,
            "holder_name": v_icon("mdi-account"),
            "address": v_icon("mdi-map-marker"),
            "phone": v_icon("mdi-phone"),
            "email": v_icon("mdi-email") or _text(soup.select_one(".navbar-user .profile-link span")),
        }
        billing = {
            "to_pay_now": to_pay_now,
            "currency": "RUB" if to_pay_now is not None else None,
            "opening_debt_period_label": opening_label,
            "opening_debt_amount": _money_to_float(pairs.get(opening_label)) if opening_label else None,
            "accrued_in_period": _money_to_float(next((pairs[k] for k in pairs if "Начислено" in k and "пени" not in k), None)),
            "recalculation": _money_to_float(pairs.get("Перерасчет")),
            "penalty_accrued_in_period": _money_to_float(next((pairs[k] for k in pairs if "Начислено пени" in k), None)),
            "paid_amount": _money_to_float(pairs.get("Оплачено")),
            "period": period,
        }
        return {"account": account, "billing": billing}

    def _fetch_counters_form(self) -> dict:
        url = f"{BASE}/account/{self.account_id}/counters"
        r = self._rq("GET", url)
        soup = BeautifulSoup(r.text, "html.parser")

        form = soup.select_one("form#sendCountersValues")
        if not form:
            raise RuntimeError("Counters form not found")
        tok = form.select_one('input[name="_token"]')
        token = tok["value"] if tok and tok.get("value") else _csrf_from_html(r.text)
        if not token:
            raise RuntimeError("Counters form CSRF missing")

        counters = []
        for block in soup.select("div[id^='counter_'].block-sch"):
            cid = block.get("id") or ""
            m = re.search(r"counter_(\\d+)", cid)
            row_id = m.group(1) if m else None

            inp = block.select_one("input[name^='counters'][name$='[value]']")
            value_name = inp.get("name") if inp else None
            rowid_name = block.select_one("input[name^='counters'][name$='[rowId]']")
            tarif_name = block.select_one("input[name^='counters'][name$='[tarif]']")
            rowid_name = rowid_name.get("name") if rowid_name else None
            tarif_name = tarif_name.get("name") if tarif_name else None
            tarif_val = block.select_one("input[name^='counters'][name$='[tarif]']")
            tarif_val = (tarif_val.get("value") if tarif_val else "0") or "0"

            last_val = None
            last_el = block.select_one(".block-note.ml-auto.text-right")
            if last_el:
                m = re.search(r"\\d+[.,]?\\d*", last_el.get_text())
                if m: last_val = float(m.group(0).replace(",", "."))

            counters.append({
                "rowId": row_id,
                "value_input": value_name,
                "rowid_input": rowid_name,
                "tarif_input": tarif_name,
                "tarif_value": tarif_val,
                "last_value": last_val,
            })
        return {"url": url, "token": token, "counters": counters}

    def submit_readings(self, readings: dict[str, float]) -> dict:
        form = self._fetch_counters_form()
        payload = {"_token": form["token"]}
        applied = []
        for c in form["counters"]:
            rid = c["rowId"]
            if not rid or rid not in readings:
                continue
            val_str = str(readings[rid]).replace(",", ".")
            if c["value_input"]: payload[c["value_input"]] = val_str
            if c["rowid_input"] and c["rowId"]: payload[c["rowid_input"]] = c["rowId"]
            if c["tarif_input"]: payload[c["tarif_input"]] = c["tarif_value"]
            applied.append({"rowId": rid, "value": val_str, "last_value": c["last_value"]})

        if not applied:
            raise ValueError("No counters matched provided readings")

        resp = self._rq("POST", form["url"], data=payload, allow_redirects=True, headers={"Referer": form["url"]})
        soup = BeautifulSoup(resp.text, "html.parser")
        alerts = [(_text(a) or "") for a in soup.select(".alerts .alert")]
        success = not any(("ошиб" in (a or "").lower() or "некоррект" in (a or "").lower()) for a in alerts)
        return {"applied": applied, "success": success, "messages": [a for a in alerts if a]}

    def get_history(self, date_from: str, date_to: str) -> list[dict]:
        url = f"{BASE}/ajax/{self.account_id}/countersHistory"
        headers = {
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{BASE}/account/{self.account_id}/counters",
            "Accept": "application/json, text/javascript, */*; q=0.01",
        }
        resp = self._rq("GET", url, params={"from": date_from, "to": date_to}, headers=headers)
        rows = resp.json()
        out = []
        for row in rows:
            name = row[1]
            date_html = row[2]
            period_html = row[3]
            value = row[4]; cons = row[5]; source = row[6]
            ds = BeautifulSoup(date_html, "html.parser").get_text(strip=True)
            ps = BeautifulSoup(period_html, "html.parser").get_text(strip=True)
            try:
                iso = datetime.strptime(ds, "%d.%m.%Y").date().isoformat()
            except Exception:
                iso = ds
            mm = None
            m = re.match(r"(\\d{2})\\.(\\d{4})", ps)
            if m: mm = f"{m.group(2)}-{m.group(1)}"
            def num(x):
                try: return float(x)
                except Exception: return x
            out.append({"name": name, "date": iso, "billing_month": mm or ps, "value": num(value), "consumption": num(cons), "source": source})
        return out