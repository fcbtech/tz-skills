"""Set the "Item Image" URL on finished-good items from data.md image links.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. STDLIB + requests ONLY. Designed to be uploaded
directly to a Lambda function (or any host with `requests` available).

What it does
------------
For each `[BOM.FG]` finished good in data.md that carries an optional
`image_url`, this step writes that link into the item's **"Item Image"**
custom field (created by step 015). With document image-preview ON (also
from 015), the product photo then renders on the item master and documents.

Only **finished goods** get an image (per product guidance). An FG without an
`image_url` is skipped. The step is idempotent — an FG whose Item Image already
equals the given URL is left untouched. It is non-fatal per-item: a finished
good that can't be found or updated is logged and skipped, not aborted.

Mechanism (captured from the webapp)
------------------------------------
Setting a custom-field value is a full-product update: read the product, inject
the value into its ``custom_fields`` map (keyed by the field's id), POST the
whole product back to ``change_product``.

Sequencing
----------
Phase-1 step that must run **after 003 (items exist) and after 015 (the field
exists)**. Despite the 016 number it runs in Phase 1, before the Phase-2 lanes.
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

ITEM_IMAGE_FIELD_NAME = "Item Image"
DATA_TYPE_URL = 6


# --- Collect finished goods that have an image_url --------------------------


def _load_fg_images(raw: Any) -> list[dict[str, str]]:
    """Return [{name, type, image_url}] for every FG in data.md with an image_url."""
    if not isinstance(raw, list) or not raw:
        sys.exit("data.md must define at least one [[BOM]] entry")
    out: list[dict[str, str]] = []
    seen: set[str] = set()
    for bom_idx, bom in enumerate(raw):
        if not isinstance(bom, dict):
            sys.exit(f"BOM[{bom_idx}] must be a table")
        fg = bom.get("FG")
        if not isinstance(fg, dict):
            sys.exit(f"BOM[{bom_idx}] must define a [BOM.FG] finished-good table")
        image_url = str(fg.get("image_url") or "").strip()
        if not image_url:
            continue
        name = str(fg.get("name") or "").strip()
        if not name:
            sys.exit(f"BOM[{bom_idx}].FG with image_url has no name")
        if name in seen:
            continue
        seen.add(name)
        out.append({"name": name, "type": str(fg.get("type") or "Both"), "image_url": image_url})
    return out


FG_IMAGES = _load_fg_images(DATA.get("BOM"))


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
        raise RuntimeError(f"GET {path} failed (HTTP {response.status_code}): {response.text[:200]}")
    return response.json() or {}


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    response = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        raise RuntimeError(f"POST {path} failed (HTTP {response.status_code}): {response.text[:200]}")
    return response.json() or {}


# --- Lookups ----------------------------------------------------------------


def find_image_field_id(token: str) -> str:
    """Return the id of the 'Item Image' custom field (created by 015)."""
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
    for field in res.get("data") or []:
        if (field.get("name") or "").strip() == ITEM_IMAGE_FIELD_NAME:
            return field["id"]
    sys.exit(
        f"Custom field {ITEM_IMAGE_FIELD_NAME!r} not found — run 015_configure_item_image.py first."
    )


def search_product(token: str, name: str, ptype: str) -> dict | None:
    """Find a product by exact name (same lookup 003 uses)."""
    res = _get(
        token,
        "/settings/product/",
        params={"product_type": ptype.lower(), "place": "product_name", "service_type": -1, "query": name},
    )
    for r in res.get("data", {}).get("results", []):
        if r.get("product_name") == name:
            return r
    return None


def fetch_product_detail(token: str, product_id: str) -> dict:
    """Return the full product object (the shape change_product expects)."""
    res = _get(token, "/inventory/main-inventory/view_product_details/", params={"id": product_id})
    product = (((res.get("data") or {}).get("data") or {}).get("product"))
    if not isinstance(product, dict):
        raise RuntimeError(f"Unexpected view_product_details shape for {product_id}")
    return product


# --- Set image --------------------------------------------------------------


def set_fg_image(token: str, fg: dict, field_id: str) -> str:
    """Set one FG's Item Image. Returns 'set' | 'skip' | a failure reason string."""
    found = search_product(token, fg["name"], fg["type"])
    if not found:
        return f"not found (name={fg['name']!r}, type={fg['type']!r})"
    product = fetch_product_detail(token, found["id"])

    cfs = product.get("custom_fields")
    if not isinstance(cfs, dict) or field_id not in cfs:
        return f"item has no {ITEM_IMAGE_FIELD_NAME!r} field slot (field not applied to item?)"

    if (cfs[field_id] or {}).get("value") == fg["image_url"]:
        return "skip"

    cfs[field_id] = {"data_type": DATA_TYPE_URL, "name": ITEM_IMAGE_FIELD_NAME, "value": fg["image_url"]}
    _post(
        token,
        "/inventory/main-inventory/change_product/",
        {"action_type": "update", "product": product, "force": False, "update_cf_in_bom_items": True},
    )
    return "set"


# --- Main -------------------------------------------------------------------


def main() -> None:
    if not FG_IMAGES:
        log.info("No finished goods declare an image_url in data.md — nothing to do.")
        return

    log.info("=== Login ===")
    token = login()

    log.info("=== Locating 'Item Image' custom field ===")
    field_id = find_image_field_id(token)

    log.info("=== Setting images on %d finished good(s) ===", len(FG_IMAGES))
    set_count = skip_count = fail_count = 0
    for fg in FG_IMAGES:
        try:
            result = set_fg_image(token, fg, field_id)
        except Exception as exc:  # noqa: BLE001 — non-fatal per item
            log.info("FAILED %s — %s", fg["name"], exc)
            fail_count += 1
            continue
        if result == "set":
            log.info("Image set: %s", fg["name"])
            set_count += 1
        elif result == "skip":
            log.info("Image already set, skipping: %s", fg["name"])
            skip_count += 1
        else:
            log.info("Skipped %s — %s", fg["name"], result)
            fail_count += 1

    log.info("=== Done: %d set, %d already set, %d skipped/failed ===", set_count, skip_count, fail_count)


if __name__ == "__main__":
    main()
