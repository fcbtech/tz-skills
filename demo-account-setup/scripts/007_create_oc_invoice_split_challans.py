"""Create an OC for the first available buyer, then an invoice and two split challans against it.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md, finds the
first buyer in the company's network, picks 3 sellable goods at random
from the product catalog, and creates a direct OC with 5% GST applied to each line and randomised
order quantities. Then it creates the corresponding sales invoice from
the OC, followed by two delivery challans: the first delivering 40% of
each line's ordered quantity, the second delivering the remaining 60%.
"""

from __future__ import annotations

import logging
import random
import re
import sys
import tomllib
import uuid as _uuid
from datetime import date, timedelta
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
OC_DOC_TYPE_INT = 4
TAX_RATE = 0.05
NUM_ITEMS = 3  # picked at random from the full sellable-goods catalog each run
QTY_MIN = 500
QTY_MAX = 1000
CHALLAN_FIRST_PCT = 0.40
TRANSACTION_TITLE_PREFIX = "Sales Demo OC"

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

OPTIONAL_COLUMNS = [
    {"label": "Alternate Unit", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 1", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 2", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Discount 3", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Total Tax", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Delivery Date", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
    {"label": "Comments", "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0},
]

EMPTY_DOC_DISCOUNT = {
    "doc_discount_1": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
    "doc_discount_2": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
    "doc_discount_3": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
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


# --- Business helpers -------------------------------------------------------


def _ddmmyyyy(d: date) -> str:
    return d.strftime("%d/%m/%Y")


def _pick_default(rows: list) -> dict:
    for row in rows:
        if row.get("default"):
            return row
    if not rows:
        raise RuntimeError("No rows available to pick from")
    return rows[0]


def _build_oc_item(
    product: dict,
    qty: int,
    position: int,
    self_id: int,
    delivery_date_str: str,
    tax_master: dict,
) -> dict:
    base_unit = next((u for u in product["units"] if u["conversion"] == 1.0), product["units"][0])
    price = float(product.get("price") or (product.get("prices") or {}).get("default") or 0) or 0.0
    tax_obj = {
        "id": tax_master["id"],
        "tax_id": tax_master["id"],
        "tax_type": "gst",
        "tax_name": tax_master["tax_name"],
        "tax_percentage": tax_master["tax_percentage"],
        "description": tax_master.get("description", ""),
        "is_active": True,
        "linked_cgst": tax_master.get("linked_cgst"),
        "linked_sgst": tax_master.get("linked_sgst"),
        "linked_igst": tax_master.get("linked_igst"),
        "linked_gst": None,
        "product_id": product["id"],
    }
    total_cost = round(qty * price, 2)
    tax_amount = round(total_cost * TAX_RATE, 2)
    return {
        "id": product["id"],
        "product": product["id"],
        "uuid": product["uuid"],
        "itemid": product["itemid"],
        "product_name": product["product_name"],
        "item_name": product["product_name"],
        "hsn_code": product.get("hsn_code") or "",
        "category_name": product.get("category_name") or "",
        "quantity": str(qty),
        "price": price,
        "base_price": price,
        "discount": 0,
        "total_cost": total_cost,
        "tax": tax_amount,
        "base_total_amount": round(total_cost + tax_amount, 2),
        "base_discount": 0,
        "base_total_cost": total_cost,
        "base_tax": tax_amount,
        "unit": base_unit,
        "units": product["units"],
        "taxes": [tax_obj],
        "taxes_data": {},
        "prices": product.get("prices") or {},
        "vendor_mapping": product.get("vendor_mapping"),
        "is_service": product.get("is_service") or 0,
        "stock": product.get("stock") or 0,
        "delivery_date": delivery_date_str,
        "position": position,
        "active": 1,
        "client": self_id,
        "comment": "",
        "discount_type": {"type": 0, "value": "₹"},
        "item_discount_type": {"type": 0, "value": "₹"},
        "item_discount_type_1": {"type": 1, "value": "%"},
        "item_discount_type_2": {"type": 1, "value": "%"},
        "item_discount_type_3": {"type": 1, "value": "%"},
        "item_discount_total": 0,
        "item_level_doc_discount": 0,
        "item_level_doc_discount_type": {"type": 0, "value": "₹"},
        "discount_data": {},
        "inrConversionRate": "1",
        "key_mappings": {"text": "product_name", "value": "id"},
        "custom_fields_parsed": product.get("custom_fields_parsed") or {},
    }


def _with_quantity(item: dict, new_qty: float, link_fields: dict | None = None) -> dict:
    out = dict(item)
    price = float(item["price"])
    total_cost = round(new_qty * price, 2)
    tax_amount = round(total_cost * TAX_RATE, 2)
    out["quantity"] = str(new_qty)
    out["total_cost"] = total_cost
    out["tax"] = tax_amount
    out["base_total_amount"] = round(total_cost + tax_amount, 2)
    out["base_total_cost"] = total_cost
    out["base_tax"] = tax_amount
    if link_fields:
        out.update(link_fields)
    return out


def _link_fields(src_item: dict, key_prefix: str) -> dict:
    fields = {
        f"{key_prefix}_item_id": src_item.get(f"{key_prefix}_item_id"),
        "source_item_id": src_item.get("source_item_id"),
        key_prefix: src_item.get(key_prefix),
        "uid": src_item.get("uid"),
    }
    return {k: v for k, v in fields.items() if v is not None}


# --- Lookups ----------------------------------------------------------------


def fetch_self_company(token: str) -> tuple[int, dict]:
    profile = _get(token, "/profile/info/fetch/")["data"]
    self_id = profile["user"]["company_id"]
    company = profile["company"]
    return self_id, {
        "company_id": self_id,
        "name": company["name"],
        "email": company["email"],
        "mobile_no": company["mobile_no"],
        "company_image": company.get("company_image_url", ""),
    }


def fetch_first_buyer(token: str) -> dict:
    rows = _get(token, "/profile/counter-party/list/", params={"target_category": "buyer"})["data"]["results"]
    if not rows:
        sys.exit("No buyers available in counter-party list — add a buyer counter-party first.")
    return rows[0]


def fetch_random_products(token: str, buyer_id: int) -> list[dict]:
    rows = _get(
        token,
        "/settings/product/",
        params={
            "product_type": "sell",
            "place": "product_name",
            "service_type": 0,
            "counter_party": buyer_id,
        },
    )["data"]["results"]
    if len(rows) < NUM_ITEMS:
        sys.exit(f"Need {NUM_ITEMS} sellable goods; found {len(rows)}.")
    # Random sample (without replacement) from the whole catalog so every run
    # can exercise any of the available products, not a fixed leading window.
    return random.sample(rows, NUM_ITEMS)


def fetch_tax_master(token: str, rate: float) -> dict:
    rows = _get(token, "/settings/tax/")["data"]["results"]
    for row in rows:
        if row.get("tax_type") != "gst":
            continue
        try:
            if abs(float(row.get("tax_percentage") or 0) - rate) < 1e-6:
                return row
        except (TypeError, ValueError):
            continue
    sys.exit(f"No GST tax master found at rate {rate}")


def resolve_doc_number(token: str, self_id: int, doc_type: str, prefix_hint: str) -> tuple[dict, list]:
    resp = _get(
        token,
        "/settings/document-number/get_document_no/",
        params={"doc_type": doc_type, "is_service": 0, "company_id": self_id},
    )["data"]
    series_list = resp["doc_number"]
    if resp["manual_number"]:
        return {"id": None, "value": f"{prefix_hint}-AUTO-{_uuid.uuid4().hex[:8]}"}, series_list
    chosen = series_list[0]
    return {"id": chosen["id"], "value": chosen["value"]}, series_list


# --- OC payload + create ----------------------------------------------------


def build_oc_payload(
    self_id: int,
    self_company_payload: dict,
    buyer: dict,
    items: list[dict],
    self_billing: dict,
    buyer_billing: dict,
    buyer_delivery: dict,
    store: dict,
    doc_number_obj: dict,
    series_list: list,
    doc_date_str: str,
    delivery_date_str: str,
) -> dict:
    buyer_id = buyer["company_id"]
    buyer_company_payload = {
        "company_id": buyer_id,
        "name": buyer["name"],
        "email": "",
        "mobile_no": "",
        "company_image": buyer.get("company_image", ""),
    }
    place_of_supply = {
        "city": buyer_delivery.get("city") or "",
        "state": buyer_delivery.get("state") or "",
        "country": buyer_delivery.get("country") or "India",
    }
    return {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "oc",
            "service": 0,
            "export": 0,
            "supplierId": self_id,
            "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": None,
        },
        "transaction_id": 0,
        "transaction": {"title": f"{TRANSACTION_TITLE_PREFIX} {_uuid.uuid4().hex[:6]}"},
        "sq_id": 0,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "supplierId": self_id,
        "buyerId": buyer_id,
        "deliveryLocationCompanyId": buyer_id,
        "supplierDetails": {
            "supplierCompanyDetails": self_company_payload,
            "selectedSupplierBillingAddress": self_billing,
            "addressPermission": 1,
        },
        "buyerDetails": {
            "buyerCompanyDetails": buyer_company_payload,
            "selectedBuyerBillingAddress": buyer_billing,
            "selectedBuyerDeliveryLocation": buyer_delivery,
            "addressPermission": 1,
            "kindAttention": "",
            "placeOfSupply": place_of_supply,
        },
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number_obj,
            "customize_doc_number": "",
            "doc_date": doc_date_str,
            "doc_amendment": 0,
            "delivery_date": delivery_date_str,
            "po_details": {},
            "sq_details": {},
            "payment_terms": {},
            "store_details": store,
            "exportDetails": {
                "show_igst": False,
                "originCountry": "India",
                "dischargeCountry": "India",
                "finalDestinationCountry": "India",
            },
            "documentSeriesList": series_list,
            "customFields": [],
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {},
        "attachSignature": 1,
        "documentBlockDetails": {"docType": "oc", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": round(sum(it["base_total_amount"] for it in items), 2),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": EMPTY_DOC_DISCOUNT,
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": None,
            "baseAdvanceToPay": None,
        },
        "tcsDetails": {"amount": 0},
        "emailRecipients": {
            "selectedRecipients": [],
            "cancelledRecipients": [],
            "subject": None,
            "introLine": None,
            "closingLine": None,
            "additionalRecipients": [],
        },
        "approvalData": {"approvalRuleType": "", "approvalMsg": "", "hasPermission": False},
        "checkApproval": False,
    }


def create_oc(
    token: str,
    self_id: int,
    self_company_payload: dict,
) -> dict:
    buyer = fetch_first_buyer(token)
    buyer_id = buyer["company_id"]
    log.info("Picked buyer: %s (id=%s)", buyer["name"], buyer_id)

    products = fetch_random_products(token, buyer_id)
    log.info(
        "Picked %d random sellable goods: %s",
        NUM_ITEMS,
        ", ".join(p["product_name"] for p in products),
    )
    tax_master = fetch_tax_master(token, TAX_RATE)
    log.info("Applying tax master id=%s (%s) at %d%%", tax_master["id"], tax_master["tax_name"], int(TAX_RATE * 100))

    # Doc-structure call is a backend prerequisite for direct_oc create even when document_data is sparse.
    _get(
        token,
        "/documents/oc/get_doc_structure/",
        params={
            "type": "oc",
            "doc_id": -1,
            "transaction_id": 0,
            "oc_type": "direct_oc",
            "action": "create",
            "counter_company_id": buyer_id,
        },
    )

    self_billing = _pick_default(
        _get(token, "/settings/billing-address/get-addresses/", params={"company_id": self_id})["data"]
    )
    buyer_billing = _pick_default(
        _get(token, "/settings/billing-address/get-addresses/", params={"company_id": buyer_id})["data"]
    )
    buyer_delivery = _pick_default(
        _get(token, "/settings/delivery-location/get-locations/", params={"company_id": buyer_id})["data"]
    )
    stores = _get(token, "/inventory/store/", params={"type": "main", "doc_type": "oc"})["data"]["results"]
    if not stores:
        sys.exit("No stores found for the logged-in company — add a main store first.")
    store = stores[0]

    doc_number_obj, series_list = resolve_doc_number(token, self_id, "oc", "OC")

    today = date.today()
    doc_date_str = _ddmmyyyy(today)
    delivery_date_str = _ddmmyyyy(today + timedelta(days=14))

    items = [
        _build_oc_item(p, random.randint(QTY_MIN, QTY_MAX), idx, self_id, delivery_date_str, tax_master)
        for idx, p in enumerate(products)
    ]

    payload = build_oc_payload(
        self_id, self_company_payload, buyer, items,
        self_billing, buyer_billing, buyer_delivery, store,
        doc_number_obj, series_list, doc_date_str, delivery_date_str,
    )
    resp = _post(token, "/documents/oc/create/", payload)
    oc_doc_id = (resp.get("data") or {}).get("doc_id")
    if not oc_doc_id:
        sys.exit(f"OC create did not return doc_id: {resp}")
    log.info("OC created: doc_id=%s number=%s", oc_doc_id, doc_number_obj["value"])

    return {
        "doc_id": oc_doc_id,
        "doc_number_value": doc_number_obj["value"],
        "items": items,
        "buyer": buyer,
        "self_billing": self_billing,
        "buyer_billing": buyer_billing,
        "buyer_delivery": buyer_delivery,
        "store": store,
        "self_company_payload": self_company_payload,
        "doc_date_str": doc_date_str,
        "delivery_date_str": delivery_date_str,
    }


# --- Invoice + Challan creation ---------------------------------------------


def _common_party_blocks(
    self_id: int,
    self_company_payload: dict,
    buyer: dict,
    self_billing: dict,
    buyer_billing: dict,
    buyer_delivery: dict,
) -> dict:
    buyer_id = buyer["company_id"]
    buyer_company_payload = {
        "company_id": buyer_id,
        "name": buyer["name"],
        "email": "",
        "mobile_no": "",
        "company_image": buyer.get("company_image", ""),
    }
    place_of_supply = {
        "city": buyer_delivery.get("city") or "",
        "state": buyer_delivery.get("state") or "",
        "country": buyer_delivery.get("country") or "India",
    }
    return {
        "buyerDetails": {
            "buyerCompanyDetails": buyer_company_payload,
            "selectedBuyerBillingAddress": buyer_billing,
            "selectedBuyerDeliveryLocation": buyer_delivery,
            "addressPermission": 1,
            "kindAttention": "",
            "placeOfSupply": place_of_supply,
        },
        "supplierDetails": {
            "supplierCompanyDetails": self_company_payload,
            "selectedSupplierBillingAddress": self_billing,
            "addressPermission": 1,
        },
    }


def fetch_oc_view(token: str, oc_doc_id: int) -> dict:
    return _get(
        token,
        "/documents/document/view/",
        params={"doc_type": OC_DOC_TYPE_INT, "doc_id": oc_doc_id},
    )["data"]["document_data"]


def create_invoice_from_oc(token: str, self_id: int, oc_ctx: dict, transaction_id: int, oc_uuid: str) -> int:
    buyer = oc_ctx["buyer"]
    buyer_id = buyer["company_id"]
    ds = _get(
        token,
        "/documents/invoice/get_doc_structure/",
        params={
            "type": "invoice",
            "doc_id": -1,
            "transaction_id": transaction_id,
            "invoice_type": "invoice_from_po_oc",
            "action": "create",
            "counter_company_id": buyer_id,
        },
    )["data"]["document_data"]

    invoice_items = [
        _with_quantity(oc_item, float(oc_item["quantity"]), _link_fields(src, "oc"))
        for oc_item, src in zip(oc_ctx["items"], ds["items"])
    ]

    doc_number_obj, series_list = resolve_doc_number(token, self_id, "invoice", "INV")
    party_blocks = _common_party_blocks(
        self_id, oc_ctx["self_company_payload"], buyer,
        oc_ctx["self_billing"], oc_ctx["buyer_billing"], oc_ctx["buyer_delivery"],
    )

    payload = {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "invoice",
            "service": 0,
            "export": 0,
            "supplierId": self_id,
            "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": oc_uuid,
        },
        "transaction_id": transaction_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "buyerId": buyer_id,
        "supplierId": self_id,
        "deliveryLocationCompanyId": buyer_id,
        **party_blocks,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": invoice_items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number_obj,
            "customize_doc_number": "",
            "doc_date": oc_ctx["doc_date_str"],
            "doc_amendment": 0,
            "delivery_date": oc_ctx["delivery_date_str"],
            "po_details": {},
            "oc_details": {"ocNumber": oc_ctx["doc_number_value"], "ocDate": oc_ctx["doc_date_str"]},
            "inward_details": {},
            "payment_terms": {},
            "payment_date": oc_ctx["doc_date_str"],
            "select_inward": [],
            "store_details": oc_ctx["store"],
            "exportDetails": {
                "show_igst": False,
                "originCountry": "India",
                "dischargeCountry": "India",
                "finalDestinationCountry": "India",
            },
            "documentSeriesList": series_list,
            "customFields": [],
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 1,
        "documentBlockDetails": {"docType": "invoice", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": round(sum(i["base_total_amount"] for i in invoice_items), 2),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": EMPTY_DOC_DISCOUNT,
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        "tcsDetails": {"amount": 0},
        "checkApproval": False,
    }
    resp = _post(token, "/documents/invoice/create/", payload)
    inv_doc_id = (resp.get("data") or {}).get("doc_id")
    if not inv_doc_id:
        sys.exit(f"Invoice create did not return doc_id: {resp}")
    log.info("Invoice created: doc_id=%s number=%s", inv_doc_id, doc_number_obj["value"])
    return inv_doc_id


def create_challan_from_oc(
    token: str,
    self_id: int,
    oc_ctx: dict,
    transaction_id: int,
    quantities: list[float],
    label: str,
) -> int:
    buyer = oc_ctx["buyer"]
    buyer_id = buyer["company_id"]
    ds = _get(
        token,
        "/documents/challan/get_doc_structure/",
        params={
            "type": "challan",
            "doc_id": -1,
            "transaction_id": transaction_id,
            "challan_type": "challan_from_po_oc",
            "action": "create",
            "counter_company_id": buyer_id,
        },
    )["data"]["document_data"]

    challan_items = [
        _with_quantity(oc_item, qty, _link_fields(src, "oc"))
        for oc_item, src, qty in zip(oc_ctx["items"], ds["items"], quantities)
    ]

    doc_number_obj, series_list = resolve_doc_number(token, self_id, "challan", "CL")
    party_blocks = _common_party_blocks(
        self_id, oc_ctx["self_company_payload"], buyer,
        oc_ctx["self_billing"], oc_ctx["buyer_billing"], oc_ctx["buyer_delivery"],
    )

    payload = {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "challan",
            "service": 0,
            "export": 0,
            "supplierId": self_id,
            "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": str(_uuid.uuid4()),
        },
        "transaction_id": transaction_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "buyerId": buyer_id,
        "supplierId": self_id,
        "deliveryLocationCompanyId": buyer_id,
        **party_blocks,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": challan_items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number_obj,
            "customize_doc_number": "",
            "doc_date": oc_ctx["doc_date_str"],
            "doc_amendment": 0,
            "delivery_date": oc_ctx["delivery_date_str"],
            "po_details": {},
            "oc_details": {"ocNumber": oc_ctx["doc_number_value"], "ocDate": oc_ctx["doc_date_str"]},
            "transportation_details": {
                "payToTransporter": 0,
                "transporterGstNo": "",
                "transporterName": "",
                "vehicleNo": "",
                "transportationDocNo": "",
                "transportationDocDate": "",
                "transportersList": [],
            },
            "store_details": oc_ctx["store"],
            "exportDetails": {
                "show_igst": False,
                "originCountry": "India",
                "dischargeCountry": "India",
                "finalDestinationCountry": "India",
            },
            "documentSeriesList": series_list,
            "customFields": [],
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 1,
        "documentBlockDetails": {"docType": "challan", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": round(sum(i["base_total_amount"] for i in challan_items), 2),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": "-1",
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": EMPTY_DOC_DISCOUNT,
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        "emailRecipients": {
            "selectedRecipients": [],
            "cancelledRecipients": [],
            "subject": None,
            "introLine": None,
            "closingLine": None,
            "additionalRecipients": [],
        },
    }
    resp = _post(token, "/documents/challan/create/", payload)
    ch_doc_id = (resp.get("data") or {}).get("doc_id")
    if not ch_doc_id:
        sys.exit(f"Challan {label} create did not return doc_id: {resp}")
    log.info("Challan %s created: doc_id=%s number=%s", label, ch_doc_id, doc_number_obj["value"])
    return ch_doc_id


# --- Entry point ------------------------------------------------------------


def main() -> None:
    token = login()
    self_id, self_company_payload = fetch_self_company(token)

    oc_ctx = create_oc(token, self_id, self_company_payload)

    oc_view = fetch_oc_view(token, oc_ctx["doc_id"])
    transaction_id = oc_view["transaction"]["id"]
    oc_uuid = oc_view["uuid"]
    log.info("OC transaction_id=%s uuid=%s", transaction_id, oc_uuid)

    create_invoice_from_oc(token, self_id, oc_ctx, transaction_id, oc_uuid)

    qtys = [float(it["quantity"]) for it in oc_ctx["items"]]
    qtys_first = [round(q * CHALLAN_FIRST_PCT, 2) for q in qtys]
    qtys_second = [round(q - q1, 2) for q, q1 in zip(qtys, qtys_first)]

    create_challan_from_oc(token, self_id, oc_ctx, transaction_id, qtys_first, "#1 (40%)")
    create_challan_from_oc(token, self_id, oc_ctx, transaction_id, qtys_second, "#2 (60%)")

    for idx, q in enumerate(qtys):
        delivered = round(qtys_first[idx] + qtys_second[idx], 2)
        if abs(delivered - q) > 1e-6:
            raise RuntimeError(
                f"Item {idx}: challan #1 + challan #2 ({delivered}) does not sum to OC ordered qty ({q})"
            )

    log.info("All documents created and quantities reconcile.")


if __name__ == "__main__":
    main()
