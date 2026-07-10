"""Create a demo Order Confirmation (OC) for the first available buyer.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md, finds the
first buyer in the company's network, picks the first 2 sellable goods,
and creates a direct OC with the 18% GST master tax applied to each
line at create time (regardless of the products' own tax mappings).
Order quantity per line is randomised at run time.
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
TAX_RATE = 0.18
NUM_ITEMS = 2
QTY_MIN = 500
QTY_MAX = 1000
TRANSACTION_TITLE = "OC auto-test"

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


def _build_item(
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
        "tax_type": tax_master["tax_type"],
        "tax_name": tax_master["tax_name"],
        "tax_percentage": str(tax_master["tax_percentage"]),
        "product_id": product["id"],
        "tax_id": tax_master["id"],
    }
    total_cost = round(qty * price, 2)
    tax_amount = round(total_cost * TAX_RATE, 2)
    half_tax = round(tax_amount / 2, 2)
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
        "taxes_data": {
            "igst": {"tax_amount": 0, "tax_percentage": 0},
            "cgst": {"tax_amount": half_tax, "tax_percentage": TAX_RATE / 2},
            "sgst": {"tax_amount": half_tax, "tax_percentage": TAX_RATE / 2},
            "gst": {"tax_amount": tax_amount, "tax_percentage": TAX_RATE},
            "cess": {"tax_amount": 0, "tax_percentage": 0},
        },
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


# --- Business flow ----------------------------------------------------------


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


def fetch_first_n_products(token: str, buyer_id: int) -> list[dict]:
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
    return rows[:NUM_ITEMS]


def fetch_tax_master(token: str, rate: float) -> dict:
    """Return the GST master tax row whose tax_percentage matches `rate`."""
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


def resolve_doc_number(token: str, self_id: int) -> tuple[dict, list]:
    resp = _get(
        token,
        "/settings/document-number/get_document_no/",
        params={"doc_type": "oc", "is_service": 0, "company_id": self_id},
    )["data"]
    series_list = resp["doc_number"]
    if resp["manual_number"]:
        return {"id": None, "value": f"OC-AUTO-{_uuid.uuid4().hex[:8]}"}, series_list
    chosen = series_list[0]
    return {"id": chosen["id"], "value": chosen["value"]}, series_list


def build_payload(
    self_id: int,
    self_company_payload: dict,
    buyer: dict,
    products: list[dict],
    tax_master: dict,
    self_billing: dict,
    buyer_billing: dict,
    buyer_delivery: dict,
    store: dict,
    doc_number_obj: dict,
    series_list: list,
) -> dict:
    today = date.today()
    doc_date_str = _ddmmyyyy(today)
    delivery_date_str = _ddmmyyyy(today + timedelta(days=7))

    items = [
        _build_item(p, random.randint(QTY_MIN, QTY_MAX), idx, self_id, delivery_date_str, tax_master)
        for idx, p in enumerate(products)
    ]

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
        "transaction": {"title": TRANSACTION_TITLE},
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
        "itemDetails": {
            "optionalColumns": OPTIONAL_COLUMNS,
            "items": items,
            "fgItems": [],
        },
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


def create_oc(token: str) -> dict:
    self_id, self_company_payload = fetch_self_company(token)
    buyer = fetch_first_buyer(token)
    buyer_id = buyer["company_id"]
    log.info("Picked buyer: %s (id=%s)", buyer["name"], buyer_id)

    products = fetch_first_n_products(token, buyer_id)
    log.info(
        "Picked first %d sellable goods: %s",
        NUM_ITEMS,
        ", ".join(p["product_name"] for p in products),
    )
    tax_master = fetch_tax_master(token, TAX_RATE)
    log.info("Applying tax master id=%s (%s) at %d%%", tax_master["id"], tax_master["tax_name"], int(TAX_RATE * 100))

    # Required prerequisite per the doc-create flow even when document_data is empty for direct_oc.
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
    doc_number_obj, series_list = resolve_doc_number(token, self_id)

    payload = build_payload(
        self_id,
        self_company_payload,
        buyer,
        products,
        tax_master,
        self_billing,
        buyer_billing,
        buyer_delivery,
        store,
        doc_number_obj,
        series_list,
    )

    return _post(token, "/documents/oc/create/", payload)


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()

    log.info("=== Creating OC ===")
    create_resp = create_oc(token)
    new_doc_id = (create_resp.get("data") or {}).get("doc_id")
    if not new_doc_id:
        sys.exit(f"OC create did not return a doc_id. Response: {create_resp}")

    log.info("=== Done — created OC doc_id=%s ===", new_doc_id)


if __name__ == "__main__":
    main()
