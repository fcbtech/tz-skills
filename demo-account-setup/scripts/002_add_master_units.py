"""Add master Units of Measurement (UoMs) to a Tranzact account.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

The list of UoMs to ensure is derived from the `unit` field of every entry in
every `[[BOM]]` block in data.md — both the `[BOM.FG]` finished good and the
`[[BOM.RM]]` raw-material rows contribute. `[[BOM]]` is the sole source of
truth for the items/units seeded into the account. Run this before
`003_add_inventory_items.py` to guarantee every required unit exists on the
company.

Backend enforces case-insensitive uniqueness on `unit_name`, so "Kg" /
"kg" / "KG" all collide. Existing units are skipped on a case-insensitive
match.
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

LIST_PAYLOAD = {
    "filters": {},
    "search": {},
    "pagination": {"page": 1, "items_per_page": 500},
}


def _iter_bom_rows(raw: Any):
    """Yield (location, entry) for every FG and RM row across all [[BOM]] blocks."""
    if not isinstance(raw, list) or not raw:
        sys.exit("data.md must define at least one [[BOM]] entry to derive units from")
    for bom_idx, bom in enumerate(raw):
        if not isinstance(bom, dict):
            sys.exit(f"BOM[{bom_idx}] must be a table")
        fg = bom.get("FG")
        if not isinstance(fg, dict):
            sys.exit(f"BOM[{bom_idx}] must define a [BOM.FG] finished-good table")
        yield f"BOM[{bom_idx}].FG", fg
        rms = bom.get("RM")
        if rms is None:
            continue
        if not isinstance(rms, list) or not rms:
            sys.exit(f"BOM[{bom_idx}].RM, when present, must be a non-empty array")
        for rm_idx, rm in enumerate(rms):
            if not isinstance(rm, dict):
                sys.exit(f"BOM[{bom_idx}].RM[{rm_idx}] must be a table")
            yield f"BOM[{bom_idx}].RM[{rm_idx}]", rm


def _collect_target_units(raw: Any) -> list[str]:
    """Pull the unique `unit` values out of every [[BOM]] row, preserving first-seen order."""
    seen: set[str] = set()
    units: list[str] = []
    for loc, entry in _iter_bom_rows(raw):
        if not entry.get("unit"):
            sys.exit(f"{loc} missing required 'unit' field")
        unit = str(entry["unit"]).strip()
        key = unit.lower()
        if key not in seen:
            seen.add(key)
            units.append(unit)
    return units


TARGET_UNITS: list[str] = _collect_target_units(DATA.get("BOM"))


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


# --- HTTP helper ------------------------------------------------------------


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    headers = {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}
    log.info(">>> POST %s", path)
    response = _send("POST", url, path, json=payload, headers=headers, timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- Business flow ----------------------------------------------------------


def fetch_existing_unit_names(token: str) -> set[str]:
    """Existing master UoM unit_names, lowercased (backend uniqueness is case-insensitive)."""
    result = _post(token, "/api/v3/settings/master-uom/list", LIST_PAYLOAD)
    rows = result.get("data", []) or []
    return {(row.get("unit_name") or "").strip().lower() for row in rows}


def create_unit(token: str, unit_name: str) -> dict:
    return _post(token, "/api/v3/settings/master-uom/", {"unit_name": unit_name})


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Target units (from BOM): %s ===", TARGET_UNITS)
    existing = fetch_existing_unit_names(token)

    created: list[str] = []
    skipped: list[str] = []

    for name in TARGET_UNITS:
        if name.strip().lower() in existing:
            log.info("'%s' already exists — skipping.", name)
            skipped.append(name)
            continue
        response = create_unit(token, name)
        if not response.get("id"):
            raise RuntimeError(f"Create UoM '{name}' returned no id; response={response}")
        if response.get("unit_name") != name:
            raise RuntimeError(
                f"Create UoM '{name}' returned mismatched unit_name={response.get('unit_name')!r}"
            )
        created.append(name)

    log.info("Created: %s", created or "none")
    log.info("Skipped (already present): %s", skipped or "none")
    log.info("=== Done ===")


if __name__ == "__main__":
    main()
