"""Add demo network companies (counter-parties) to a Tranzact account.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md, then creates
three demo counter-parties via POST /profile/counter-party/create/, one
each for Supplier / Buyer / Both categories. The company names come from
data.md (SUPPLIER_COMPANY_NAME, BUYER_COMPANY_NAME, BOTH_COMPANY_NAME);
contact emails are derived as `<first_name>@<first_word_of_company>.test`.
"""

from __future__ import annotations

import logging
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any

import requests


# --- data.md loader ---------------------------------------------------------


def load_data_md(script_file: str) -> dict[str, Any]:
    """Load TOML inputs from data.md adjacent to this script."""
    data_path = Path(script_file).resolve().parent / "data.md"
    text = data_path.read_text(encoding="utf-8")
    match = re.search(r"```toml\s*\n(.*?)\n```", text, re.DOTALL)
    if not match:
        sys.exit(f"data.md at {data_path} has no ```toml block")
    return tomllib.loads(match.group(1))


DATA = load_data_md(__file__)

BASE_URL: str = DATA["BASE_URL"].rstrip("/")
TIMEOUT = 30

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Tz-Request-Source": "webapp",
}

ENDPOINT = "/profile/counter-party/create/"


def _email_for(first_name: str, company_name: str) -> str:
    return f"{first_name.lower()}@{company_name.split()[0].lower()}.test"


def _company(name: str, category: str, contact: dict, address: dict) -> dict:
    return {
        "name": name,
        "category": category,
        "contact": {**contact, "email": _email_for(contact["first_name"], name)},
        "address": address,
    }


COMPANIES: list[dict] = [
    _company(
        name=DATA["SUPPLIER_COMPANY_NAME"],
        category="Supplier",
        contact={
            "first_name": "Ramesh",
            "last_name": "Ramanathan",
            "phone": "980980980",
        },
        address={
            "address1": "Central Corporate Park",
            "address2": "Ravivar Peth",
            "city": "Nashik",
            "state": "Maharashtra",
            "pin": "422001",
        },
    ),
    _company(
        name=DATA["BUYER_COMPANY_NAME"],
        category="Buyer",
        contact={
            "first_name": "Suresh",
            "last_name": "Suryavanshi",
            "phone": "9879879870",
        },
        address={
            "address1": "Best Colony, Opp. Colaba Causeway",
            "address2": "Annie Basent Road",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pin": "400002",
        },
    ),
    _company(
        name=DATA["BOTH_COMPANY_NAME"],
        category="Both",
        contact={
            "first_name": "Naresh",
            "last_name": "Narayan",
            "phone": "8978978970",
        },
        address={
            "address1": "99 Corporate Park",
            "address2": "Nr. Marol Metro Station, Andheri",
            "city": "Mumbai",
            "state": "Maharashtra",
            "pin": "400057",
        },
    ),
]


# --- Logging ----------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("automation")

# --- Throttle-aware retry (per-call backoff on HTTP 429) --------------------

_THROTTLE_RETRIES = 4
_THROTTLE_BACKOFF = [3, 6, 12, 24]


def _throttle_sleep(resp, method, path, attempt):
    """Back off before retrying a rate-limited (429) request. Honors Retry-After."""
    wait = _THROTTLE_BACKOFF[min(attempt, len(_THROTTLE_BACKOFF) - 1)]
    retry_after = resp.headers.get("Retry-After")
    if retry_after:
        try:
            wait = max(wait, int(float(retry_after)) + 2)
        except (TypeError, ValueError):
            pass
    log.info("    throttled (429) on %s %s -> backoff %ss, retry %d/%d",
             method, path, wait, attempt + 1, _THROTTLE_RETRIES)
    time.sleep(wait)


def _send(method, url, path_label, **kwargs):
    """Issue a request, retrying the single call on HTTP 429 with backoff."""
    for _tt in range(_THROTTLE_RETRIES + 1):
        resp = requests.request(method, url, **kwargs)
        if resp.status_code != 429 or _tt == _THROTTLE_RETRIES:
            return resp
        _throttle_sleep(resp, method.upper(), path_label, _tt)


# --- Auth -------------------------------------------------------------------


def login() -> str:
    """POST /main/login/password-login/ → access token. Inlined; replaces core.auth.ensure_authenticated."""
    url = f"{BASE_URL}/main/login/password-login/"
    log.info(">>> POST /main/login/password-login/")
    response = _send(
        "POST",
        url,
        "/main/login/password-login/",
        json={"email": DATA["EMAIL"], "password": DATA["PASSWORD"]},
        headers=DEFAULT_HEADERS,
        timeout=TIMEOUT,
    )
    log.info("<<< POST /main/login/password-login/ -> %d", response.status_code)
    if response.status_code >= 400:
        sys.exit(f"Login failed (HTTP {response.status_code}): {response.text[:300]}")
    data = (response.json() or {}).get("data") or {}
    token = data.get("access_token") or data.get("access")
    if not token:
        sys.exit(f"Login response missing access token. Keys: {list(data.keys())}")
    log.info("Login complete; access token captured.")
    return token


# --- Business flow ----------------------------------------------------------


def build_payload(company: dict) -> dict:
    name = company["name"]
    contact = company["contact"]
    addr = company["address"]
    full_name = f"{contact['first_name']} {contact['last_name']}".strip()

    address_details = {
        "gstin": "",
        "address1": addr["address1"],
        "address2": addr["address2"],
        "pin": addr["pin"],
        "city": addr["city"],
        "state": addr["state"],
        "country": "India",
        "name": name,
        "gstin_type": "Regular",
        "email": contact["email"],
        "tags": [],
        "additional_email": "",
        "landline_no": "",
        "fax_no": "",
    }

    location_details = {
        "gstin": "",
        "address1": addr["address1"],
        "address2": addr["address2"],
        "pin": addr["pin"],
        "city": addr["city"],
        "state": addr["state"],
        "country": "India",
        "name": name,
        "gstintype": "Regular",
    }

    return {
        "company_details": {
            "name": name,
            "email": contact["email"],
            "tags": [],
            "sector": "",
            "ownership": "",
            "mobile_no": contact["phone"],
        },
        "user_details": {
            "name": full_name,
            "email": contact["email"],
            "contact_no": contact["phone"],
            "first_name": contact["first_name"],
            "last_name": contact["last_name"],
        },
        "address_details": address_details,
        "delivery_location_details": location_details,
        "billing_address_details": location_details,
        "network_details": {
            "category": company["category"],
            "cp_reference_id": "",
            "custom_fields": {},
        },
        "attached_tags": [],
    }


def create_counter_party(token: str, company: dict) -> dict:
    payload = build_payload(company)
    url = f"{BASE_URL}{ENDPOINT}"
    headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}
    log.info(">>> POST %s  (%s — %s)", ENDPOINT, company["name"], company["category"])
    response = _send("POST", url, ENDPOINT, json=payload, headers=headers, timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", ENDPOINT, response.status_code)
    if response.status_code >= 400:
        sys.exit(
            f"Counter-party '{company['name']}' creation failed "
            f"(HTTP {response.status_code}): {response.text[:300]}"
        )
    try:
        return response.json()
    except ValueError:
        return {"status_code": response.status_code, "text": response.text}


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Creating %d network companies ===", len(COMPANIES))
    for company in COMPANIES:
        create_counter_party(token, company)
        log.info("Created: %s (%s)", company["name"], company["category"])

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
