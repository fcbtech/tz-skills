"""Register a new Tranzact company end-to-end (signup → onboarding → masters).

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Flow:
  Phase 1: Signup (POST /main/login/signup/) — capture access/refresh tokens.
  Phase 2: Onboarding chain:
    - GET  /settings/onboarding-data/do-basic-setup/
    - POST /profile/company-profile/update/
    - POST /profile/user-profile/update-personal-info/   (onboarding: true)
    - GET  /profile/info/fetch/                          (verification)
    - POST /settings/onboarding-data/activate-initial-modules/
    - POST /subscription/subscription/free-trial/        (activate 5-day free trial)
  Phase 3: Masters (v3 settings):
    - POST /api/v3/settings/billing-addresses/
    - POST /api/v3/settings/delivery-locations/
    - POST /api/v3/settings/banks/
  Phase 4: Print credentials.
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
ORIGIN = ""
REFERER = ""
TIMEOUT = 30

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Tz-Request-Source": "webapp",
}

# Fixed values for this use case — edit here if a one-off run needs different settings.
SECTOR = "Manufacturing"
IS_MANUFACTURING = "Yes"
CREATE_EINVOICE = "Yes"
POC_DESIGNATION = "Owner / Director"
USER_DESIGNATION = "Owner / Director"
MOBILE_CONSENT = False
ADDRESS_NAME = "Office"
GSTINTYPE = "Regular"
GSTIN = ""
BANK_ACCOUNT_NO = "243432434"
BANK_NAME = "Kotak Bank"
BANK_IFSC = "KBR3423534"


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


# --- Session state ----------------------------------------------------------


class _Session:
    access_token: str = ""
    refresh_token: str = ""


SESSION = _Session()


# --- HTTP helpers -----------------------------------------------------------


def _headers(authed: bool) -> dict[str, str]:
    h = {**DEFAULT_HEADERS}
    if ORIGIN:
        h["Origin"] = ORIGIN
    if REFERER:
        h["Referer"] = REFERER
    if authed:
        if not SESSION.access_token:
            sys.exit("No access token — signup must run first.")
        h["Authorization"] = f"Bearer {SESSION.access_token}"
    return h


def call(method: str, path: str, payload: dict | None = None, authed: bool = True) -> dict:
    method = method.upper()
    url = f"{BASE_URL}{path}"
    log.info(">>> %s %s", method, path)
    response = _send(
        method,
        url,
        path,
        json=payload if method in {"POST", "PUT", "PATCH"} else None,
        headers=_headers(authed),
        timeout=TIMEOUT,
    )
    log.info("<<< %s %s -> %d", method, path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"{method} {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    try:
        return response.json()
    except ValueError:
        return {"status_code": response.status_code, "text": response.text}


# --- Phase 1: Signup --------------------------------------------------------


def signup(email: str, password: str) -> dict:
    response = call(
        "POST",
        "/main/login/signup/",
        payload={"email": email, "password": password, "type": "signup", "join_token": None},
        authed=False,
    )
    data = response.get("data") or {}
    access = data.get("access_token") or data.get("access")
    refresh = data.get("refresh_token") or data.get("refresh")
    if not access or not refresh:
        sys.exit(f"Signup response missing tokens. Keys: {list(data.keys())}")
    SESSION.access_token = access
    SESSION.refresh_token = refresh
    log.info("Signup complete; tokens captured.")
    return data


# --- Phase 2: Onboarding ----------------------------------------------------


def update_company_profile() -> dict:
    return call(
        "POST",
        "/profile/company-profile/update/",
        payload={
            "name": DATA["COMPANY_NAME"],
            "industry": "",
            "sector": SECTOR,
            "address1": DATA["ADDRESS1"],
            "pin": DATA["PIN"],
            "city": DATA["CITY"],
            "state": DATA["STATE"],
            "country": DATA["COUNTRY"],
            "company_meta_data": {
                "is_manufacturing": IS_MANUFACTURING,
                "create_einvoice": CREATE_EINVOICE,
                "poc_designation": POC_DESIGNATION,
            },
        },
    )


def update_user_personal_info() -> dict:
    full_name = f"{DATA['FIRST_NAME']} {DATA['LAST_NAME']}".strip()
    return call(
        "POST",
        "/profile/user-profile/update-personal-info/",
        payload={
            "firstName": DATA["FIRST_NAME"],
            "lastName": DATA["LAST_NAME"],
            "fullName": full_name,
            "contactNo": DATA["CONTACT_NO"],
            "mobileNumberConsent": MOBILE_CONSENT,
            "designation": USER_DESIGNATION,
            "onboarding": True,
            "password": "",
            "confirmPassword": "",
        },
    )


# --- Phase 3: Masters -------------------------------------------------------


def _address_payload(name: str, company_id: str) -> dict:
    return {
        "company_id": company_id,
        "name": name,
        "address1": DATA["ADDRESS1"],
        "address2": "",
        "city": DATA["CITY"],
        "state": DATA["STATE"],
        "country": DATA["COUNTRY"],
        "pin": DATA["PIN"],
        "gstin": GSTIN,
        "gstintype": GSTINTYPE,
        "location_name": name,
    }


def create_bank_account() -> dict:
    return call(
        "POST",
        "/api/v3/settings/banks/",
        payload={
            "account_name": "Primary Current Account",
            "account_no": BANK_ACCOUNT_NO,
            "address": "",
            "bank_name": BANK_NAME,
            "branch": DATA["CITY"],
            "ifsc": BANK_IFSC,
            "micr": "",
            "swift_code": "",
            "is_default": 1,
        },
    )


# --- Main -------------------------------------------------------------------


def main() -> None:
    email = DATA["EMAIL"]
    password = DATA["PASSWORD"]

    log.info("=== Phase 1: Signup ===")
    signup(email, password)

    log.info("=== Phase 2: Onboarding ===")
    call("GET", "/settings/onboarding-data/do-basic-setup/")
    update_company_profile()
    update_user_personal_info()
    profile = call("GET", "/profile/info/fetch/")
    call("POST", "/settings/onboarding-data/activate-initial-modules/", payload={})

    free_trial = call("POST", "/subscription/subscription/free-trial/", payload={})
    subscription = (free_trial.get("data") or {}).get("subscription") or {}
    if subscription.get("status") != 1:
        sys.exit(f"Free-trial activation did not return active subscription: {free_trial}")

    company = (profile.get("data") or {}).get("company") or {}
    user = (profile.get("data") or {}).get("user") or {}
    if company.get("onboarding_complete") is not True:
        sys.exit("Onboarding did not complete: onboarding_complete is not True.")
    if company.get("name") != DATA["COMPANY_NAME"]:
        sys.exit(f"Company name mismatch: server={company.get('name')!r} expected={DATA['COMPANY_NAME']!r}")
    if user.get("first_name") != DATA["FIRST_NAME"]:
        sys.exit(f"User first_name mismatch: server={user.get('first_name')!r} expected={DATA['FIRST_NAME']!r}")

    log.info("=== Phase 3: Masters (billing / delivery / bank) ===")
    # v3 settings endpoints expect the company UUID (data.company.uuid), NOT the
    # integer PK (data.company.id) or the JWT company_id claim — both of which are
    # ints and trigger a 422 "UUID input should be a string..." on these routes.
    company_id = company.get("uuid")
    if not company_id:
        sys.exit(
            "Could not resolve company UUID from /profile/info/fetch/ (data.company.uuid); v3 settings masters require it."
        )
    log.info("Resolved company UUID=%s for v3 settings masters", company_id)
    call("POST", "/api/v3/settings/billing-addresses/", payload=_address_payload(ADDRESS_NAME, company_id))
    call("POST", "/api/v3/settings/delivery-locations/", payload=_address_payload(ADDRESS_NAME, company_id))
    create_bank_account()

    log.info("=== Account Credentials ===")
    log.info("Email   : %s", email)
    log.info("Password: %s", password)
    log.info("BaseURL : %s", BASE_URL)


if __name__ == "__main__":
    main()
