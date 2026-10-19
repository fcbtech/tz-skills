"""Create the "Item Image" custom field on items and turn on document image preview.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script (only BASE_URL + EMAIL + PASSWORD are used here).
STDLIB + requests ONLY. Designed to be uploaded directly to a Lambda function
(or any host with `requests` available), like the other steps.

What it does
------------
Two account-level configurations that let a demo show product photos:

1. **Item Image custom field** — a URL-type custom field on inventory items
   (Settings → Inventory → Custom Fields). Its value, per item, is a link to
   the product's photo. Step 016 fills it for finished goods.

2. **Image preview on documents** — the per-document-type "Image Preview"
   display option (Settings → Documents → Preferences). When ON, URL image
   fields render as a thumbnail on the document. This step turns it ON for
   every document type that exposes the option (the item-bearing sales/purchase
   documents).

Both actions are idempotent — the field is created only if absent, and each
document type is written only when its preview flag isn't already ON.

Sequencing
----------
This is a **Phase-1 configuration step**. Despite the 015 number it runs in
Phase 1 (not Phase 2): the custom field must exist before step 016 fills it.
It only needs the account to exist (step 000) — no counter-parties, units or
items — so it can run any time after 000. Step 016 (which sets the values)
runs after 003 (items) AND after this step.
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

# The custom field we create. data_type 6 = URL (TranZact's custom-field data
# types: 1=Numeric, 2=Text, 6=URL, ...). field_type 45 = items/inventory on the
# CREATE endpoint (note: the LIST endpoint uses the string "items" instead).
ITEM_IMAGE_FIELD_NAME = "Item Image"
ITEM_IMAGE_FIELD_DESC = "Product image link (preview as image)"
DATA_TYPE_URL = 6
FIELD_TYPE_ITEMS = 45

# The document-preference flag that renders URL image fields as a thumbnail.
IMAGE_PREVIEW_KEY = "show_item_image_preview"
# Document types to check for the flag. The option only exists on the item-bearing
# sales/purchase documents (observed: 3, 7, 9, 11, 13); we probe a small range and
# act only where the flag is actually present, so it stays correct if the set grows.
DOC_TYPE_PROBE_RANGE = range(1, 17)
DEFAULT_CURRENCY = 1  # INR


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


def _get(token: str, path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> GET %s", path)
    response = requests.get(url, params=params, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< GET %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"GET {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    response = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- 1. Item Image custom field ---------------------------------------------


def list_item_custom_fields(token: str) -> list[dict]:
    """Return all item custom fields (POST list; field_type is the string 'items')."""
    res = _post(
        token,
        "/api/v3/settings/custom-fields/list",
        {
            "field_type": "items",
            "pagination": {
                "group_by": [], "group_desc": [], "items_per_page": 500,
                "multi_sort": True, "must_sort": False, "page": 1,
                "sort_by": [], "sort_desc": [],
            },
        },
    )
    fields = res.get("data")
    return fields if isinstance(fields, list) else []


def ensure_item_image_field(token: str) -> None:
    """Create the 'Item Image' URL custom field if it doesn't already exist."""
    existing = {(f.get("name") or "").strip() for f in list_item_custom_fields(token)}
    if ITEM_IMAGE_FIELD_NAME in existing:
        log.info("Custom field %r already exists — skipping create.", ITEM_IMAGE_FIELD_NAME)
        return
    _post(
        token,
        "/api/v3/settings/custom-fields/",
        {
            "name": ITEM_IMAGE_FIELD_NAME,
            "description": ITEM_IMAGE_FIELD_DESC,
            "data_type": DATA_TYPE_URL,
            "field_type": FIELD_TYPE_ITEMS,
            "sub_type": 0,
            "field_position": 0,
            "calculation_strategy": 0,
            "default_value": "",
            "option_value": "",
            "fetch_from": None,
            "fetch_from_obj": {"id": None, "field_type_name": ""},
            "is_mandatory": False,
            "is_read_only": False,
            "is_show_counter_party": False,
            "is_calculative": False,
            "is_show_sum_in_primary_details": False,
            "is_update_downstream_options": False,
        },
    )
    log.info("Created URL custom field %r on items.", ITEM_IMAGE_FIELD_NAME)


# --- 2. Document image-preview setting --------------------------------------


def enable_image_preview(token: str) -> None:
    """Turn ON 'Image Preview' for every document type that exposes the option."""
    enabled = 0
    for doc_type in DOC_TYPE_PROBE_RANGE:
        res = _get(
            token,
            "/api/v3/settings/document-options/with-defaults",
            params={"document_type": doc_type, "currency": DEFAULT_CURRENCY, "id": ""},
        )
        data = res.get("data") or {}
        options = data.get("document_options") or {}
        flag = options.get(IMAGE_PREVIEW_KEY)
        if not isinstance(flag, dict):
            continue  # this document type doesn't support image preview
        if flag.get("value") is True:
            log.info("Image Preview already ON for document_type %d.", doc_type)
            continue
        flag["value"] = True
        _post(
            token,
            "/api/v3/settings/document-options/",
            {
                "id": data.get("id"),
                "options": options,
                "document_type": doc_type,
                "currency": DEFAULT_CURRENCY,
            },
        )
        log.info("Image Preview turned ON for document_type %d.", doc_type)
        enabled += 1
    if enabled:
        log.info("Image preview enabled on %d document type(s).", enabled)
    else:
        log.info("Image preview already enabled on all supported document types.")


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Ensuring 'Item Image' custom field ===")
    ensure_item_image_field(token)

    log.info("=== Enabling document image preview ===")
    enable_image_preview(token)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
