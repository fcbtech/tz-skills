"""Create a demo OC + Challan (60% delivery) + Invoice for the second available buyer.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md, finds the
second buyer in the company's network, picks one sellable goods item at random from the product catalog,
creates a direct OC with 28% GST + Rs. 2000 shipping charge taxed at 18%,
then creates a Challan delivering 60% of the OC quantity, and finally
creates an Invoice for the full OC quantity.

Order quantity is randomised at run time within [QTY_MIN, QTY_MAX].
"""

from __future__ import annotations

import datetime
import logging
import random
import re
import sys
import tomllib
import uuid as _uuid
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
BUYER_INDEX = 1  # 0 = first buyer, 1 = second buyer.
# The single OC line item is picked at random from the full sellable-goods catalog each run.
TAX_RATE_ITEM = 0.28
TAX_RATE_SHIPPING = 0.18
SHIPPING_CHARGE = 2000
CHALLAN_DELIVERY_FRACTION = 0.6
QTY_MIN = 500
QTY_MAX = 1000
TRANSACTION_TITLE = "OC sales-demo"

OC_DOC_TYPE_INT = 4
CHALLAN_DOC_TYPE_INT = 6
INVOICE_DOC_TYPE_INT = 2

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


def _ddmmyyyy(d: datetime.date) -> str:
    return d.strftime("%d/%m/%Y")


def _iso_to_ddmmyyyy(value: Any) -> str:
    """Convert ISO 8601 datetime/date strings to DD/MM/YYYY. Pass through if already DD/MM/YYYY."""
    if not value:
        return ""
    s = str(value)
    if "/" in s and len(s.split("/")[0]) <= 2:
        return s
    try:
        return datetime.datetime.fromisoformat(s.split(".")[0].replace("T", " ")).strftime("%d/%m/%Y")
    except (ValueError, AttributeError):
        return s


def _pick_default(rows: list) -> dict:
    for row in rows:
        if row.get("default") == 1:
            return row
    if not rows:
        raise RuntimeError("No rows available to pick from")
    return rows[0]


def _find_gst_master(taxes: list, percentage: float) -> dict:
    for t in taxes:
        if t.get("tax_type") == "gst" and abs(float(t["tax_percentage"]) - percentage) < 1e-6:
            return t
    sys.exit(f"No GST tax master found at rate {percentage}")


def _build_item_tax(tax_master: dict, product_id: int) -> dict:
    return {
        "id": tax_master["id"],
        "tax_id": tax_master["id"],
        "tax_type": tax_master["tax_type"],
        "tax_name": tax_master["tax_name"],
        "tax_percentage": tax_master["tax_percentage"],
        "product_id": product_id,
        "linked_cgst": tax_master.get("linked_cgst"),
        "linked_sgst": tax_master.get("linked_sgst"),
        "linked_igst": tax_master.get("linked_igst"),
        "linked_gst": tax_master.get("linked_gst"),
        "is_active": tax_master.get("is_active", True),
        "description": tax_master.get("description", ""),
    }


def _build_charge_tax(tax_master: dict) -> dict:
    return {
        "id": tax_master["id"],
        "tax_type": tax_master["tax_type"],
        "tax_name": tax_master["tax_name"],
        "tax_percentage": tax_master["tax_percentage"],
        "linked_cgst": tax_master.get("linked_cgst"),
        "linked_sgst": tax_master.get("linked_sgst"),
        "linked_igst": tax_master.get("linked_igst"),
        "linked_gst": tax_master.get("linked_gst"),
        "is_active": tax_master.get("is_active", True),
        "description": tax_master.get("description", ""),
    }


def _transform_unit(unit_value: Any, units_pool: list) -> dict:
    if isinstance(unit_value, dict):
        return unit_value
    for u in units_pool:
        if u.get("unit_name") == unit_value:
            return u
    return units_pool[0]


def _transform_units(units_value: Any, units_pool: list) -> list:
    if isinstance(units_value, list):
        return units_value
    if isinstance(units_value, (int, str)):   # unit id may be an int PK or a UUIDv7 string
        for u in units_pool:
            if u.get("id") == units_value:
                return [u]
    return units_pool


def _empty_doc_discount() -> dict:
    return {
        "doc_discount_1": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
        "doc_discount_2": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
        "doc_discount_3": {"chargeDescription": "", "chargeType": {"type": 1, "value": "%"}, "value": ""},
    }


def _export_details_default() -> dict:
    return {
        "show_igst": False,
        "originCountry": "India",
        "dischargeCountry": "India",
        "finalDestinationCountry": "India",
    }


def _resolve_doc_number(token: str, doc_type: str, self_id: int) -> tuple[dict, list]:
    resp = _get(
        token,
        "/settings/document-number/get_document_no/",
        params={"doc_type": doc_type, "is_service": 0, "company_id": self_id},
    )["data"]
    series_list = resp["doc_number"]
    if resp["manual_number"]:
        return {"id": None, "value": f"{doc_type.upper()}-AUTO-{_uuid.uuid4().hex[:8]}"}, series_list
    chosen = series_list[0]
    return {"id": chosen["id"], "value": chosen["value"]}, series_list


# --- Business flow ----------------------------------------------------------


def fetch_self_company(token: str) -> tuple[int, dict, dict]:
    profile = _get(token, "/profile/info/fetch/")["data"]
    user = profile["user"]
    company = profile["company"]
    self_id = user["company_id"]
    company_payload = {
        "company_id": self_id,
        "name": company["name"],
        "email": company["email"],
        "mobile_no": company["mobile_no"],
        "company_image": company.get("company_image_url", ""),
    }
    return self_id, company_payload, user


def fetch_buyer(token: str) -> dict:
    rows = _get(token, "/profile/counter-party/list/", params={"target_category": "buyer"})["data"]["results"]
    if len(rows) <= BUYER_INDEX:
        sys.exit(
            f"Need at least {BUYER_INDEX + 1} buyers in the network; found {len(rows)}. "
            f"Add more buyer counter-parties first."
        )
    return rows[BUYER_INDEX]


def fetch_random_product(token: str, buyer_id: int) -> dict:
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
    if not rows:
        sys.exit("No sellable goods found — add a sellable inventory item first.")
    return random.choice(rows)


def build_oc_payload(
    self_id: int,
    self_company_payload: dict,
    buyer: dict,
    product: dict,
    quantity: int,
    tax_item: dict,
    tax_shipping: dict,
    self_billing: dict,
    buyer_billing: dict,
    buyer_delivery: dict,
    store: dict,
    payment_term: dict,
    bank: dict,
    selected_recipients: list,
    doc_number_obj: dict,
    series_list: list,
) -> dict:
    today = datetime.date.today()
    delivery_date = today + datetime.timedelta(days=10)
    unit_price = product["prices"].get("default") or product.get("price") or 100
    line_total = quantity * unit_price
    selected_unit = product["units"][0]
    delivery_date_str = _ddmmyyyy(delivery_date)

    item = {
        "id": product["id"],
        "product": product["id"],
        "uuid": product["uuid"],
        "itemid": product["itemid"],
        "product_name": product["product_name"],
        "item_name": product["product_name"],
        "hsn_code": product.get("hsn_code") or "",
        "category_name": product.get("category_name", ""),
        "quantity": str(quantity),
        "price": unit_price,
        "base_price": unit_price,
        "discount": 0,
        "base_discount": 0,
        "total_cost": line_total,
        "base_total_cost": line_total,
        "tax": 0,
        "base_tax": 0,
        "base_total_amount": line_total,
        "unit": selected_unit,
        "units": product["units"],
        "taxes": [_build_item_tax(tax_item, product["id"])],
        "taxes_data": {},
        "prices": product["prices"],
        "vendor_mapping": product.get("vendor_mapping"),
        "is_service": product.get("is_service", 0),
        "stock": product.get("stock", 0),
        "delivery_date": delivery_date_str,
        "position": 0,
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
        "custom_fields_parsed": product.get("custom_fields_parsed", {}),
        "inrConversionRate": "1",
    }

    buyer_id = buyer["company_id"]
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
        "transaction": {"title": f"{TRANSACTION_TITLE} {today.isoformat()}"},
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "buyerId": buyer_id,
        "deliveryLocationCompanyId": buyer_id,
        "supplierId": self_id,
        "sq_id": 0,
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": [item], "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": doc_number_obj,
            "customize_doc_number": "",
            "doc_date": _ddmmyyyy(today),
            "doc_amendment": 0,
            "delivery_date": delivery_date_str,
            "po_details": {},
            "sq_details": {},
            "payment_terms": payment_term,
            "store_details": store,
            "exportDetails": _export_details_default(),
            "documentSeriesList": series_list,
            "customFields": [],
        },
        "buyerDetails": {
            "buyerCompanyDetails": {
                "company_id": buyer_id,
                "name": buyer["name"],
                "email": "",
                "mobile_no": "",
                "company_image": buyer.get("company_image", ""),
            },
            "selectedBuyerBillingAddress": buyer_billing,
            "selectedBuyerDeliveryLocation": buyer_delivery,
            "placeOfSupply": place_of_supply,
            "addressPermission": 1,
            "kindAttention": "",
        },
        "supplierDetails": {
            "supplierCompanyDetails": self_company_payload,
            "selectedSupplierBillingAddress": self_billing,
            "addressPermission": 1,
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": bank,
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 1,
        "documentBlockDetails": {"docType": "oc", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": line_total * (1 + TAX_RATE_ITEM) + SHIPPING_CHARGE * (1 + TAX_RATE_SHIPPING),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
        "gstExtraCharges": [
            {
                "advance": 0,
                "description": "Shipping Charges",
                "total": str(SHIPPING_CHARGE),
                "taxes": [_build_charge_tax(tax_shipping)],
            }
        ],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": _empty_doc_discount(),
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": None,
            "baseAdvanceToPay": None,
        },
        "tcsDetails": {"amount": 0},
        "emailRecipients": {
            "selectedRecipients": selected_recipients,
            "cancelledRecipients": [],
            "subject": None,
            "introLine": None,
            "closingLine": None,
            "additionalRecipients": [],
        },
        "approvalData": {"approvalRuleType": "", "approvalMsg": "", "hasPermission": False},
        "checkApproval": False,
    }


def build_dependent_item(
    src: dict,
    line_qty: float,
    product: dict,
    tax_item: dict,
    self_id: int,
    delivery_date_str: str,
) -> dict:
    unit_obj = _transform_unit(src.get("unit"), product["units"])
    units_arr = _transform_units(src.get("units"), product["units"])
    line_price = float(src.get("price") or product["prices"].get("default") or 100)
    line_total_cost = line_qty * line_price
    return {
        "id": src.get("product") or product["id"],
        "product": src.get("product") or product["id"],
        "uuid": src.get("uuid") or product["uuid"],
        "itemid": src.get("itemid") or product["itemid"],
        "product_name": src.get("item_name") or product["product_name"],
        "item_name": src.get("item_name") or product["product_name"],
        "hsn_code": src.get("hsn_code") or "",
        "category_name": product.get("category_name", ""),
        "oc_item_id": src.get("oc_item_id"),
        "source_item_id": src.get("source_item_id"),
        "oc": src.get("oc"),
        "po_item": src.get("po_item"),
        "po": src.get("po"),
        "creation_date": src.get("creation_date"),
        "uid": src.get("uid"),
        "quantity": str(line_qty),
        "price": line_price,
        "base_price": line_price,
        "discount": 0,
        "base_discount": 0,
        "total_cost": line_total_cost,
        "base_total_cost": line_total_cost,
        "tax": 0,
        "base_tax": 0,
        "base_total_amount": line_total_cost * (1 + TAX_RATE_ITEM),
        "unit": unit_obj,
        "units": units_arr,
        "taxes": [_build_item_tax(tax_item, src.get("product") or product["id"])],
        "taxes_data": {},
        "prices": product["prices"],
        "vendor_mapping": product.get("vendor_mapping"),
        "is_service": 0,
        "stock": product.get("stock", 0),
        "delivery_date": delivery_date_str,
        "position": 0,
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
        "custom_fields": [],
        "customFields": [],
        "custom_fields_parsed": product.get("custom_fields_parsed", {}),
        "inrConversionRate": "1",
    }


def create_oc_chain(token: str) -> dict:
    self_id, self_company_payload, _user = fetch_self_company(token)
    log.info("Logged-in company: id=%s name=%s", self_id, self_company_payload["name"])

    buyer = fetch_buyer(token)
    buyer_id = buyer["company_id"]
    log.info("Picked buyer #%d: %s (id=%s)", BUYER_INDEX + 1, buyer["name"], buyer_id)

    product = fetch_random_product(token, buyer_id)
    log.info("Picked product: %s (id=%s)", product["product_name"], product["id"])

    taxes = _get(token, "/settings/tax/")["data"]["results"]
    tax_item = _find_gst_master(taxes, TAX_RATE_ITEM)
    tax_shipping = _find_gst_master(taxes, TAX_RATE_SHIPPING)
    log.info(
        "Tax masters: item=%s (%s), shipping=%s (%s)",
        tax_item["id"], tax_item["tax_name"], tax_shipping["id"], tax_shipping["tax_name"],
    )

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

    stores = _get(
        token, "/inventory/store/", params={"type": "main", "doc_type": "oc", "default": 1}
    )["data"]["results"]
    if not stores:
        sys.exit("No default supplier store found — add a main store first.")
    store = stores[0]

    payment_terms = _get(token, "/settings/payment-term/")["data"]["results"]
    payment_term = payment_terms[0] if payment_terms else {}

    banks = _get(token, "/settings/bank/")["data"]["results"]
    if not banks:
        sys.exit("No bank account configured — add one before running this automation.")
    bank = next((b for b in banks if b.get("is_default")), banks[0])

    recipients_resp = _get(
        token,
        "/documents/document_recipients/fetch_recipients/",
        params={"doc_type": "oc", "to_company": buyer_id},
    )["data"]
    selected_recipients = (recipients_resp.get("email_recipients") or {}).get("recipients") or []

    quantity = random.randint(QTY_MIN, QTY_MAX)
    log.info("Random order quantity: %d", quantity)

    oc_doc_number, oc_series_list = _resolve_doc_number(token, "oc", self_id)
    oc_payload = build_oc_payload(
        self_id, self_company_payload, buyer, product, quantity,
        tax_item, tax_shipping, self_billing, buyer_billing, buyer_delivery,
        store, payment_term, bank, selected_recipients, oc_doc_number, oc_series_list,
    )

    log.info("=== Creating OC ===")
    oc_resp = _post(token, "/documents/oc/create/", oc_payload)
    oc_doc_id = (oc_resp.get("data") or {}).get("doc_id")
    if not oc_doc_id:
        sys.exit(f"OC create did not return a doc_id. Response: {oc_resp}")
    log.info("OC created: doc_id=%s", oc_doc_id)

    oc_view = _get(
        token,
        "/documents/document/view/",
        params={"doc_type": OC_DOC_TYPE_INT, "doc_id": oc_doc_id},
    )["data"]["document_data"]
    transaction_id = oc_view["transaction"]["id"]
    log.info("OC transaction id: %s", transaction_id)

    today = datetime.date.today()
    delivery_date = today + datetime.timedelta(days=10)
    payment_date = today + datetime.timedelta(days=30)
    delivery_date_str = _ddmmyyyy(delivery_date)

    # --- Challan ---
    challan_ds = _get(
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
    challan_doc_number, challan_series_list = _resolve_doc_number(token, "challan", self_id)

    delivered_qty = round(quantity * CHALLAN_DELIVERY_FRACTION, 3)
    challan_items = [
        build_dependent_item(src, delivered_qty, product, tax_item, self_id, delivery_date_str)
        for src in challan_ds["items"]
    ]

    challan_payload = {
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
        "deliveryLocationCompanyId": buyer_id,
        "supplierId": self_id,
        "itemDetails": {
            "optionalColumns": OPTIONAL_COLUMNS,
            "items": challan_items,
            "fgItems": [],
            "itemCustomFields": [],
            "customFields": {"0": [], "1": []},
            "customFieldTableHeader": {"0": [], "1": []},
        },
        "primaryDocumentDetails": {
            "doc_number": challan_doc_number,
            "customize_doc_number": "",
            "doc_date": _ddmmyyyy(today),
            "delivery_date": delivery_date_str,
            "po_details": {"poNumber": ""},
            "oc_details": {
                "ocNumber": challan_ds.get("oc_number") or oc_doc_number["value"],
                "ocDate": _iso_to_ddmmyyyy(challan_ds.get("oc_date")) or _ddmmyyyy(today),
            },
            "transportation_details": {
                "payToTransporter": 0,
                "transporterGstNo": "",
                "transporterName": "",
                "vehicleNo": "",
                "transportationDocNo": "",
                "transportationDocDate": "",
                "transportersList": [],
            },
            "store_details": store,
            "exportDetails": _export_details_default(),
            "documentSeriesList": challan_series_list,
            "customFields": [],
        },
        "buyerDetails": {
            "buyerCompanyDetails": {
                "company_id": buyer_id,
                "name": buyer["name"],
                "email": "",
                "mobile_no": "",
                "company_image": buyer.get("company_image", ""),
            },
            "selectedBuyerBillingAddress": buyer_billing,
            "selectedBuyerDeliveryLocation": buyer_delivery,
            "placeOfSupply": {
                "city": buyer_delivery.get("city") or "",
                "state": buyer_delivery.get("state") or "",
                "country": buyer_delivery.get("country") or "India",
            },
            "addressPermission": 1,
            "kindAttention": "",
        },
        "supplierDetails": {
            "supplierCompanyDetails": self_company_payload,
            "selectedSupplierBillingAddress": self_billing,
            "addressPermission": 1,
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "challan", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": delivered_qty * (product["prices"].get("default") or 100) * (1 + TAX_RATE_ITEM),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": "-1",
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": _empty_doc_discount(),
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        "emailRecipients": {
            "selectedRecipients": selected_recipients,
            "cancelledRecipients": [],
            "subject": None,
            "introLine": None,
            "closingLine": None,
            "additionalRecipients": [],
        },
    }

    log.info("=== Creating Challan (%d%% delivery, qty=%s) ===",
             int(CHALLAN_DELIVERY_FRACTION * 100), delivered_qty)
    challan_resp = _post(token, "/documents/challan/create/", challan_payload)
    challan_doc_id = (challan_resp.get("data") or {}).get("doc_id")
    if not challan_doc_id:
        sys.exit(f"Challan create did not return a doc_id. Response: {challan_resp}")
    log.info("Challan created: doc_id=%s", challan_doc_id)

    # --- Invoice ---
    invoice_ds = _get(
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
    invoice_doc_number, invoice_series_list = _resolve_doc_number(token, "invoice", self_id)

    invoice_items = [
        build_dependent_item(
            src,
            float(src.get("quantity") or quantity),
            product,
            tax_item,
            self_id,
            delivery_date_str,
        )
        for src in invoice_ds["items"]
    ]

    invoice_payload = {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "invoice",
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
        "deliveryLocationCompanyId": buyer_id,
        "supplierId": self_id,
        "itemDetails": {
            "optionalColumns": OPTIONAL_COLUMNS,
            "items": invoice_items,
            "fgItems": [],
            "itemCustomFields": [],
            "customFields": {"0": [], "1": []},
            "customFieldTableHeader": {"0": [], "1": []},
        },
        "primaryDocumentDetails": {
            "doc_number": invoice_doc_number,
            "customize_doc_number": "",
            "doc_date": _ddmmyyyy(today),
            "po_details": {
                "poNumber": invoice_ds.get("oc_number") or oc_doc_number["value"],
                "poDate": _iso_to_ddmmyyyy(invoice_ds.get("oc_date")) or _ddmmyyyy(today),
            },
            "inward_details": {},
            "payment_terms": payment_term,
            "payment_date": _ddmmyyyy(payment_date),
            "select_inward": [],
            "store_details": store,
            "exportDetails": _export_details_default(),
            "documentSeriesList": invoice_series_list,
            "customFields": [],
        },
        "buyerDetails": {
            "buyerCompanyDetails": {
                "company_id": buyer_id,
                "name": buyer["name"],
                "email": "",
                "mobile_no": "",
                "company_image": buyer.get("company_image", ""),
            },
            "selectedBuyerBillingAddress": buyer_billing,
            "selectedBuyerDeliveryLocation": buyer_delivery,
            "placeOfSupply": {
                "city": buyer_delivery.get("city") or "",
                "state": buyer_delivery.get("state") or "",
                "country": buyer_delivery.get("country") or "India",
            },
            "addressPermission": 1,
            "kindAttention": "",
        },
        "supplierDetails": {
            "supplierCompanyDetails": self_company_payload,
            "selectedSupplierBillingAddress": self_billing,
            "addressPermission": 1,
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": bank,
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": True,
        "documentBlockDetails": {"docType": "invoice", "action": "create"},
        "document_config": {"price_type": "default"},
        "totalAmount": quantity * (product["prices"].get("default") or 100) * (1 + TAX_RATE_ITEM),
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": -1,
        "fgItems": [],
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": _empty_doc_discount(),
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": 0,
            "baseAdvanceToPay": 0,
        },
        "tcsDetails": {"amount": 0},
        "checkApproval": False,
    }

    log.info("=== Creating Invoice (full qty=%d) ===", quantity)
    invoice_resp = _post(token, "/documents/invoice/create/", invoice_payload)
    invoice_doc_id = (invoice_resp.get("data") or {}).get("doc_id")
    if not invoice_doc_id:
        sys.exit(f"Invoice create did not return a doc_id. Response: {invoice_resp}")
    log.info("Invoice created: doc_id=%s", invoice_doc_id)

    return {
        "transaction_id": transaction_id,
        "oc_doc_id": oc_doc_id,
        "challan_doc_id": challan_doc_id,
        "invoice_doc_id": invoice_doc_id,
        "buyer": buyer["name"],
        "buyer_id": buyer_id,
        "quantity": quantity,
        "delivered_qty": delivered_qty,
    }


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Login ===")
    token = login()
    summary = create_oc_chain(token)
    log.info("=== Done ===")
    log.info("Buyer:        %s (id=%s)", summary["buyer"], summary["buyer_id"])
    log.info("Transaction:  %s", summary["transaction_id"])
    log.info("OC:           %s  qty=%s", summary["oc_doc_id"], summary["quantity"])
    log.info("Challan:      %s  delivered=%s (%d%%)",
             summary["challan_doc_id"], summary["delivered_qty"],
             int(CHALLAN_DELIVERY_FRACTION * 100))
    log.info("Invoice:      %s  qty=%s", summary["invoice_doc_id"], summary["quantity"])


if __name__ == "__main__":
    main()
