"""Add demo inventory items (products) to a Tranzact account — in ONE bulk upload.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available). STDLIB + requests ONLY — the
xlsx the bulk endpoint needs is generated with the standard-library `zipfile`
module (no openpyxl/pandas).

The list of items to create is derived from the `[[BOM]]` blocks in data.md
(the sole source of truth). Each block has one `[BOM.FG]` finished-good
table and zero-or-more `[[BOM.RM]]` raw-material rows. Each row needs:
name, type ("Buy" | "Sell" | "Both"), unit, qty, price. Items appearing in
multiple BOMs must agree on `type` and `unit` — the first occurrence's
per-unit price (`price / qty`) wins. The `unit` must already exist as a Unit of
Measurement on the company (seeded by 002); the script fails fast if it doesn't.

Bulk creation
-------------
Instead of one API call per item, every new item is written into a single Excel
sheet (TranZact's item-import template columns) and POSTed once to
`/ops_dashboard/product_api/product_submit/`. The server auto-numbers Item IDs
from the account's product series (we pass the series *prefix* as the Item ID and
it fills in the running number). This sets name, Product/Service, type
(Buy/Sell/Both), unit, price and GST in one shot. Opening stock is intentionally
NOT set (the bulk template has no field for it; it is cosmetic and nothing
downstream requires it). Re-running is safe — items already present are skipped
before the upload.
"""

from __future__ import annotations

import base64
import io
import logging
import re
import sys
import tomllib
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

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
UPLOAD_TIMEOUT = 120  # the bulk upload does server-side parse+create; give it room

DEFAULT_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Tz-Request-Source": "webapp",
}

# Item-import template columns the bulk endpoint expects (exact labels; the
# "Item Type (Buy/Sell/Both)" header is required — the upload is rejected without
# it). Optional numeric columns (other prices, min/max stock level) are omitted:
# the endpoint rejects empty numeric cells, so we simply don't include them.
BULK_COLUMNS = [
    "Item ID",
    "Item Name",
    "Product/Service",
    "Item Type (Buy/Sell/Both)",
    "Unit of Measurement",
    "Default Price",
    "Tax",
]

_VALID_TYPES = {"Buy", "Sell", "Both"}
_REQUIRED_STR_KEYS = ("name", "type", "unit")

# HARD server-load ceiling on the number of inventory items a single run may create.
# This is a deterministic backstop, NOT a soft guideline: 003 is the only script that
# creates items, and it aborts BEFORE creating anything if the deduped item set exceeds
# this cap — so it can never be breached regardless of what the BOM tree in data.md
# contains. The agent designs the tree to stay under it (see SKILL.md); this is the
# guarantee that it always does.
MAX_ITEMS = 20


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


def _post_multipart(token: str, path: str, files: dict) -> dict:
    """POST multipart/form-data. Must NOT set Content-Type — requests adds the
    boundary. Used for the Excel bulk upload."""
    url = f"{BASE_URL}{path}"
    headers = {
        "Accept": "application/json",
        "Tz-Request-Source": "webapp",
        "Authorization": f"Bearer {token}",
    }
    log.info(">>> POST %s (multipart)", path)
    response = requests.post(url, files=files, headers=headers, timeout=UPLOAD_TIMEOUT)
    log.info("<<< POST %s -> %d", path, response.status_code)
    if response.status_code >= 400:
        sys.exit(f"POST {path} failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- Minimal stdlib xlsx writer/reader (no openpyxl) ------------------------


def _col_letter(idx: int) -> str:
    """0-based column index -> spreadsheet column letter (0->A, 26->AA)."""
    out = ""
    idx += 1
    while idx > 0:
        idx, rem = divmod(idx - 1, 26)
        out = chr(65 + rem) + out
    return out


def build_xlsx(rows: list[list[Any]]) -> bytes:
    """Build a minimal single-sheet .xlsx from `rows` (list of cell lists) using
    only the standard library. Text cells use inline strings; ints/floats are
    written as numbers."""
    def cell(letter: str, r: int, value: Any) -> str:
        ref = f"{letter}{r}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return f'<c r="{ref}"><v>{value}</v></c>'
        text = escape("" if value is None else str(value))
        return f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">{text}</t></is></c>'

    sheet_rows = "".join(
        f'<row r="{ri}">' + "".join(cell(_col_letter(ci), ri, v) for ci, v in enumerate(row)) + "</row>"
        for ri, row in enumerate(rows, start=1)
    )
    sheet = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{sheet_rows}</sheetData></worksheet>"
    )
    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        "</Types>"
    )
    root_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )
    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        '<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets></workbook>'
    )
    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        "</Relationships>"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", root_rels)
        z.writestr("xl/workbook.xml", workbook)
        z.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        z.writestr("xl/worksheets/sheet1.xml", sheet)
    return buf.getvalue()


def read_xlsx_rows(data: bytes) -> list[list[str]]:
    """Best-effort read of an .xlsx (the server's error file) into rows of text,
    using only the standard library. Handles shared strings and inline strings."""
    import xml.etree.ElementTree as ET

    ns = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
    z = zipfile.ZipFile(io.BytesIO(data))
    shared: list[str] = []
    if "xl/sharedStrings.xml" in z.namelist():
        for si in ET.fromstring(z.read("xl/sharedStrings.xml")).findall(f"{ns}si"):
            shared.append("".join(t.text or "" for t in si.iter(f"{ns}t")))
    sheet_name = next((n for n in z.namelist() if n.startswith("xl/worksheets/") and n.endswith(".xml")), None)
    if not sheet_name:
        return []
    out: list[list[str]] = []
    for row in ET.fromstring(z.read(sheet_name)).iter(f"{ns}row"):
        cells: list[str] = []
        for c in row.findall(f"{ns}c"):
            ctype = c.get("t")
            v = c.find(f"{ns}v")
            if ctype == "s" and v is not None and v.text is not None:
                cells.append(shared[int(v.text)])
            elif ctype == "inlineStr":
                is_el = c.find(f"{ns}is")
                cells.append("".join(t.text or "" for t in is_el.iter(f"{ns}t")) if is_el is not None else "")
            else:
                cells.append(v.text if v is not None and v.text is not None else "")
        out.append(cells)
    return out


# --- Master-data lookups ----------------------------------------------------


def fetch_masters(token: str) -> dict:
    res = _get(token, "/inventory/main-inventory/get_details_for_add_items/")
    return res.get("data", {})


def pick_default_gst_rate(masters: dict) -> str | None:
    """Return a GST rate string (e.g. "18") for the bulk `Tax` column, or None.

    The bulk upload maps `Tax` by percentage rate (unlike the single-item endpoint
    which took a tax_id). We read the account's GST tax masters — each carries a
    `tax_name` like "Tax:18%" — and prefer 18% (India's common rate), else the
    first GST rate found. Downstream sales/PO scripts require items to carry a GST
    mapping, so every item gets this rate.
    """
    tax_type_map = masters.get("tax_type") or {}
    taxes = masters.get("taxes") or []
    if not isinstance(taxes, list):  # defensive — backend returns {} when empty
        return None
    rates: list[str] = []
    for tax in taxes:
        if not isinstance(tax, dict):
            continue
        if tax_type_map.get(str(tax.get("id"))) != "gst":
            continue
        m = re.search(r"(\d+(?:\.\d+)?)\s*%", str(tax.get("tax_name") or ""))
        if m:
            rates.append(m.group(1))
    if not rates:
        return None
    return "18" if "18" in rates else rates[0]


def fetch_series_prefix(token: str) -> str:
    """Return the account's product-series prefix (e.g. "RM/"). Passed as the Item
    ID in the bulk sheet; the server auto-numbers each row from the series."""
    data = _post(token, "/inventory/main-inventory/get_product_series/", {}).get("data", {})
    if data.get("product_series_type") != "series":
        raise RuntimeError("Account is in manual Item-ID mode; bulk item upload needs series mode.")
    nums = data.get("product_number") or []
    if not nums:
        raise RuntimeError("No product series configured on the account.")
    prefix = nums[0].get("prefix")
    if not prefix:
        raise RuntimeError(f"First product series has no prefix: {nums[0]!r}")
    return prefix


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


def validate_units_exist(token: str) -> None:
    """Fail fast if any item references a UoM the company doesn't have (002 seeds them)."""
    masters = fetch_masters(token)
    available = {(u.get("unit_name") or "").strip() for u in masters.get("master_units") or []}
    missing = sorted({item["unit"] for item in ITEMS if item["unit"] not in available})
    if missing:
        raise RuntimeError(
            f"UoM(s) not configured on the company: {missing}. "
            f"Add them under Settings → Master UoM, then re-run."
        )


# --- Bulk create ------------------------------------------------------------


def _error_reasons(data: dict) -> str:
    """Extract per-row failure reasons from the server's error file (base64 xlsx)."""
    dl = (data.get("download") or {}).get("data")
    if not dl:
        return "(no error file returned)"
    try:
        rows = read_xlsx_rows(base64.b64decode(dl))
        msgs = [str(r[-1]).replace("\n", " ").strip() for r in rows[1:] if r and r[-1]]
        return " | ".join(msgs[:10]) or "(error file had no messages)"
    except Exception as exc:  # noqa: BLE001 — surface a readable hint, don't crash
        return f"(could not parse error file: {exc})"


def ensure_products(token: str) -> None:
    """Create every missing product in one bulk Excel upload, attaching GST."""
    masters = fetch_masters(token)
    gst_rate = pick_default_gst_rate(masters)
    if gst_rate is None:
        raise RuntimeError(
            "No GST tax master found on the company — downstream sales-doc scripts "
            "(010_, 011_) require sell-side products to carry a GST mapping. "
            "Enable GST 18% under Settings → Tax Options and re-run."
        )
    prefix = fetch_series_prefix(token)
    log.info("Default GST rate %s%% | item-ID series prefix %r (server auto-numbers).", gst_rate, prefix)

    to_create = []
    for item in ITEMS:
        if search_product(token, item["name"], item["type"]):
            log.info("Product already exists, skipping: %s (%s)", item["name"], item["type"])
        else:
            to_create.append(item)

    if not to_create:
        log.info("All %d products already exist; nothing to upload.", len(ITEMS))
        return

    rows: list[list[Any]] = [BULK_COLUMNS]
    for item in to_create:
        rows.append(
            [prefix, item["name"], "Product", item["type"], item["unit"], item["price"], gst_rate]
        )
    xlsx = build_xlsx(rows)

    files = {
        "input_excel": (
            "Product_Add.xlsx",
            xlsx,
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ),
        "upload_action": (None, "add"),
        "ignore_duplicate_name": (None, "1"),
        "preserve_existing": (None, "1"),
        "update_cf_in_bom_items": (None, "1"),
    }
    log.info("Bulk-creating %d product(s) in a single upload...", len(to_create))
    resp = _post_multipart(token, "/ops_dashboard/product_api/product_submit/", files)
    data = resp.get("data") or {}
    uploaded = data.get("no_of_items_uploaded")
    if uploaded != len(to_create):
        raise RuntimeError(
            f"Bulk upload created {uploaded} of {len(to_create)} item(s). "
            f"Server: {data.get('message')!r}. Row errors: {_error_reasons(data)}"
        )
    log.info("Bulk upload succeeded: %d product(s) created.", uploaded)


# --- Main -------------------------------------------------------------------


def main() -> None:
    # HARD cap — enforced before any network call so a breach creates nothing.
    if len(ITEMS) > MAX_ITEMS:
        sys.exit(
            f"Item cap exceeded: data.md resolves to {len(ITEMS)} unique inventory items, "
            f"but the hard maximum is {MAX_ITEMS}. No items were created. Trim the BOM tree "
            f"(fewer or shallower sub-assemblies, prune leaf materials) so the deduped item "
            f"set is <= {MAX_ITEMS}, then re-run from 003."
        )

    log.info("=== Login ===")
    token = login()

    log.info("=== Validating UoMs ===")
    validate_units_exist(token)

    log.info("=== Bulk-adding %d products (derived from BOM) ===", len(ITEMS))
    ensure_products(token)

    log.info("=== Done ===")


if __name__ == "__main__":
    main()
