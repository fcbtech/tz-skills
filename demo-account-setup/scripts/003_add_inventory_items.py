"""Add demo inventory items (products) to a Tranzact account.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Creates each product/item via the inventory master endpoint. Re-running the
script is safe — products that already exist on the account are skipped.

The list of items to create is derived from the `[[BOM]]` blocks in data.md
(the sole source of truth). Each block has one `[BOM.FG]` finished-good
table and zero-or-more `[[BOM.RM]]` raw-material rows. Each row needs:
name, type ("Buy" | "Sell" | "Both"), unit, qty, price. Items appearing in
multiple BOMs must agree on `type` and `unit` — the first occurrence's
per-unit price (`price / qty`) wins. The `unit` must already exist as a
Unit of Measurement on the company; the script fails fast if it doesn't.
"""

from __future__ import annotations

import logging
import random
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

_VALID_TYPES = {"Buy", "Sell", "Both"}
_REQUIRED_STR_KEYS = ("name", "type", "unit")

# Initial (opening) stock assigned to each new item, randomised per item.
STOCK_MIN = 0
STOCK_MAX = 300


def _is_number(value: Any) -> bool:
    return not isinstance(value, bool) and isinstance(value, (int, float))


def _iter_bom_rows(raw: Any):
    """Yield (location, entry) for every FG and RM row across all [[BOM]] blocks."""
    if not isinstance(raw, list) or not raw:
        sys.exit("data.md must define at least one [[BOM]] entry")
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


def _load_items(raw: Any) -> list[dict[str, Any]]:
    """Flatten all [[BOM]] FG + RM rows into a deduped inventory-item list.

    For each unique item name (first-seen wins on per-unit price), we keep
    name/type/unit/price (per-unit = `price / qty`). Subsequent occurrences
    of the same name must agree on type and unit.
    """
    by_name: dict[str, dict[str, Any]] = {}
    order: list[str] = []

    for loc, entry in _iter_bom_rows(raw):
        missing = [k for k in _REQUIRED_STR_KEYS if not entry.get(k)]
        if "qty" not in entry:
            missing.append("qty")
        if "price" not in entry:
            missing.append("price")
        if missing:
            sys.exit(f"{loc} missing required keys: {missing}")
        if entry["type"] not in _VALID_TYPES:
            sys.exit(
                f"{loc} (name={entry['name']!r}) has invalid type "
                f"{entry['type']!r}; must be one of {sorted(_VALID_TYPES)}"
            )
        qty = entry["qty"]
        price = entry["price"]
        if not _is_number(qty) or qty <= 0:
            sys.exit(f"{loc} (name={entry['name']!r}) has invalid qty {qty!r}; must be > 0")
        if not _is_number(price) or price < 0:
            sys.exit(
                f"{loc} (name={entry['name']!r}) has invalid price {price!r}; "
                f"must be a non-negative number"
            )

        name = str(entry["name"])
        unit = str(entry["unit"])
        ptype = str(entry["type"])
        unit_price = price / qty

        existing = by_name.get(name)
        if existing is None:
            by_name[name] = {"name": name, "type": ptype, "unit": unit, "price": unit_price}
            order.append(name)
        else:
            if existing["type"] != ptype:
                sys.exit(
                    f"{loc} (name={name!r}) declares type={ptype!r} but earlier BOM "
                    f"row declared type={existing['type']!r}; types must match"
                )
            if existing["unit"] != unit:
                sys.exit(
                    f"{loc} (name={name!r}) declares unit={unit!r} but earlier BOM "
                    f"row declared unit={existing['unit']!r}; units must match"
                )
            # First-occurrence per-unit price wins; subsequent prices are ignored.

    return [by_name[name] for name in order]


ITEMS: list[dict[str, Any]] = _load_items(DATA.get("BOM"))


# --- Logging ----------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("automation")


# --- Auth -------------------------------------------------------------------


def login() -> str:
    """POST /main/login/password-login/ → access token. Inlined; replaces core.auth.ensure_authenticated."""
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


# --- Throttle-aware retry (per-call backoff on HTTP 429) --------------------

_THROTTLE_RETRIES = 4
_THROTTLE_BACKOFF = [3, 6, 12, 24]


def _throttle_sleep(resp, method: str, path: str, attempt: int) -> None:
    """Back off before retrying a rate-limited (429) request. Honors Retry-After.

    Retries the SINGLE failing call rather than letting the whole script fail and
    be re-run — that avoids the 60s wait + full re-run (and duplicate work) that
    dominated slow runs.
    """
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


def _get(token: str, path: str, params: dict | None = None) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> GET %s", path)
    for _tt in range(_THROTTLE_RETRIES + 1):
        response = requests.get(url, params=params, headers=_auth_headers(token), timeout=TIMEOUT)
        if response.status_code != 429 or _tt == _THROTTLE_RETRIES:
            break
        _throttle_sleep(response, "GET", path, _tt)
    log.info("<<< GET %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"GET {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    for _tt in range(_THROTTLE_RETRIES + 1):
        response = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
        if response.status_code != 429 or _tt == _THROTTLE_RETRIES:
            break
        _throttle_sleep(response, "POST", path, _tt)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- Business flow ----------------------------------------------------------


def fetch_masters(token: str) -> dict:
    res = _get(token, "/inventory/main-inventory/get_details_for_add_items/")
    return res.get("data", {})


def fetch_first_series(token: str) -> tuple[int, str]:
    """Returns (series row id, next itemid value)."""
    res = _post(token, "/inventory/main-inventory/get_product_series/", {})
    data = res.get("data", {})
    if data.get("product_series_type") != "series":
        raise RuntimeError("Account is in manual Item-ID mode; this automation needs series mode.")
    nums = data.get("product_number") or []
    if not nums:
        raise RuntimeError("No product series configured.")
    return nums[0]["id"], nums[0]["value"]


def build_custom_fields(fields_in_pair: list) -> dict:
    """Echo every field with empty value (read-only fields keep their existing value)."""
    out: dict = {}
    for sub in fields_in_pair:
        items = sub if isinstance(sub, list) else [sub]
        for f in items:
            uuid = f.get("uuid")
            if not uuid:
                continue
            out[uuid] = {
                "data_type": f.get("data_type"),
                "name": f.get("name"),
                "value": f.get("value", "") if f.get("is_read_only") else "",
            }
    return out


def search_product(token: str, name: str, ptype: str) -> dict | None:
    res = _get(
        token,
        "/settings/product/",
        params={
            "product_type": ptype.lower(),
            "place": "product_name",
            "service_type": -1,
            "query": name,
        },
    )
    for r in res.get("data", {}).get("results", []):
        if r.get("product_name") == name:
            return r
    return None


def pick_default_gst_tax_id(masters: dict) -> int | None:
    """First GST tax id from the masters' tax pool, or None if the account has none.

    Per knowledge/endpoints/product-change, the create payload's `taxes` field is a
    SINGLE integer tax_id (despite the plural name). Downstream OC/SQ/SE scripts
    (010, 011, …) require sell-side products to carry a GST mapping, so every item
    created here gets the default GST attached.
    """
    tax_type_map = masters.get("tax_type") or {}
    taxes = masters.get("taxes") or []
    if not isinstance(taxes, list):  # defensive — backend returns {} when empty
        return None
    for tax in taxes:
        tax_id = tax.get("id")
        if tax_id is None:
            continue
        if tax_type_map.get(str(tax_id)) == "gst":
            return int(tax_id)
    return None


def create_product(
    token: str,
    name: str,
    ptype: str,
    unit_name: str,
    price: int | float,
    tax_id: int | None,
    custom_fields: dict,
) -> dict:
    series_id, itemid = fetch_first_series(token)
    opening_stock = random.randint(STOCK_MIN, STOCK_MAX)
    payload = {
        "action_type": "create",
        "product": {
            "itemid": itemid,
            "product_name": name,
            "is_service": 0,
            "type": ptype,
            "unit": unit_name,
            "category": None,
            "quantity": opening_stock,
            "price": price,
            "hsn_code": None,
            "taxes": tax_id,
            "min_sl": None,
            "max_sl": None,
            "other_prices": {},
            "custom_fields": custom_fields,
            "product_no": series_id,
        },
        "units": [],
        "product_name_check": True,
    }
    return _post(token, "/inventory/main-inventory/change_product/", payload)


def validate_units_exist(token: str) -> None:
    """Fail fast if any item references a UoM the company doesn't have."""
    masters = fetch_masters(token)
    available = {(u.get("unit_name") or "").strip() for u in masters.get("master_units") or []}
    missing = sorted({item["unit"] for item in ITEMS if item["unit"] not in available})
    if missing:
        raise RuntimeError(
            f"UoM(s) not configured on the company: {missing}. "
            f"Add them under Settings → Master UoM, then re-run."
        )


def ensure_products(token: str) -> None:
    """Create each missing product, attaching the company's default GST tax."""
    masters = fetch_masters(token)
    default_tax_id = pick_default_gst_tax_id(masters)
    if default_tax_id is None:
        raise RuntimeError(
            "No GST tax master found on the company — downstream sales-doc scripts "
            "(010_, 011_) require sell-side products to carry a GST mapping. "
            "Enable GST 18% under Settings → Tax Options and re-run."
        )
    log.info("Default GST tax_id for new products: %d", default_tax_id)

    for item in ITEMS:
        name = item["name"]
        ptype = item["type"]
        unit = item["unit"]
        price = item["price"]

        if search_product(token, name, ptype):
            log.info("Product already exists, skipping: %s (%s)", name, ptype)
            continue

        log.info(
            "Creating product %s (%s, unit=%s, price=%s, tax_id=%d, stock=%d..%d)",
            name, ptype, unit, price, default_tax_id, STOCK_MIN, STOCK_MAX,
        )
        masters = fetch_masters(token)
        custom_fields = build_custom_fields(masters.get("fields_in_pair") or [])
        result = create_product(token, name, ptype, unit, price, default_tax_id, custom_fields)
        # change_product has two mutually-exclusive success shapes inside `data`:
        #   - no opening stock:   {"status": "success"}
        #   - opening stock > 0:  {"inventory_approval_doc_id": <int>}  (no "status" key)
        # Since every item here is seeded with opening stock, the second shape is the
        # normal case — treat either as success.
        data = result.get("data") or {}
        if data.get("status") != "success" and "inventory_approval_doc_id" not in data:
            raise RuntimeError(f"Product '{name}' create returned unexpected response: {result!r}")
        if not search_product(token, name, ptype):
            raise RuntimeError(f"Product '{name}' not searchable after create")


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Validating UoMs ===")
    validate_units_exist(token)

    log.info("=== Ensuring %d products (derived from BOM) ===", len(ITEMS))
    ensure_products(token)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
