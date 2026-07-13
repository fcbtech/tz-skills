"""PO → split Inwards (40% + 60%) → two QIRs (100% / 90% accept) → PRDC (rejected) → Invoice (full PO qty).

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Picks the first supplier in the company's network, one buyable goods
product (item 15 of the product catalog) linked to that supplier, and a random PO quantity. The chain then
exercises a full Quality-Inspection + return flow:

  1. Direct PO — 1 item, random qty, product's own tax (fall back to 18% GST).
  2. Inward #1 — receives floor(po_qty * 0.40) units.
  3. QIR #1   — accepts 100% of inward #1.
  4. Inward #2 — receives the remaining qty.
  5. QIR #2   — accepts floor(inward2 * 0.90); rejects the remaining 10%.
  6. PRDC     — returns the rejected qty to the original supplier (role flip).
  7. Invoice  — full PO quantity (regardless of returns).
"""

from __future__ import annotations

import logging
import math
import random
import re
import sys
import time
import tomllib
import uuid as _uuid
from datetime import date
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

# Automation identity — edit here for a one-off run.
PO_DOC_TYPE_INT = 1
INVOICE_DOC_TYPE_INT = 2
INWARD_DOC_TYPE_INT = 3
QIR_DOC_TYPE_INT = 5
PRDC_DOC_TYPE_INT = 44

SUPPLIER_INDEX = 0  # 0 = first supplier in counter-party list.
NUM_ITEMS = 1
ITEM_OFFSET = 14  # which item in the sorted product list to use; diversifies items across docs
PO_QTY_MIN = 50
PO_QTY_MAX = 200
INWARD1_FRACTION = 0.40
QIR2_ACCEPT_FRACTION = 0.90
FALLBACK_TAX_RATE = 0.18
TRANSACTION_TITLE = "PO Split-Inward QIR PRDC Invoice auto-test"

INR_CURRENCY = {
    "currency_name": "Rupees",
    "currency_code": "INR",
    "currency_hashcode": "8377",
    "currency_conversion_rate": "1",
    "currency_symbol": "₹",
    "currency_style": "en-IN",
    "currency_dropdown_value": "₹",
    "currency_value": 1,
}

EXPORT_DETAILS = {
    "show_igst": False,
    "originCountry": "India",
    "dischargeCountry": "India",
    "finalDestinationCountry": "India",
}

EMPTY_RECIPIENTS = {
    "selectedRecipients": [],
    "cancelledRecipients": [],
    "subject": None,
    "introLine": None,
    "closingLine": None,
    "additionalRecipients": [],
}

OPTIONAL_COLUMNS = [
    {"label": "Alternate Unit", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 1", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 2", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 3", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Total Tax", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Delivery Date", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Comments", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
]

INWARD_OPTIONAL_COLUMNS = [
    {"label": "Alternate Unit", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
]


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


def _get(token: str, path: str, params: dict | None = None) -> dict | list:
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


# --- Helpers ----------------------------------------------------------------


def _today_ddmmyyyy() -> str:
    return date.today().strftime("%d/%m/%Y")


def _addr_list(resp: Any) -> list[dict]:
    if isinstance(resp, list):
        return resp
    return resp.get("data") or []


def _pick_default(rows: list[dict]) -> dict:
    for row in rows:
        if row.get("default"):
            return row
    if not rows:
        raise RuntimeError("No rows available to pick from")
    return rows[0]


def _minimal_tax_entry(tax_master: dict) -> dict:
    return {
        "id": tax_master["id"],
        "tax_id": tax_master["id"],
        "tax_type": tax_master["tax_type"],
        "tax_name": tax_master["tax_name"],
        "tax_percentage": tax_master["tax_percentage"],
    }


def _billing_block(addr: dict, company_id: int) -> dict:
    return {
        "id": addr["id"],
        "address1": addr.get("address1", ""),
        "address2": addr.get("address2", ""),
        "city": addr.get("city", ""),
        "state": addr.get("state", ""),
        "state_code": addr.get("state_code", ""),
        "country": addr.get("country", ""),
        "pin": addr.get("pin", ""),
        "gstin": addr.get("gstin", ""),
        "gstintype": addr.get("gstintype", ""),
        "name": addr.get("name", ""),
        "default": addr.get("default", 0),
        "company_id": company_id,
    }


def _to_custom_fields_list(cf_dict: dict) -> list:
    rows = []
    for cf_uuid, cf_val in (cf_dict or {}).items():
        rows.append([{"uuid": cf_uuid, **cf_val}])
    return rows


def _doc_number_for(token: str, doc_type: str, self_id: int) -> tuple[dict, list]:
    resp = _get(
        token,
        "/settings/document-number/get_document_no/",
        params={"doc_type": doc_type, "is_service": 0, "company_id": self_id},
    )["data"]
    series = resp["doc_number"]
    if not series:
        sys.exit(f"No doc number series configured for {doc_type}")
    return series[0], series


def _resolve_product_tax(product: dict, taxes_by_id: dict) -> dict:
    """Use the product's first taxes[] mapping; fall back to FALLBACK_TAX_RATE GST master."""
    for t in product.get("taxes") or []:
        tax_id = t.get("tax_id") or t.get("id")
        if tax_id and tax_id in taxes_by_id:
            return taxes_by_id[tax_id]
    for t in taxes_by_id.values():
        if t.get("tax_type") == "gst" and abs(float(t["tax_percentage"]) - FALLBACK_TAX_RATE) < 1e-6:
            return t
    sys.exit("No tax master available — neither product-linked nor fallback GST.")


def _build_po_item(
    *,
    product: dict,
    quantity: int,
    tax_master: dict,
    self_id: int,
    delivery_date_str: str,
    position: int,
) -> dict:
    unit = product["units"][0]
    price = float(product.get("in_avg_price") or product.get("prices", {}).get("default") or 0)
    total_cost = round(price * quantity, 2)
    tax_pct = float(tax_master["tax_percentage"])
    tax_amount = round(total_cost * tax_pct, 2)
    return {
        "id": product["id"],
        "product": product["id"],
        "uuid": product.get("uuid", str(_uuid.uuid4())),
        "itemid": product.get("itemid", ""),
        "product_name": product["product_name"],
        "item_name": product["product_name"],
        "category_name": product.get("category_name", ""),
        "hsn_code": product.get("hsn_code") or "",
        "quantity": str(quantity),
        "price": price,
        "base_price": price,
        "discount": 0,
        "base_discount": 0,
        "total_cost": total_cost,
        "base_total_cost": total_cost,
        "tax": tax_amount,
        "base_tax": tax_amount,
        "base_total_amount": round(total_cost + tax_amount, 2),
        "unit": unit,
        "units": product["units"],
        "taxes": [_minimal_tax_entry(tax_master)],
        "taxes_data": {},
        "prices": product.get("prices", {}),
        "vendor_mapping": product.get("vendor_mapping"),
        "is_service": product.get("is_service", 0),
        "stock": product.get("stock", 0),
        "delivery_date": delivery_date_str,
        "position": position,
        "active": 1,
        "client": self_id,
        "comment": "",
        "discount_type": {"type": 0, "value": "₹"},
        "item_discount_1": "0",
        "item_discount_2": 0,
        "item_discount_3": 0,
        "item_discount_type_1": {"type": 1, "value": "%"},
        "item_discount_type_2": {"type": 1, "value": "%"},
        "item_discount_type_3": {"type": 1, "value": "%"},
        "item_discount_type": {"type": 0, "value": "₹"},
        "item_discount_total": 0,
        "item_level_doc_discount": 0,
        "item_level_doc_discount_type": {"type": 0, "value": "₹"},
        "discount_data": {},
        "custom_fields": [],
        "custom_fields_parsed": {},
        "inrConversionRate": "1",
        "key_mappings": {"text": "product_name", "value": "id"},
    }


def _rehydrate_item(ds_item: dict, tax_master: dict, products_by_id: dict) -> dict:
    item = dict(ds_item)
    item.pop("custom_fields_parsed", None)
    item.pop("custom_fields", None)
    item.pop("alternate_quantity", None)
    item.pop("alternate_unit", None)
    item.pop("alternate_units", None)
    item["taxes"] = [_minimal_tax_entry(tax_master)]
    item["taxes_data"] = {}
    item["discount_data"] = {}
    product_id = item.get("product") or item.get("id")
    item["id"] = product_id
    prod = products_by_id.get(product_id, {})
    unit_in = item.get("unit")
    if isinstance(unit_in, str):
        unit_obj = next((u for u in prod.get("units", []) if u.get("unit_name") == unit_in), None)
        item["unit"] = unit_obj or {"unit_name": unit_in}
    if not isinstance(item.get("units"), list):
        item["units"] = prod.get("units", [])
    if not item.get("product_name"):
        item["product_name"] = prod.get("product_name") or item.get("item_name", "")
    return item


def _extract_doc_id(create_resp: dict) -> int:
    data = create_resp.get("data") or {}
    for k in ("doc_id", "id", "document_id"):
        if isinstance(data.get(k), int):
            return data[k]
    doc = data.get("document") or {}
    for k in ("doc_id", "id"):
        if isinstance(doc.get(k), int):
            return doc[k]
    raise RuntimeError(f"Could not extract doc_id from create response: {list(data.keys())}")


def _fetch_view(token: str, doc_type_int: int, doc_id: int) -> dict:
    return _get(
        token,
        "/documents/document/view/",
        params={"doc_type": doc_type_int, "doc_id": doc_id},
    )


# --- Discovery --------------------------------------------------------------


def discover_context(token: str) -> dict[str, Any]:
    suppliers = _get(token, "/profile/counter-party/list/", params={"target_category": "supplier"})["data"][
        "results"
    ]
    if len(suppliers) <= SUPPLIER_INDEX:
        sys.exit(f"Need at least {SUPPLIER_INDEX + 1} supplier(s); got {len(suppliers)}.")
    supplier = suppliers[SUPPLIER_INDEX]
    sup_id = supplier["company_id"]
    sup_name = supplier["name"]

    tax_resp = _get(token, "/settings/tax/")["data"]["results"]
    taxes_by_id = {t["id"]: t for t in tax_resp if t.get("tax_type") == "gst"}

    prod_resp = _get(
        token,
        "/settings/product/",
        params={
            "product_type": "buy",
            "place": "product_name",
            "service_type": 0,
            "counter_party": sup_id,
        },
    )["data"]["results"]
    buyable = [p for p in prod_resp if not p.get("is_service")]
    if not buyable:
        sys.exit(f"Need a buyable good for supplier {sup_id}; got 0")
    product = buyable[min(ITEM_OFFSET, len(buyable) - 1)]
    tax_master = _resolve_product_tax(product, taxes_by_id)

    ds = _get(
        token,
        "/documents/po/get_doc_structure/",
        params={
            "type": "po",
            "doc_id": -1,
            "action": "create",
            "counter_company_id": sup_id,
            "transaction_id": 0,
            "po_type": "direct_po",
        },
    )
    self_id = ds["data"]["user"]["company"]
    doc_cf = ds["data"]["document_data"].get("custom_fields") or {}

    ba_buyer = _addr_list(
        _get(token, "/settings/billing-address/get-addresses/", params={"company_id": self_id})
    )
    ba_supplier = _addr_list(
        _get(token, "/settings/billing-address/get-addresses/", params={"company_id": sup_id})
    )
    dl_buyer = _addr_list(
        _get(token, "/settings/delivery-location/get-locations/", params={"company_id": self_id})
    )
    dl_supplier = _addr_list(
        _get(token, "/settings/delivery-location/get-locations/", params={"company_id": sup_id})
    )
    if not ba_buyer:
        sys.exit("Buyer company has no billing addresses")
    if not ba_supplier:
        sys.exit(f"Supplier {sup_id} has no billing addresses")
    if not dl_buyer:
        sys.exit("Buyer company has no delivery locations")
    if not dl_supplier:
        sys.exit(f"Supplier {sup_id} has no delivery locations — required for PRDC")

    stores = _get(token, "/inventory/store/", params={"type": "main", "doc_type": "po"})["data"]["results"]
    if not stores:
        sys.exit("No stores returned")
    store = next((s for s in stores if s.get("is_default")), stores[0])

    return {
        "supplier_id": sup_id,
        "supplier_name": sup_name,
        "self_company_id": self_id,
        "tax_master": tax_master,
        "product": product,
        "buyer_billing": _pick_default(ba_buyer),
        "supplier_billing": _pick_default(ba_supplier),
        "buyer_delivery": _pick_default(dl_buyer),
        "supplier_delivery": _pick_default(dl_supplier),
        "store": store,
        "doc_level_custom_fields": doc_cf,
    }


# --- Phase 1: PO -----------------------------------------------------------


def create_po(token: str, ctx: dict, po_qty: int) -> dict:
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]
    doc_number, series_list = _doc_number_for(token, "po", self_id)
    today = _today_ddmmyyyy()

    items = [
        _build_po_item(
            product=ctx["product"],
            quantity=po_qty,
            tax_master=ctx["tax_master"],
            self_id=self_id,
            delivery_date_str=today,
            position=0,
        )
    ]
    total_amount = sum(it["base_total_amount"] for it in items)

    payload = {
        "userCompany": {"id": self_id, "type": "buyer"},
        "details": {
            "docType": "po",
            "service": 0,
            "export": 0,
            "supplierId": sup_id,
            "buyerId": self_id,
            "userCompany": {"id": self_id, "type": "buyer"},
            "id": str(_uuid.uuid4()),
        },
        "transaction_id": 0,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": sup_id,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number,
            "customize_doc_number": "",
            "doc_date": today,
            "doc_amendment": 0,
            "delivery_date": today,
            "indent_details": {},
            "oc_details": {},
            "payment_terms": {},
            "select_oc": [],
            "store_details": ctx["store"],
            "exportDetails": EXPORT_DETAILS,
            "documentSeriesList": series_list,
            "customFields": _to_custom_fields_list(ctx["doc_level_custom_fields"]),
        },
        "buyerDetails": {
            "buyerCompanyDetails": {"company_id": self_id},
            "selectedBuyerBillingAddress": _billing_block(ctx["buyer_billing"], self_id),
            "selectedBuyerDeliveryLocation": _billing_block(ctx["buyer_delivery"], self_id),
            "addressPermission": 1,
            "kindAttention": "",
            "placeOfSupply": {
                "city": ctx["buyer_delivery"].get("city", ""),
                "state": ctx["buyer_delivery"].get("state", ""),
                "state_code": ctx["buyer_delivery"].get("state_code", ""),
                "country": ctx["buyer_delivery"].get("country", "India"),
            },
        },
        "buyerId": self_id,
        "deliveryLocationCompanyId": self_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": sup_id, "name": ctx["supplier_name"]},
            "selectedSupplierBillingAddress": _billing_block(ctx["supplier_billing"], sup_id),
            "addressPermission": 1,
        },
        "supplierId": sup_id,
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 1,
        "documentBlockDetails": {"docType": "po", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": total_amount,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "transaction": {"title": TRANSACTION_TITLE, "uuid": str(_uuid.uuid4())},
        "sq_id": 0,
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": {
                "doc_discount_1": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_2": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_3": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
            },
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": None,
            "baseAdvanceToPay": None,
        },
        "tcsDetails": {"amount": 0},
        "emailRecipients": EMPTY_RECIPIENTS,
        "approvalData": {"approvalRuleType": "", "approvalMsg": "", "hasPermission": False},
        "checkApproval": True,
        "po_type": "direct_po",
    }
    return _post(token, "/documents/po/create/", payload)


# --- Phase 2 / 4: Inward ----------------------------------------------------


def create_inward(token: str, ctx: dict, po_view: dict, qty_to_receive: int) -> dict:
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]
    po_doc = po_view["data"]["document_data"]
    transaction_id = po_doc["transaction"]["id"]
    po_uuid = po_doc["transaction"].get("uuid")

    ds = _get(
        token,
        "/documents/inward/get_doc_structure/",
        params={
            "type": "inward",
            "doc_id": -1,
            "action": "create",
            "counter_company_id": sup_id,
            "transaction_id": transaction_id,
            "inward_type": "inward_from_po_oc",
        },
    )
    dd = ds["data"]["document_data"]
    ds_items = dd.get("items") or []
    products = {p["id"]: p for p in (dd.get("products") or [])}
    if not ds_items:
        sys.exit("Inward doc_structure carries no items")

    today = _today_ddmmyyyy()
    doc_number, series_list = _doc_number_for(token, "inward", self_id)

    items = []
    for pos, raw in enumerate(ds_items):
        item = _rehydrate_item(raw, ctx["tax_master"], products)
        item["quantity"] = str(qty_to_receive)
        item["product_delivered_now"] = str(qty_to_receive)
        item["delivery_date"] = today
        item["position"] = pos
        item["active"] = 1
        item["client"] = self_id
        items.append(item)

    payload = {
        "userCompany": {"id": self_id, "type": "buyer"},
        "details": {
            "docType": "inward",
            "service": 0,
            "export": 0,
            "supplierId": sup_id,
            "buyerId": self_id,
            "userCompany": {"id": self_id, "type": "buyer"},
            "id": dd.get("uuid") or po_uuid,
        },
        "transaction_id": transaction_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": sup_id,
        "itemDetails": {"optionalColumns": INWARD_OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number,
            "customize_doc_number": "",
            "doc_date": today,
            "doc_amendment": 0,
            "delivery_date": today,
            "po_details": {
                "poNumber": po_doc.get("document_no_text") or po_doc.get("po_number") or "",
                "poDate": today,
            },
            "challan_details": {},
            "invoice_details": {},
            "transportation_details": {
                "transporterName": "",
                "vehicleNo": "",
                "transportationDocNo": "",
                "transportationDocDate": "",
                "transportersList": [],
            },
            "store_details": ctx["store"],
            "exportDetails": EXPORT_DETAILS,
            "documentSeriesList": series_list,
        },
        "buyerDetails": {
            "buyerCompanyDetails": {"company_id": self_id},
            "selectedBuyerBillingAddress": _billing_block(ctx["buyer_billing"], self_id),
            "selectedBuyerDeliveryLocation": _billing_block(ctx["buyer_delivery"], self_id),
            "addressPermission": 1,
        },
        "buyerId": self_id,
        "deliveryLocationCompanyId": self_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": sup_id, "name": ctx["supplier_name"]},
            "selectedSupplierBillingAddress": _billing_block(ctx["supplier_billing"], sup_id),
            "addressPermission": 1,
        },
        "supplierId": sup_id,
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "inward", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": 0,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
    }
    return _post(token, "/documents/inward/create/", payload)


# --- Phase 3 / 5: QIR -------------------------------------------------------


def create_qir(
    token: str, ctx: dict, inward_id: int, transaction_id: int, qty_accepted_by_pos: list[int]
) -> dict:
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]

    ds = _get(
        token,
        "/documents/qir/get_doc_structure/",
        params={
            "type": "qir",
            "doc_id": -1,
            "action": "create",
            "counter_company_id": sup_id,
            "transaction_id": transaction_id,
            "qir_type": "qir_from_inward",
            "inward_id": inward_id,
        },
    )
    dd = ds["data"]["document_data"]
    ds_items = dd.get("items") or []
    products = {p["id"]: p for p in (dd.get("products") or [])}
    if not ds_items:
        sys.exit("QIR doc_structure carries no items")
    today = _today_ddmmyyyy()
    doc_number, series_list = _doc_number_for(token, "qir", self_id)

    items = []
    for pos, raw in enumerate(ds_items):
        item = _rehydrate_item(raw, ctx["tax_master"], products)
        item["quantity_accepted"] = 0
        item["product_accepted"] = str(qty_accepted_by_pos[pos])
        item["delivery_date"] = today
        item["position"] = pos
        item["active"] = 1
        item["client"] = self_id
        items.append(item)

    payload = {
        "userCompany": {"id": self_id, "type": "buyer"},
        "details": {
            "docType": "qir",
            "service": 0,
            "export": 0,
            "supplierId": sup_id,
            "buyerId": self_id,
            "userCompany": {"id": self_id, "type": "buyer"},
            "id": dd.get("uuid") or str(_uuid.uuid4()),
            "transaction": dd.get("transaction") or {},
        },
        "transaction_id": transaction_id,
        "inward_id": inward_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": sup_id,
        "itemDetails": {"optionalColumns": INWARD_OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number,
            "customize_doc_number": "",
            "doc_date": today,
            "doc_amendment": 0,
            "delivery_date": today,
            "po_details": {
                "poNumber": dd.get("po_number") or "",
                "poDate": today,
            },
            "inward_details": {
                "inwardNumber": dd.get("inward_number") or dd.get("document_no_text") or "",
                "inwardDate": today,
            },
            "testing_date": "",
            "testingDate": today,
            "store_details": ctx["store"],
            "exportDetails": EXPORT_DETAILS,
            "documentSeriesList": series_list,
        },
        "buyerDetails": {
            "buyerCompanyDetails": {"company_id": self_id},
            "selectedBuyerBillingAddress": _billing_block(ctx["buyer_billing"], self_id),
            "selectedBuyerDeliveryLocation": _billing_block(ctx["buyer_delivery"], self_id),
            "addressPermission": 1,
        },
        "buyerId": self_id,
        "deliveryLocationCompanyId": self_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": sup_id, "name": ctx["supplier_name"]},
            "selectedSupplierBillingAddress": _billing_block(ctx["supplier_billing"], sup_id),
            "addressPermission": 1,
        },
        "supplierId": sup_id,
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "qir", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": 0,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
    }
    return _post(token, "/documents/qir/create/", payload)


# --- Phase 6: PRDC ----------------------------------------------------------


def create_prdc(token: str, ctx: dict, po_view: dict, return_qty: int) -> dict:
    """Purchase Return Delivery Challan — buyer returns rejected goods to original supplier."""
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]
    po_doc = po_view["data"]["document_data"]
    transaction_id = po_doc["transaction"]["id"]

    ds = _get(
        token,
        "/documents/purchase_return_challan/get_doc_structure/",
        params={
            "type": "purchase_return_challan",
            "doc_id": -1,
            "action": "create",
            "counter_company_id": sup_id,
            "transaction_id": transaction_id,
            "purchase_return_type": "purchase_return_challan_from_po_oc",
        },
    )
    dd = ds["data"]["document_data"]
    ds_items = dd.get("items") or []
    products = {p["id"]: p for p in (dd.get("products") or [])}
    if not ds_items:
        sys.exit("PRDC doc_structure carries no items")
    today = _today_ddmmyyyy()
    doc_number, series_list = _doc_number_for(token, "purchase_return_challan", self_id)

    items = []
    for pos, raw in enumerate(ds_items):
        item = _rehydrate_item(raw, ctx["tax_master"], products)
        item["quantity"] = str(return_qty)
        item["delivery_date"] = today
        item["position"] = pos
        item["active"] = 1
        item["client"] = self_id
        items.append(item)

    # Party flip: PRDC dispatches FROM buyer TO supplier, so logged-in company plays "supplier" role
    # and the original supplier plays "buyer". The "buyer's" delivery location MUST be the original
    # supplier's delivery location (PRDC fails HTTP 404 DeliveryLocationNotFoundException otherwise).
    payload = {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "purchase_return_challan",
            "service": 0,
            "export": 0,
            "supplierId": self_id,
            "buyerId": sup_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": str(_uuid.uuid4()),
        },
        "transaction_id": transaction_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": sup_id,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number,
            "customize_doc_number": "",
            "doc_date": today,
            "doc_amendment": 0,
            "delivery_date": today,
            "po_details": {
                "poNumber": po_doc.get("document_no_text") or po_doc.get("po_number") or "",
                "poDate": today,
            },
            "transportation_details": {
                "transporterName": "",
                "vehicleNo": "",
                "transportationDocNo": "",
                "transportationDocDate": "",
                "transportersList": [],
            },
            "store_details": ctx["store"],
            "exportDetails": EXPORT_DETAILS,
            "documentSeriesList": series_list,
        },
        "buyerDetails": {
            "buyerCompanyDetails": {"company_id": sup_id, "name": ctx["supplier_name"]},
            "selectedBuyerBillingAddress": _billing_block(ctx["supplier_billing"], sup_id),
            "selectedBuyerDeliveryLocation": _billing_block(ctx["supplier_delivery"], sup_id),
            "addressPermission": 1,
            "kindAttention": "",
            "placeOfSupply": {
                "city": ctx["supplier_billing"].get("city", ""),
                "state": ctx["supplier_billing"].get("state", ""),
                "state_code": ctx["supplier_billing"].get("state_code", ""),
                "country": ctx["supplier_billing"].get("country", "India"),
            },
        },
        "buyerId": sup_id,
        "deliveryLocationCompanyId": sup_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": self_id},
            "selectedSupplierBillingAddress": _billing_block(ctx["buyer_billing"], self_id),
            "addressPermission": 1,
        },
        "supplierId": self_id,
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "purchase_return_challan", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": 0,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": "-1",
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": {
                "doc_discount_1": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_2": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_3": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
            },
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        # PRDC sends the rich TCS shape (Proforma-style), not the shallow {amount:0} that Challan/PO use.
        "tcsDetails": {"text": "", "value": 0, "amount": 0},
        "emailRecipients": EMPTY_RECIPIENTS,
    }
    return _post(token, "/documents/purchase_return_challan/create/", payload)


# --- Phase 7: Invoice -------------------------------------------------------


def create_invoice(token: str, ctx: dict, po_view: dict) -> dict:
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]
    po_doc = po_view["data"]["document_data"]
    transaction_id = po_doc["transaction"]["id"]
    po_qty_by_product = {it["product"]: float(it["quantity"]) for it in po_doc["items"]}

    ds = _get(
        token,
        "/documents/invoice/get_doc_structure/",
        params={
            "type": "invoice",
            "doc_id": -1,
            "action": "create",
            "counter_company_id": sup_id,
            "transaction_id": transaction_id,
            "invoice_type": "invoice_from_po_oc",
        },
    )
    dd = ds["data"]["document_data"]
    ds_items = dd.get("items") or []
    products = {p["id"]: p for p in (dd.get("products") or [])}
    if not ds_items:
        sys.exit("Invoice doc_structure carries no items")

    today = _today_ddmmyyyy()
    doc_number, series_list = _doc_number_for(token, "invoice", self_id)

    items = []
    for pos, raw in enumerate(ds_items):
        item = _rehydrate_item(raw, ctx["tax_master"], products)
        pid = item.get("product") or item.get("id")
        full_qty = int(po_qty_by_product[pid])
        item["quantity"] = full_qty
        item["delivery_date"] = today
        item["position"] = pos
        item["active"] = 1
        item["client"] = self_id
        items.append(item)

    payload = {
        "userCompany": {"id": self_id, "type": "buyer"},
        "details": {
            "docType": "invoice",
            "service": 0,
            "export": 0,
            "supplierId": sup_id,
            "buyerId": self_id,
            "userCompany": {"id": self_id, "type": "buyer"},
            "id": dd.get("uuid"),
        },
        "transaction_id": transaction_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": sup_id,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number,
            "customize_doc_number": "",
            "doc_date": today,
            "doc_amendment": 0,
            "delivery_date": today,
            "po_details": {
                "poNumber": po_doc.get("document_no_text") or po_doc.get("po_number") or "",
                "poDate": today,
            },
            "inward_details": {},
            "payment_terms": {},
            "payment_date": today,
            # Sending raw inward IDs in `select_inward` triggers HTTP 500 on this endpoint
            # (psycopg2 TypeError). The invoice is still tied to the chain via transaction_id.
            "select_inward": [],
            "store_details": ctx["store"],
            "exportDetails": EXPORT_DETAILS,
            "documentSeriesList": series_list,
        },
        "buyerDetails": {
            "buyerCompanyDetails": {"company_id": self_id},
            "selectedBuyerBillingAddress": _billing_block(ctx["buyer_billing"], self_id),
            "selectedBuyerDeliveryLocation": _billing_block(ctx["buyer_delivery"], self_id),
            "addressPermission": 1,
            "placeOfSupply": {
                "city": ctx["buyer_delivery"].get("city", ""),
                "state": ctx["buyer_delivery"].get("state", ""),
                "state_code": ctx["buyer_delivery"].get("state_code", ""),
                "country": ctx["buyer_delivery"].get("country", "India"),
            },
        },
        "buyerId": self_id,
        "deliveryLocationCompanyId": self_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": sup_id, "name": ctx["supplier_name"]},
            "selectedSupplierBillingAddress": _billing_block(ctx["supplier_billing"], sup_id),
            "addressPermission": 1,
        },
        "supplierId": sup_id,
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "invoice", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": dd.get("grand_total") or 0,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": {
                "doc_discount_1": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_2": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
                "doc_discount_3": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
            },
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        "tcsDetails": {"amount": 0},
        "checkApproval": False,
    }
    return _post(token, "/documents/invoice/create/", payload)


# --- Main -------------------------------------------------------------------


def main() -> None:
    token = login()
    ctx = discover_context(token)

    # Pick a PO qty where every downstream phase leaves a non-zero quantity to act on.
    while True:
        po_qty = random.randint(PO_QTY_MIN, PO_QTY_MAX)
        inward1_qty = math.floor(po_qty * INWARD1_FRACTION)
        inward2_qty = po_qty - inward1_qty
        qir2_accept = math.floor(inward2_qty * QIR2_ACCEPT_FRACTION)
        qir2_reject = inward2_qty - qir2_accept
        if inward1_qty >= 1 and inward2_qty >= 1 and qir2_accept >= 1 and qir2_reject >= 1:
            break

    log.info(
        "Plan: PO=%d, inward1=%d, qir1_accept=%d, inward2=%d, qir2_accept=%d, qir2_reject=%d",
        po_qty, inward1_qty, inward1_qty, inward2_qty, qir2_accept, qir2_reject,
    )

    log.info("Phase 1: PO")
    po_id = _extract_doc_id(create_po(token, ctx, po_qty))
    po_view = _fetch_view(token, PO_DOC_TYPE_INT, po_id)
    transaction_id = po_view["data"]["document_data"]["transaction"]["id"]
    log.info("PO doc_id=%d, transaction=%d", po_id, transaction_id)

    log.info("Phase 2: Inward #1 (%d units)", inward1_qty)
    inward1_id = _extract_doc_id(create_inward(token, ctx, po_view, inward1_qty))

    log.info("Phase 3: QIR #1 (accept all %d)", inward1_qty)
    qir1_id = _extract_doc_id(create_qir(token, ctx, inward1_id, transaction_id, [inward1_qty]))

    log.info("Phase 4: Inward #2 (%d units)", inward2_qty)
    inward2_id = _extract_doc_id(create_inward(token, ctx, po_view, inward2_qty))

    log.info("Phase 5: QIR #2 (accept %d / reject %d)", qir2_accept, qir2_reject)
    qir2_id = _extract_doc_id(create_qir(token, ctx, inward2_id, transaction_id, [qir2_accept]))

    log.info("Phase 6: PRDC (return %d rejected units)", qir2_reject)
    prdc_id = _extract_doc_id(create_prdc(token, ctx, po_view, qir2_reject))

    log.info("Phase 7: Invoice (full PO qty %d)", po_qty)
    invoice_id = _extract_doc_id(create_invoice(token, ctx, po_view))

    log.info(
        "Done. PO=%d Inward1=%d QIR1=%d Inward2=%d QIR2=%d PRDC=%d Invoice=%d (transaction=%d)",
        po_id, inward1_id, qir1_id, inward2_id, qir2_id, prdc_id, invoice_id, transaction_id,
    )


if __name__ == "__main__":
    main()
