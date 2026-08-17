"""Create a Bill of Materials in a demo account from the [[BOM]] block in data.md.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md and:
  1. Resolves the FG and RM products by name (case-insensitive exact match).
  2. Maps each row's unit name to the matching unit on the product master.
  3. Picks the first non-reject store (rm/fg/scrap) + first available BOM
     number series, and resolves the WIP store the header requires
     (`doc_wip_store` — base-data default, else a WIP-typed store, else the
     main store). Omitting `doc_wip_store` makes create 500.
  4. POSTs /production/bom/create/ with `save_action=save_and_publish`, filling
     the four fixed `other_charges` buckets from the block's optional
     `[BOM.charges]` table (labour/machinery/electricity/other) and the `scrap`
     array from its optional `[[BOM.scrap]]` rows — so the finished good carries a
     realistic cost: rolled-up material + charges − scrap recovery (a scrap row's
     recovery value is the scrap item's own master price × qty; the row itself
     carries no price). Both are optional; omitted ⇒ zero charges / no scrap.
  5. Verifies the persisted BOM by reading /production/general/view/ (name, FG,
     raw-material count, and — when declared — the charge amounts and scrap rows).

Multi-level (nested child-BOM) support
---------------------------------------
A raw material can itself be a manufactured item that already has its own
published BOM. Setting ``child_bom`` on a ``[[BOM.RM]]`` row links that RM to
its child BOM so the sub-assembly's own raw materials are expanded inline:

    [[BOM.RM]]
    name = "Sub Assembly"
    qty  = 2
    unit = "Pcs"
    type = "Both"
    child_bom = true            # link first published BOM for this item, OR
    # child_bom = "BOM00041"    # target a specific bom_number / bom_name

When set, the script mirrors the web flow:
  a. POST /production/bom/get-child-boms/ to discover the item's published
     BOM(s) (filtered by item id + selected unit id).
  b. GET  /production/bom/get-bom-items/?selected_bom=<id> to read the child
     BOM's raw materials.
  c. Builds the nested ``child_rm`` rows and stamps ``child_bom_id`` /
     ``bom_item_id`` / ``mfg_quantity`` on the parent RM row.

The child BOM must already be published when this RM is created — either it was
created by an earlier [[BOM]] block in this same data.md run (list the child
BOM before the parent), or it pre-exists on the account. NOTE: on read-back the
view FLATTENS nested child RMs into top-level raw_materials rows (the parent RM
keeps a non-zero child_bom_id), so the verification accounts for child rows.

Assumes the account has already been seeded with the inventory items
declared in data.md (via 003_add_inventory_items.py).
"""

from __future__ import annotations

import datetime
import json
import logging
import re
import sys
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

# Production-module doc_type for Bill of Materials (string form on body endpoints,
# integer 78 on /production/general/view/). See doc-type-constants in QA memory.
BOM_DOC_TYPE_STR = "bill_of_material"
BOM_DOC_TYPE_ID = 78

# Other-charges buckets are FIXED — backend identifies them by key, not label.
OTHER_CHARGES_BUCKETS = [
    ("labour", "Labour Charges"),
    ("machinery", "Machinery Charges"),
    ("electricity", "Electricity Charges"),
    ("other_charges", "Other Charges"),
]


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
    r = requests.get(url, params=params, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< GET %s -> %d", path, r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {path} failed (HTTP {r.status_code}): {r.text[:500]}")
    return r.json()


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    r = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST %s -> %d", path, r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed (HTTP {r.status_code}): {r.text[:500]}")
    return r.json()


def _post_bom_create(token: str, payload: dict) -> tuple[bool, int, dict | None, str]:
    """POST the BOM create WITHOUT raising, so the caller can retry/inspect.

    Returns ``(ok, status_code, resp_json_or_None, body_text)``. ``ok`` is True
    only on a 2xx that also parses as JSON with ``status == 1`` and a positive
    integer id; every other outcome (HTTP >= 400, unparseable body, or a 200 that
    still reports failure) comes back ``ok=False`` with the raw body for logging.
    """
    url = f"{BASE_URL}/production/bom/create/"
    log.info(">>> POST /production/bom/create/")
    r = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
    log.info("<<< POST /production/bom/create/ -> %d", r.status_code)
    if r.status_code >= 400:
        return False, r.status_code, None, r.text[:1000]
    try:
        data = r.json()
    except ValueError:
        return False, r.status_code, None, r.text[:1000]
    # Accept any truthy id — the backend now returns UUIDv7 strings for primary
    # keys (not just ints); requiring an int here made a SUCCESSFUL create look
    # failed, which would trigger the store-cycling retry and create duplicates.
    new_id = (data.get("data") or {}).get("id")
    ok = data.get("status") == 1 and bool(new_id) and str(new_id).strip() not in ("", "0")
    return ok, r.status_code, data, "" if ok else str(data)[:1000]


# --- Master-data fetches ----------------------------------------------------


def fetch_base_data(token: str) -> dict[str, Any]:
    # Post-release: get-base-data returns both `docData` (defaults) and `docStructure`
    # (form/CF definitions). Doc-level custom-field defs now live under
    # docStructure.primary_document_details.custom_fields; docData's copy is now `{}`.
    return _post(token, "/production/general/get-base-data/",
                 {"doc_type": BOM_DOC_TYPE_STR, "action": "create", "id": "0"})["data"]


def flatten_doc_custom_fields(base_data: dict[str, Any]) -> list[dict[str, Any]]:
    """Flatten docStructure document-level custom fields into the flat list the create
    payload expects.

    Post-release shape: docStructure.primary_document_details.custom_fields is a dict
    keyed by section -> list of rows -> list of field-definition dicts, e.g.
    ``{"0": [[field, field], [field], ...]}``. The FE flattens this (row/section order
    preserved) into primary_document_details.custom_fields; definitions are passed
    through verbatim here.
    """
    structure_cf = (
        base_data.get("docStructure", {}).get("primary_document_details", {}).get("custom_fields", {})
    )
    fields: list[dict[str, Any]] = []
    if isinstance(structure_cf, dict):
        for rows in structure_cf.values():
            for row in rows:
                for field in row:
                    fields.append(field)
    return fields


def fetch_all_stores(token: str) -> list[dict[str, Any]]:
    return _get(token, "/settings/my-settings/", params={"model": "all_stores"})["data"]


def first_non_reject_store(stores: list[dict[str, Any]]) -> Any:
    for store in stores:
        if store.get("is_reject") == 0:
            return store["id"]                 # id as-is (int or UUIDv7 string)
    raise RuntimeError("No non-reject store found in this account.")


def resolve_wip_store(base_data: dict[str, Any], stores: list[dict[str, Any]],
                      fallback_store_id: Any) -> tuple[Any, str]:
    """Resolve the WIP store the BOM header requires.

    The web flow always sends ``doc_wip_store`` in primary_document_details;
    omitting it (or sending a non-WIP store) makes /production/bom/create/ 500
    ("Something went wrong!"). Prefer the base-data default (what the webapp
    pre-fills), then a WIP-typed store from the store list, then reuse the main
    store as a last resort.

    The default is read shape-agnostically: pre-release backends carry it under
    ``docData.primary_document_details``; the post-release shape migrates these
    defaults to ``docStructure.primary_document_details`` (same migration the
    custom-field defs went through — see ``flatten_doc_custom_fields``). Reading
    ``docData`` only returned ``None`` on post-release envs (prod), silently
    dropping to the non-WIP fallback and 500-ing the create.
    """
    default_wip = None
    for _root in ("docData", "docStructure"):
        _pdd = (base_data.get(_root) or {}).get("primary_document_details") or {}
        if _pdd.get("doc_wip_store"):
            default_wip = _pdd["doc_wip_store"]
            break
    if default_wip:
        return default_wip, "base-data default"
    for s in stores:
        blob = " ".join(
            str(s.get(k, "")).lower()
            for k in ("store_type", "type", "name", "store_name", "store_category")
        )
        if "wip" in blob or "work in progress" in blob or "work-in-progress" in blob:
            return s["id"], "store-list WIP match"
    return fallback_store_id, "fallback (reused main store)"


def fetch_number_series(token: str, store_id: int) -> list[dict[str, Any]]:
    return _get(token, "/production/general/get-number-series/",
                params={"doc_type": BOM_DOC_TYPE_STR, "store_id": store_id})["data"]["number"]


_PRODUCT_CACHE: dict[str, dict[str, Any]] = {}


def prefetch_products(token: str) -> None:
    """Fetch the whole product list ONCE and cache it by lowercased name.

    BOM rows reuse the same items heavily (a raw material appears across several
    finished goods), so resolving each row with its own /settings/product/ query
    means dozens of redundant round-trips. Pull the full list up front instead;
    per-row lookups then hit the cache. find_product_by_name still falls back to a
    targeted query on a cache miss (e.g. a paginated list), so behaviour is
    unchanged — just fewer calls.
    """
    rows = _get(token, "/settings/product/", params={
        "product_type": "both", "place": "product_name", "service_type": "",
    })["data"]["results"]
    for row in rows:
        key = (row.get("product_name") or "").strip().lower()
        if key:
            _PRODUCT_CACHE.setdefault(key, row)
    log.info("Prefetched %d products into the lookup cache.", len(_PRODUCT_CACHE))


def find_product_by_name(token: str, name: str) -> dict[str, Any]:
    needle = name.strip().lower()
    cached = _PRODUCT_CACHE.get(needle)
    if cached is not None:
        return cached
    rows = _get(token, "/settings/product/", params={
        "product_type": "both", "place": "product_name",
        "service_type": "", "query": name,
    })["data"]["results"]
    for row in rows:
        if (row.get("product_name") or "").strip().lower() == needle:
            _PRODUCT_CACHE[needle] = row
            return row
    raise RuntimeError(
        f"Product not found by name: {name!r} (search returned {len(rows)} row(s)). "
        "Ensure 003_add_inventory_items.py has been run against this account."
    )


def pick_unit(product: dict[str, Any], unit_name: str) -> dict[str, Any]:
    needle = unit_name.strip().lower()
    for u in product["units"]:
        if (u.get("unit_name") or "").strip().lower() == needle:
            return u
    available = [u.get("unit_name") for u in product["units"]]
    raise RuntimeError(
        f"Unit {unit_name!r} not configured on product {product.get('product_name')!r}; "
        f"available: {available}"
    )


# --- Multi-level (child-BOM) fetches ----------------------------------------


def fetch_child_boms(token: str, item_id: int, unit_id: int,
                     exclude_id: str = "0") -> list[dict[str, Any]]:
    """POST /production/bom/get-child-boms/ → published BOM(s) for an item+unit.

    Returns the ``bom_dict[item_id]`` list (empty if the item has no published
    BOM in the selected unit). Each entry carries ``id`` (the child_bom_id),
    ``bom_number``, ``bom_name`` and ``bom_item_id`` (the child BOM's FG
    bom_item_id, which the parent RM row must echo).
    """
    resp = _post(token, "/production/bom/get-child-boms/", {
        "item_ids": [item_id], "unit_ids": [unit_id], "exclude_id": exclude_id,
    })
    bom_dict = (resp.get("data") or {}).get("bom_dict") or {}
    return bom_dict.get(str(item_id)) or bom_dict.get(item_id) or []


def fetch_bom_items(token: str, child_bom_id: int) -> dict[str, Any]:
    """GET /production/bom/get-bom-items/?selected_bom=<id> → child BOM's items.

    Returns ``data`` with ``fg_items`` (the child finished good) and
    ``rm_items`` (the child raw materials used to build ``child_rm``).
    """
    return _get(token, "/production/bom/get-bom-items/",
                params={"selected_bom": child_bom_id})["data"]


def count_flattened_rm_rows(token: str, child_bom_id: int) -> int:
    """Total RM rows the BOM view will flatten a linked child BOM into.

    ``/production/general/view/`` expands a linked child BOM's ENTIRE sub-tree —
    grandchildren, great-grandchildren and all — into flat top-level rows. But
    ``get-bom-items`` (what ``attach_child_bom`` reads) returns only the child's
    IMMEDIATE raw materials. So for a BOM nested 3+ levels deep, counting just the
    direct child rows under-counts the flattened view by every grandchild row.
    Recurse through each sub-assembly (any rm_item carrying a ``child_bom_id``) to
    get the true flattened total the view will report.
    """
    items = fetch_bom_items(token, child_bom_id)
    total = 0
    for it in (items.get("rm_items") or []):
        total += 1
        grand_bom_id = it.get("child_bom_id") or 0
        if grand_bom_id:
            total += count_flattened_rm_rows(token, grand_bom_id)
    return total


def select_child_bom(child_boms: list[dict[str, Any]],
                     selector: Any) -> dict[str, Any] | None:
    """Pick a child BOM. ``selector`` True ⇒ first; a string ⇒ match bom_number/bom_name."""
    if not child_boms:
        return None
    if selector is True:
        return child_boms[0]
    needle = str(selector).strip().lower()
    for cb in child_boms:
        if ((cb.get("bom_number") or "").strip().lower() == needle
                or (cb.get("bom_name") or "").strip().lower() == needle):
            return cb
    return None


# --- Payload builders -------------------------------------------------------


def base_product_row(product: dict[str, Any], unit: dict[str, Any],
                     quantity: float, comment: str = "") -> dict[str, Any]:
    return {
        "quantity": quantity,
        "bom_item_id": "",
        "comment": comment,
        "has_alternate": 0,
        "item_id": product["id"],
        "id": product["id"],
        "product": product["id"],
        "uuid": product["uuid"],
        "item_uuid": product["uuid"],
        "itemid": product["itemid"],
        "product_name": product["product_name"],
        "name": product["product_name"],
        "hsn_code": product.get("hsn_code", ""),
        "category_name": product.get("category_name", ""),
        "category": product.get("category_name", ""),
        "unit": unit["id"],
        "unit_id": unit["id"],
        "units": product["units"],
        "stock": product.get("stock", 0),
        "price": product.get("price", 0),
        "in_avg_price": product.get("in_avg_price", 0),
        "is_service": product.get("is_service", 0),
        "taxes": product.get("taxes", []),
        "prices": product.get("prices", {}),
        "vendor_mapping": product.get("vendor_mapping"),
        "custom_fields_parsed": product.get("custom_fields_parsed", {}),
        "custom_fields": [],
        "alternate_list": [],
        "routing_list": [],
        "key_mappings": {"text": "product_name", "value": "id"},
    }


def build_fg_row(product: dict[str, Any], unit: dict[str, Any], quantity: float) -> dict[str, Any]:
    row = base_product_row(product, unit, quantity)
    row["cost_alloc"] = 100
    return row


def build_rm_row(product: dict[str, Any], unit: dict[str, Any], quantity: float,
                 fg_quantity: float, index: int, color: str) -> dict[str, Any]:
    row = base_product_row(product, unit, quantity)
    row.update({
        "req_quantity": 1,
        "composition": quantity / fg_quantity if fg_quantity else 0,
        "index": index,
        "index_color": color,
        "expanded": False,
        "isExtra": True,
        "isDisabled": False,
        "process_item_id": 0,
        "child_bom_id": 0,
        "child_rm": [],
        "mfg_quantity": 0,
        "current_stock": 0,
        "showMoveRMDialog": False,
    })
    return row


def build_child_rm_row(rm_item: dict[str, Any], parent_quantity: float,
                       index: str, color: str) -> dict[str, Any]:
    """Build a nested ``child_rm`` row from a child BOM's get-bom-items rm_item.

    Quantity scales by the parent RM quantity: the child BOM stores a per-unit
    requirement (``rm_item.quantity``); the nested row consumes that × the
    parent RM quantity. ``unit`` carries the unit id (not name) here.
    """
    per_unit = float(rm_item.get("quantity") or 0)
    unit_id = rm_item.get("unit_id")
    return {
        "quantity": per_unit * parent_quantity,
        "bom_item_id": rm_item.get("bom_item_id", ""),
        "comment": rm_item.get("comment", ""),
        "has_alternate": rm_item.get("has_alternate", 0),
        "child_rm": [],
        "req_quantity": per_unit,
        "isDisabled": False,
        "process_item_id": 0,
        "current_stock": 0,
        "showMoveRMDialog": False,
        "item_id": rm_item["item_id"],
        "item_uuid": rm_item.get("item_uuid", ""),
        "itemid": rm_item.get("itemid", ""),
        "product_name": rm_item.get("product_name", ""),
        "index_color": color,
        "units": rm_item.get("units", []),
        "unit": unit_id,
        "cost_alloc": rm_item.get("cost_alloc", 0),
        "category_name": rm_item.get("category_name", ""),
        "unit_id": unit_id,
        "child_bom_id": rm_item.get("child_bom_id", 0) or 0,
        "alternate_list": rm_item.get("alternate_list", []),
        "latest_purchase_price": rm_item.get("latest_purchase_price", 0),
        "in_avg_price": rm_item.get("in_avg_price", 0),
        "fifo_price": rm_item.get("fifo_price", 0),
        "default_price": rm_item.get("default_price", 0),
        "custom_fields": [],
        "index": index,
        "isExtra": False,
        "expanded": False,
        "composition": per_unit,
        "mfg_quantity": 0,
        "routing_list": [],
    }


def attach_child_bom(token: str, rm_row: dict[str, Any], item_id: int, unit_id: int,
                     parent_quantity: float, parent_index: int, palette: list[str],
                     selector: Any) -> dict[str, Any]:
    """Discover and link a child BOM, mutating ``rm_row`` into a sub-assembly row.

    Sets ``child_bom_id`` / ``bom_item_id`` / ``mfg_quantity`` and populates
    ``child_rm`` from the child BOM's raw materials. Returns the chosen child
    BOM dict. Raises if no matching published child BOM exists.
    """
    child_boms = fetch_child_boms(token, item_id, unit_id)
    child = select_child_bom(child_boms, selector)
    if child is None:
        available = [f"{cb.get('bom_number')} ({cb.get('bom_name')})" for cb in child_boms]
        raise RuntimeError(
            f"child_bom requested for item_id={item_id} unit_id={unit_id} but no "
            f"matching published child BOM found (selector={selector!r}; available: {available}). "
            "Ensure the child BOM is published before this one (list it earlier in data.md)."
        )

    items = fetch_bom_items(token, child["id"])
    rm_items = items.get("rm_items") or []
    rm_row["child_rm"] = [
        build_child_rm_row(it, parent_quantity, f"{parent_index}.{i + 1}",
                           palette[i % len(palette)])
        for i, it in enumerate(rm_items)
    ]
    rm_row["child_bom_id"] = child["id"]
    rm_row["bom_item_id"] = (
        child.get("bom_item_id")
        or (items.get("fg_items") or {}).get("bom_item_id")
        or ""
    )
    rm_row["mfg_quantity"] = 1
    log.info("    Linked child BOM %s (%s) with %d child RM(s).",
             child.get("bom_number"), child.get("bom_name"), len(rm_items))
    return child


# data.md [BOM.charges] keys -> backend other_charges bucket keys. The four buckets
# are FIXED (backend keys them, not by label); data.md exposes them by friendly name.
CHARGE_KEY_MAP = {
    "labour": "labour",
    "machinery": "machinery",
    "electricity": "electricity",
    "other": "other_charges",
}


def build_other_charges(charges_spec: dict[str, Any] | None) -> dict[str, dict[str, Any]]:
    """Build the fixed four-bucket other_charges dict, filling amounts from an
    optional ``[BOM.charges]`` table (labour/machinery/electricity/other — INR for
    the FG qty). Any subset may be given; omitted buckets stay 0. These add on top
    of the rolled-up raw-material cost to make the FG's cost realistic.
    """
    amounts: dict[str, float] = {bucket: 0 for bucket, _ in OTHER_CHARGES_BUCKETS}
    for raw_key, value in (charges_spec or {}).items():
        bucket = CHARGE_KEY_MAP.get(str(raw_key).strip().lower())
        if bucket is None:
            sys.exit(f"[BOM.charges] has unknown key {raw_key!r}; valid keys: {sorted(CHARGE_KEY_MAP)}")
        if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
            sys.exit(f"[BOM.charges].{raw_key} must be a non-negative number, got {value!r}")
        amounts[bucket] = value
    return {key: {"charges": amounts[key], "comment": "", "classification": label}
            for key, label in OTHER_CHARGES_BUCKETS}


def build_scrap_row(product: dict[str, Any], unit: dict[str, Any], quantity: float) -> dict[str, Any]:
    """A scrap byproduct row. Same enriched product shape as an RM row but with
    ``cost_alloc`` 0 — scrap carries no manufacturing-cost share; its recovery
    VALUE is the scrap item's own master price × quantity, which the backend
    credits against the finished good's cost. (Verified accepted by
    /production/bom/create/ and persisted by the view.)"""
    row = base_product_row(product, unit, quantity)
    row["cost_alloc"] = 0
    return row


def build_create_payload(base_data: dict[str, Any], store_id: int, wip_store_id: int,
                         series_list: list[dict[str, Any]],
                         fg_row: dict[str, Any], rm_rows: list[dict[str, Any]],
                         bom_name: str,
                         other_charges: dict[str, Any] | None = None,
                         scrap_rows: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    scrap_rows = scrap_rows or []
    if other_charges is None:
        other_charges = build_other_charges(None)
    doc_data = base_data["docData"]
    pdd_defaults = doc_data.get("primary_document_details") or {}
    custom_fields = flatten_doc_custom_fields(base_data)
    doc_date = datetime.datetime.now().strftime("%d/%m/%Y - %H:%M")
    return {
        "data": {
            "primary_document_details": {
                "is_manual": 0,
                "doc_number": series_list[0]["id"],
                "auto_doc_numbers": series_list,
                "doc_date": doc_date,
                "doc_name": bom_name,
                "doc_description": "",
                "doc_rm_store": store_id,
                "doc_fg_store": store_id,
                "doc_scrap_store": store_id,
                "doc_wip_store": wip_store_id,
                "doc_created_by": pdd_defaults.get("doc_created_by", ""),
                "doc_reference_no": "",
                "doc_comment": "",
                "doc_bom_description": "",
                "custom_fields": custom_fields,
                "action_info": [],
            },
            "is_sub_contract": 0,
            "status": "",
            "attachments": {"existing_attachments": []},
            "finished_goods": [fg_row],
            "raw_materials": rm_rows,
            "routing": {},
            "routing_change_history": [],
            "scrap": scrap_rows,
            "other_charges": other_charges,
            "action_details": doc_data["action_details"],
            "doc_title": "Bill of Material",
            "doc_type": BOM_DOC_TYPE_STR,
            "doc_type_id": BOM_DOC_TYPE_ID,
            "sum_rm_quantity": 0,
            "sum_scrap_quantity": sum(float(r.get("quantity") or 0) for r in scrap_rows),
        },
        "is_draft": False,
        "action": "create",
        "save_action": "save_and_publish",
        "id": "0",
    }


def fetch_bom_view(token: str, bom_id: int) -> dict[str, Any]:
    return _get(token, "/production/general/view/", params={
        "id": bom_id, "doc_type": BOM_DOC_TYPE_ID, "action": "view",
    })["data"]["docData"]


# --- Business flow ----------------------------------------------------------


def main() -> None:
    token = login()

    boms = DATA.get("BOM") or []
    if not boms:
        raise RuntimeError("data.md has no [[BOM]] entries.")

    log.info("Fetching BOM base-data, store and number series.")
    base_data = fetch_base_data(token)
    stores = fetch_all_stores(token)
    store_id = first_non_reject_store(stores)
    wip_store_id, wip_src = resolve_wip_store(base_data, stores, store_id)
    log.info("Stores: rm/fg/scrap=%s, doc_wip_store=%s (%s)", store_id, wip_store_id, wip_src)
    series_list = fetch_number_series(token, store_id)
    if not series_list:
        raise RuntimeError(f"No BOM number series available for store_id={store_id}.")

    prefetch_products(token)  # one product-list call up front; per-row lookups hit the cache

    palette = ["#A3D5FD", "#FFC09F", "#B5EAD7", "#F6C6EA", "#FFE6A7"]
    created_bom_ids: list[int] = []

    for bom_idx, bom_spec in enumerate(boms):
        fg_spec = bom_spec["FG"]
        rm_specs = bom_spec.get("RM") or []
        bom_name = f"BOM - {fg_spec['name']}"
        log.info("=== Creating BOM %d/%d: %r ===", bom_idx + 1, len(boms), bom_name)

        log.info("Resolving FG %r (qty %s %s).", fg_spec["name"], fg_spec["qty"], fg_spec["unit"])
        fg_product = find_product_by_name(token, fg_spec["name"])
        fg_unit = pick_unit(fg_product, fg_spec["unit"])
        fg_row = build_fg_row(fg_product, fg_unit, float(fg_spec["qty"]))

        rm_rows: list[dict[str, Any]] = []
        linked_rm_item_ids: list[int] = []
        expected_view_rows = 0
        for idx, spec in enumerate(rm_specs):
            log.info("Resolving RM %r (qty %s %s).", spec["name"], spec["qty"], spec["unit"])
            product = find_product_by_name(token, spec["name"])
            unit = pick_unit(product, spec["unit"])
            rm_quantity = float(spec["qty"])
            rm_row = build_rm_row(product, unit, rm_quantity,
                                  float(fg_spec["qty"]), idx + 1,
                                  palette[idx % len(palette)])
            expected_view_rows += 1  # the top-level RM itself

            child_selector = spec.get("child_bom")
            if child_selector:
                log.info("  RM %r requests a child BOM (selector=%r).",
                         spec["name"], child_selector)
                attach_child_bom(token, rm_row, product["id"], unit["id"],
                                 rm_quantity, idx + 1, palette, child_selector)
                linked_rm_item_ids.append(product["id"])
                # The view flattens the child's ENTIRE sub-tree (grandchildren and
                # deeper) into top-level rows, so count it recursively — not just the
                # immediate child_rm rows, which would under-count at 3+ levels.
                expected_view_rows += count_flattened_rm_rows(token, rm_row["child_bom_id"])

            rm_rows.append(rm_row)

        # --- Realistic costing: labour/machinery/electricity/other charges + scrap ---
        other_charges = build_other_charges(bom_spec.get("charges"))
        scrap_rows: list[dict[str, Any]] = []
        for s_spec in bom_spec.get("scrap") or []:
            log.info("Resolving scrap %r (qty %s %s).", s_spec["name"], s_spec["qty"], s_spec["unit"])
            s_product = find_product_by_name(token, s_spec["name"])
            s_unit = pick_unit(s_product, s_spec["unit"])
            scrap_rows.append(build_scrap_row(s_product, s_unit, float(s_spec["qty"])))

        total_charges = sum(b["charges"] for b in other_charges.values())
        log.info("Creating BOM %r with FG %r, %d RM(s), %d linked child BOM(s), "
                 "charges=%s, %d scrap row(s).",
                 bom_name, fg_spec["name"], len(rm_rows), len(linked_rm_item_ids),
                 total_charges, len(scrap_rows))

        # Resilient create. `doc_wip_store` is the documented #1 cause of the
        # create 500 ("Something went wrong!"): if the resolved store is wrong for
        # this env, cycle through the other non-reject stores as doc_wip_store
        # before giving up. On total failure, dump the exact payload + server
        # response so the real cause is pinpointed in one run, not guessed.
        non_reject_ids = [s["id"] for s in stores if s.get("is_reject") == 0]
        wip_candidates = [wip_store_id] + [sid for sid in non_reject_ids if sid != wip_store_id]
        create_resp = None
        payload = None
        last_status: int | None = None
        last_body = ""
        for attempt_i, cand_wip in enumerate(wip_candidates):
            payload = build_create_payload(base_data, store_id, cand_wip, series_list, fg_row,
                                           rm_rows, bom_name, other_charges=other_charges,
                                           scrap_rows=scrap_rows)
            if attempt_i:
                log.warning("  create failed (HTTP %s) — retrying %r with doc_wip_store=%s "
                            "(store %d/%d).", last_status, bom_name, cand_wip,
                            attempt_i + 1, len(wip_candidates))
            ok, status, resp_json, body = _post_bom_create(token, payload)
            if ok:
                create_resp = resp_json
                if attempt_i:
                    log.info("  BOM %r created after store fallback (doc_wip_store=%s).",
                             bom_name, cand_wip)
                break
            last_status, last_body = status, body

        if create_resp is None:
            dump_path = Path(__file__).resolve().parent / f"bom_create_failure_{bom_idx + 1}.json"
            dump_path.write_text(json.dumps(
                {"bom_name": bom_name, "last_status": last_status,
                 "stores_tried": wip_candidates, "last_response": last_body,
                 "payload": payload}, indent=2, default=str), encoding="utf-8")
            raise RuntimeError(
                f"BOM {bom_name!r} create failed after trying {len(wip_candidates)} store(s) as "
                f"doc_wip_store (last HTTP {last_status}). Full payload + server response written to "
                f"{dump_path}. Server said: {last_body[:300]}")

        if create_resp.get("status") != 1:
            raise RuntimeError(f"BOM create did not return status=1; response: {create_resp}")
        bom_id = (create_resp.get("data") or {}).get("id")
        if not bom_id or str(bom_id).strip() in ("", "0"):
            raise RuntimeError(f"BOM create did not return a usable id; response: {create_resp}")
        log.info("BOM created: id=%s", bom_id)

        view = fetch_bom_view(token, bom_id)
        persisted_name = (view.get("primary_document_details") or {}).get("doc_name")
        if persisted_name != bom_name:
            raise RuntimeError(
                f"BOM doc_name mismatch — expected {bom_name!r}, view returned {persisted_name!r}"
            )
        fg = view.get("finished_goods")
        if isinstance(fg, list):
            if len(fg) != 1:
                raise RuntimeError(f"BOM view returned {len(fg)} finished_goods rows; expected 1.")
            fg = fg[0]
        if (fg or {}).get("itemid") != fg_product["itemid"]:
            raise RuntimeError(
                f"BOM FG itemid mismatch — expected {fg_product['itemid']!r}, "
                f"view returned {(fg or {}).get('itemid')!r}"
            )
        # On read-back the view flattens nested child RMs into top-level rows, so the
        # expected count is top-level RMs + each linked RM's full flattened sub-tree.
        rms = view.get("raw_materials") or []
        if len(rms) != expected_view_rows:
            raise RuntimeError(
                f"BOM view returned {len(rms)} raw_materials; expected {expected_view_rows} "
                f"({len(rm_specs)} top-level + flattened child RMs)."
            )
        # Each linked RM must persist with a non-zero child_bom_id marking the sub-assembly.
        for item_id in linked_rm_item_ids:
            match = next((r for r in rms if r.get("item_id") == item_id
                          or r.get("id") == item_id), None)
            if not match or not (match.get("child_bom_id") or 0):
                raise RuntimeError(
                    f"Linked RM item_id={item_id} did not persist a child_bom_id in the BOM view."
                )
        # Costing persisted as sent: each declared charge bucket + the scrap rows.
        # The backend normalizes other_charges to PER-UNIT — it divides each charge
        # by the FG quantity before storing — so the view returns charge / FG-qty,
        # not the as-sent total. Compare against that per-unit value; otherwise a BOM
        # with charges and FG qty > 1 fails here AFTER it was already created fine.
        if bom_spec.get("charges"):
            fg_qty = float(fg_spec["qty"]) or 1.0
            view_oc = view.get("other_charges") or {}
            for bucket, _label in OTHER_CHARGES_BUCKETS:
                sent = float(other_charges[bucket]["charges"])
                expected = sent / fg_qty
                got = float((view_oc.get(bucket) or {}).get("charges") or 0)
                if abs(got - expected) > 0.001:
                    raise RuntimeError(
                        f"BOM other_charges[{bucket}] mismatch — expected per-unit {expected} "
                        f"(sent {sent} for FG qty {fg_qty}), view returned {got}."
                    )
        if scrap_rows:
            view_scrap = view.get("scrap") or []
            if len(view_scrap) != len(scrap_rows):
                raise RuntimeError(
                    f"BOM view returned {len(view_scrap)} scrap row(s); expected {len(scrap_rows)}."
                )
        created_bom_ids.append(bom_id)

    log.info("Done. Created %d BOM(s): %s", len(created_bom_ids), created_bom_ids)


if __name__ == "__main__":
    main()
