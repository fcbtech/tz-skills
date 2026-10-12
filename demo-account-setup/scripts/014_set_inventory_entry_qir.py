"""Configure the company so inventory updates on **GRN/QIR**, not plain Inward.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script (only BASE_URL + EMAIL + PASSWORD are used here).
STDLIB + requests ONLY. Designed to be uploaded directly to a Lambda function
(or any host with `requests` available), exactly like the other steps.

What it does
------------
TranZact has a company setting (Settings → Inventory → Document Settings)
that decides **which purchase document adds raw-material stock**: the plain
**Inward**, or the **GRN / QIR** (goods-receipt with quality inspection). The
demo should show the QIR-driven flow (matches steps 008–010, which build
POs → Inwards → QIRs), so this step flips the raw-material side to
"stock enters on GRN/QIR":

    raw-material  Inward   (document_type 3)  -> OFF
    raw-material  GRN/QIR  (document_type 5)  -> ON

It reads the account's current document-settings, changes only the records
that are not already in the desired state (idempotent — safe to re-run), and
leaves the finished-good side untouched.

Sequencing
----------
This is a **Phase-1 configuration step**. Despite the 014 number it must run
**before the Phase-2 document lanes** (specifically the PO/QIR lane
008–010, which assumes stock enters on QIR). It only needs the account to
exist (step 000); it does not depend on counter-parties, units or items, so
it can run any time after 000 and before Phase 2.
"""

from __future__ import annotations

import logging
import re
import sys
from pathlib import Path
from typing import Any

import requests


# --- data.md loader ---------------------------------------------------------


def load_data_md(script_file: str) -> dict[str, Any]:
    """Load TOML inputs from data.md adjacent to this script."""
    import tomllib

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

# Raw-material document-settings record types (Settings → Inventory → Document
# Settings). These ints are TranZact's document_type codes for the raw-material
# ("rm") purchase side; the finished-good side uses different codes and is left
# alone. entry_status: 1 = this document adds stock, 0 = it does not.
PRODUCT_TYPE_RM = "rm"
DOC_TYPE_INWARD = 3
DOC_TYPE_GRN_QIR = 5

# Desired raw-material state: stock enters on GRN/QIR, never on plain Inward.
DESIRED_RM_ENTRY = {
    DOC_TYPE_INWARD: 0,
    DOC_TYPE_GRN_QIR: 1,
}


# --- Logging ----------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("automation")


# --- Auth -------------------------------------------------------------------


def login() -> str:
    """POST /main/login/password-login/ → access token."""
    url = f"{BASE_URL}/main/login/password-login/"
    log.info(">>> POST /main/login/password-login/")
    response = requests.post(
        url,
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


# --- HTTP helpers -----------------------------------------------------------


def _auth_headers(token: str) -> dict[str, str]:
    return {**DEFAULT_HEADERS, "Authorization": f"Bearer {token}"}


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    response = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


def _put(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> PUT %s", path)
    response = requests.put(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< PUT %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"PUT {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- Document-settings read / update ----------------------------------------


def fetch_document_settings(token: str) -> list[dict]:
    """Return the list of inventory document-settings records.

    POST (not GET — the endpoint is POST-only) with an empty body; the server
    returns every record: {id, product_type, document_type, entry_status}.
    """
    res = _post(token, "/api/v3/settings/document-settings/list", {})
    records = res.get("data")
    if not isinstance(records, list):
        sys.exit(f"Unexpected document-settings/list response (no data array): {res!r}")
    return records


def set_entry_status(token: str, record: dict, entry_status: int) -> None:
    """PUT one document-settings record to the given entry_status."""
    _put(
        token,
        f"/api/v3/settings/document-settings/{record['id']}",
        {
            "product_type": record["product_type"],
            "document_type": record["document_type"],
            "entry_status": entry_status,
        },
    )


def configure_inventory_entry(token: str) -> None:
    """Flip the raw-material inventory-entry side to GRN/QIR (idempotent)."""
    records = fetch_document_settings(token)
    by_doc_type = {
        r["document_type"]: r
        for r in records
        if r.get("product_type") == PRODUCT_TYPE_RM
    }

    changed = 0
    for doc_type, desired in DESIRED_RM_ENTRY.items():
        record = by_doc_type.get(doc_type)
        label = "Inward" if doc_type == DOC_TYPE_INWARD else "GRN/QIR"
        if record is None:
            # Not fatal — the account simply doesn't expose this record. Warn and move on.
            log.info("No raw-material %s (document_type %d) record found; skipping.", label, doc_type)
            continue
        if record.get("entry_status") == desired:
            log.info("RM %s already %s — no change.", label, "ON" if desired else "OFF")
            continue
        set_entry_status(token, record, desired)
        log.info("RM %s set to %s.", label, "ON" if desired else "OFF")
        changed += 1

    if changed:
        log.info("Inventory entry configured: raw-material stock now updates on GRN/QIR.")
    else:
        log.info("Inventory entry already configured for GRN/QIR; nothing to change.")


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Setting inventory entry to GRN/QIR (raw materials) ===")
    configure_inventory_entry(token)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
