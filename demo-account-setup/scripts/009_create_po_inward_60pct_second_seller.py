"""Create a demo Purchase Order for the second available seller and a 60%-receipt Inward.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md, picks the
second supplier in the company's network, three buyable goods picked at
random from the product catalog and linked to that supplier, and creates:

  1. a direct PO with 18% GST on each line and a random quantity per item;
  2. an Inward against that PO marking floor(po_qty * 0.6) units per line
     as received (partial receipt).

Both documents share the same transaction chain.
"""

from __future__ import annotations

import logging
import math
import random
import re
import sys
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
INWARD_DOC_TYPE_INT = 3
TAX_RATE = 0.18
NUM_ITEMS = 3  # picked at random from the full buyable-goods catalog each run
INWARD_FRACTION = 0.60
SUPPLIER_INDEX = 1  # 0 = first available, 1 = second available, ...
QTY_MIN = 50  # keep low bound >= 2 so floor(qty * INWARD_FRACTION) >= 1
QTY_MAX = 200
TRANSACTION_TITLE = "PO Inward 60% auto-test"

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


def _get(token: str, path: str, params: dict | None = None) -> dict | list:
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
    tax_amount = round(total_cost * TAX_RATE, 2)
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
    """Strip FE-only fields and transform shape per schema-transforms rule."""
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
    def _valid(v):  # accept int PKs AND UUIDv7 string ids; reject None/""/0
        return (isinstance(v, int) and v > 0) or (isinstance(v, str) and v.strip() != "")
    for k in ("doc_id", "id", "document_id"):
        if _valid(data.get(k)):
            return data[k]
    doc = data.get("document") or {}
    for k in ("doc_id", "id"):
        if _valid(doc.get(k)):
            return doc[k]
    raise RuntimeError(f"Could not extract doc_id from create response keys: {list(data.keys())}")


# --- Discovery --------------------------------------------------------------


def discover_context(token: str) -> dict[str, Any]:
    suppliers = _get(token, "/profile/counter-party/list/", params={"target_category": "supplier"})["data"][
        "results"
    ]
    if len(suppliers) <= SUPPLIER_INDEX:
        sys.exit(
            f"Need at least {SUPPLIER_INDEX + 1} supplier(s) in counter-party list; got {len(suppliers)}."
        )
    supplier = suppliers[SUPPLIER_INDEX]
    sup_id = supplier["company_id"]
    sup_name = supplier["name"]

    tax_resp = _get(token, "/settings/tax/")["data"]["results"]
    gst_match = next(
        (
            t
            for t in tax_resp
            if t.get("tax_type") == "gst" and abs(float(t["tax_percentage"]) - TAX_RATE) < 1e-6
        ),
        None,
    )
    if not gst_match:
        sys.exit(f"No GST tax master found at rate {TAX_RATE}")

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
    if len(buyable) < NUM_ITEMS:
        sys.exit(f"Need {NUM_ITEMS} buyable goods for supplier {sup_id}; got {len(buyable)}")
    # Random sample (without replacement) from the whole catalog so every run
    # can exercise any of the available products, not a fixed leading window.
    products = random.sample(buyable, NUM_ITEMS)

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
    if not ba_buyer:
        sys.exit("Buyer company has no billing addresses")
    if not ba_supplier:
        sys.exit(f"Supplier {sup_id} has no billing addresses")
    if not dl_buyer:
        sys.exit("Buyer company has no delivery locations")

    stores = _get(token, "/inventory/store/", params={"type": "main", "doc_type": "po"})["data"]["results"]
    if not stores:
        sys.exit("No stores returned")
    store = next((s for s in stores if s.get("is_default")), stores[0])

    return {
        "supplier_id": sup_id,
        "supplier_name": sup_name,
        "self_company_id": self_id,
        "gst": gst_match,
        "products": products,
        "buyer_billing": _pick_default(ba_buyer),
        "supplier_billing": _pick_default(ba_supplier),
        "buyer_delivery": _pick_default(dl_buyer),
        "store": store,
        "doc_level_custom_fields": doc_cf,
    }


# --- Business flow ----------------------------------------------------------


def create_po(token: str, ctx: dict, qty_by_product: dict[int, int]) -> dict:
    self_id = ctx["self_company_id"]
    sup_id = ctx["supplier_id"]
    doc_number, series_list = _doc_number_for(token, "po", self_id)
    today = _today_ddmmyyyy()

    items = [
        _build_po_item(
            product=p,
            quantity=qty_by_product[p["id"]],
            tax_master=ctx["gst"],
            self_id=self_id,
            delivery_date_str=today,
            position=pos,
        )
        for pos, p in enumerate(ctx["products"])
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


def create_inward(token: str, ctx: dict, po_view: dict, inward_qty_by_product: dict[int, int]) -> dict:
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
    if len(ds_items) != NUM_ITEMS:
        sys.exit(f"Inward doc_structure should carry {NUM_ITEMS} items from PO; got {len(ds_items)}")

    today = _today_ddmmyyyy()
    doc_number, series_list = _doc_number_for(token, "inward", self_id)

    items = []
    for pos, raw in enumerate(ds_items):
        item = _rehydrate_item(raw, ctx["gst"], products)
        product_id = item.get("product") or item.get("id")
        inward_qty = inward_qty_by_product[product_id]
        item["quantity"] = str(inward_qty)
        item["product_delivered_now"] = str(inward_qty)
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


# --- Entry point ------------------------------------------------------------


def main() -> None:
    token = login()
    ctx = discover_context(token)
    po_qty = {p["id"]: random.randint(QTY_MIN, QTY_MAX) for p in ctx["products"]}
    inward_qty = {pid: math.floor(q * INWARD_FRACTION) for pid, q in po_qty.items()}

    log.info(
        "Phase 1: creating PO for supplier #%d (%s, id=%s)",
        SUPPLIER_INDEX + 1,
        ctx["supplier_name"],
        ctx["supplier_id"],
    )
    po_resp = create_po(token, ctx, po_qty)
    po_id = _extract_doc_id(po_resp)
    log.info("PO created — doc_id=%s  qty=%s", po_id, po_qty)

    po_view = _get(
        token, "/documents/document/view/", params={"doc_type": PO_DOC_TYPE_INT, "doc_id": po_id}
    )

    log.info("Phase 2: creating Inward for %.0f%% of PO quantity (per-line: %s)", INWARD_FRACTION * 100, inward_qty)
    inward_resp = create_inward(token, ctx, po_view, inward_qty)
    inward_id = _extract_doc_id(inward_resp)

    log.info(
        "Done. PO=%s  Inward=%s  (transaction=%s)",
        po_id,
        inward_id,
        po_view["data"]["document_data"]["transaction"]["id"],
    )


if __name__ == "__main__":
    main()
