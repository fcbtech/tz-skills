"""Seed a demo account with three Sales Enquiries and one Sales Quotation.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md and runs:
  Phase 1: SE for the first available buyer (2 items).
  Phase 2: SE for the second available buyer (1 item), then flips its
           deal_status to Rejected via /quotations/deal/update/.
  Phase 3: SE for the first available buyer (1 item), then creates an
           SQ-from-SE against it.

Buyers and sellable items are discovered dynamically from the account's
counter-party network and product catalog (same pattern as the OC demo
automations in this folder).
"""

from __future__ import annotations

import logging
import random
import re
import sys
import time
import tomllib
from datetime import datetime
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

# Tranzact doc_type integers (from OBJECT_DICT).
SE_DOC_TYPE = 98
SQ_DOC_TYPE = 20
# deal_status enum for an SE — 3 = Rejected.
DEAL_STATUS_REJECTED = 3

# Per-line item quantity, randomised at run time.
QTY_MIN = 250
QTY_MAX = 750

# Seconds to pause after each SE/SQ is created.
PAUSE_SECONDS = 3

INR_CURRENCY = {
    "currency_name": "Rupees",
    "currency_code": "INR",
    "currency_hashcode": "8377",
    "currency_conversion_rate": "1",
    "currency_symbol": "₹",
    "currency_style": "en-IN",
    "currency_value": 1,
    "currency_dropdown_value": "Rupees - INR",
}

OPTIONAL_COLUMNS = [
    {"label": lbl, "display_on_form": 0, "display_on_sidebar": 1, "disabled": 0, "required": 0}
    for lbl in ("Alternate Unit", "Discount 1", "Discount 2", "Discount 3",
                "Total Tax", "Delivery Date", "Comments")
]

EMPTY_DOC_DISCOUNT = {
    f"doc_discount_{i}": {
        "chargeDescription": "",
        "chargeType": {"type": 1, "value": "%"},
        "value": "",
    }
    for i in (1, 2, 3)
}


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


def _self_company_id(token: str) -> int:
    """Decode the company_id claim out of the JWT (no signature verification)."""
    import base64 as _b64
    import json as _json
    payload_b64 = token.split(".")[1]
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return _json.loads(_b64.urlsafe_b64decode(payload_b64))["company_id"]


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


# --- Small utilities --------------------------------------------------------


def _iso_to_ddmmyyyy(value: str) -> str:
    """SE view returns se_date as ISO; sq_from_se create expects DD/MM/YYYY."""
    if not value:
        return ""
    if len(value) == 10 and value[2] == "/" and value[5] == "/":
        return value
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00").split(".")[0]).strftime("%d/%m/%Y")
    except (ValueError, TypeError):
        head = value.split(" ")[0].split("T")[0]
        if "-" in head:
            y, m, d = head.split("-")
            return f"{d}/{m}/{y}"
        return value


def _default_row(rows: list) -> dict:
    for r in rows:
        if r.get("default") in (1, True):
            return r
    return rows[0]


# --- Master-data fetches ----------------------------------------------------


def fetch_buyers(token: str) -> list:
    return _get(token, "/profile/counter-party/list/",
                params={"target_category": "buyer"})["data"]["results"]


def fetch_sell_products_with_tax(token: str) -> list:
    results = _get(token, "/settings/product/",
                   params={"product_type": "sell", "place": "product_name", "service_type": -1})["data"]["results"]
    return [p for p in results if p.get("taxes")]


def fetch_tax_master_by_id(token: str) -> dict:
    rows = _get(token, "/settings/tax/", params={})["data"]["results"]
    return {t["id"]: t for t in rows}


def fetch_billing_addresses(token: str, company_id: int) -> list:
    r = _get(token, "/settings/billing-address/get-addresses/", params={"company_id": company_id})
    return r if isinstance(r, list) else r["data"]


def fetch_delivery_locations(token: str, company_id: int) -> list:
    r = _get(token, "/settings/delivery-location/get-locations/", params={"company_id": company_id})
    return r if isinstance(r, list) else r["data"]


def fetch_first_payment_term(token: str) -> dict:
    rows = _get(token, "/settings/payment-term/", params={})["data"]["results"]
    # sq_from_se backend trips on payment_terms.payment_delta = None; prefer a term with delta set.
    with_delta = next((t for t in rows if t.get("payment_delta")), None)
    return with_delta or (rows[0] if rows else {})


def fetch_first_logistic(token: str, doc_type: str = "sales_quotation") -> dict:
    r = _get(token, "/settings/general/", params={"type": "logistics", "doc_type": doc_type})
    rows = (r.get("data") or r).get("results") or []
    return rows[0] if rows else {}


def fetch_default_store(token: str) -> dict:
    return _get(token, "/inventory/store/",
                params={"type": "store", "default": 1, "doc_type": "sales_enquiry"})["data"]["results"][0]


def fetch_se_doc_number(token: str, self_id: int) -> dict:
    return _get(token, "/settings/document-number/get_document_no/",
                params={"doc_type": "sales_enquiry", "is_service": 0, "company_id": self_id})["data"]


def fetch_sq_doc_number(token: str, self_id: int) -> dict:
    return _get(token, "/settings/document-number/get_document_no/",
                params={"doc_type": "sales_quotation", "is_service": 0, "company_id": self_id})["data"]


# --- Item & payload builders -----------------------------------------------


def build_item(product: dict, quantity: float, position: int,
               tax_master_by_id: dict, self_id: int) -> dict:
    full_tax = tax_master_by_id[product["taxes"][0]["tax_id"]]
    tax_master_block = {
        "id": full_tax["id"],
        "tax_type": full_tax["tax_type"],
        "tax_name": full_tax["tax_name"],
        "tax_percentage": float(full_tax["tax_percentage"]),
        "linked_cgst": full_tax.get("linked_cgst"),
        "linked_sgst": full_tax.get("linked_sgst"),
        "linked_igst": full_tax.get("linked_igst"),
        "linked_gst": full_tax.get("linked_gst"),
    }
    price = float(product.get("price") or 0) or 100.0
    line_total = round(quantity * price, 2)
    tax_pct = float(full_tax["tax_percentage"])
    tax_amt = round(line_total * tax_pct, 2)
    return {
        "id": product["id"],
        "product": product["id"],
        "uuid": product["uuid"],
        "itemid": product["itemid"],
        "product_name": product["product_name"],
        "item_name": product["product_name"],
        "hsn_code": product.get("hsn_code", ""),
        "quantity": str(quantity),
        "price": price,
        "base_price": price,
        "discount": 0,
        "base_discount": 0,
        "total_cost": line_total,
        "base_total_cost": line_total,
        "tax": tax_amt,
        "base_tax": tax_amt,
        "base_total_amount": line_total + tax_amt,
        "unit": product["units"][0],
        "units": product["units"],
        "taxes": [tax_master_block],
        "taxes_data": {},
        "prices": product.get("prices", {}),
        "vendor_mapping": product.get("vendor_mapping"),
        "is_service": product.get("is_service", 0),
        "stock": product.get("stock", 0),
        "delivery_date": "",
        "position": position,
        "active": 1,
        "client": self_id,
        "comment": "",
        "discount_type": {"type": 0, "value": "₹"},
        "item_discount_1": "0",
        "item_discount_2": "0",
        "item_discount_3": "0",
        "item_discount_type_1": {"type": 1, "value": "%"},
        "item_discount_type_2": {"type": 1, "value": "%"},
        "item_discount_type_3": {"type": 1, "value": "%"},
        "item_discount_type": {"type": 0, "value": "₹"},
        "item_discount_total": 0,
        "item_level_doc_discount": 0,
        "item_level_doc_discount_type": {"type": 0, "value": "₹"},
        "discount_data": {},
        "category_name": product.get("category_name", ""),
        "inrConversionRate": "1",
        "custom_fields_parsed": product.get("custom_fields_parsed", {}),
        "customFields": [],
        "key_mappings": {"text": "product_name", "value": "id"},
    }


def build_sq_from_se_item(product: dict, quantity: float, position: int,
                          tax_master_by_id: dict, self_id: int, source_se_id: int) -> dict:
    """Items for sq_from_se require MINIMAL taxes[0] (no linked_*) AND tax_percentage as
    a STRING. Sending the rich shape or numeric tax_percentage triggers HTTP 500
    ValueError: Inappropriate argument value (of correct type).
    """
    item = build_item(product, quantity, position, tax_master_by_id, self_id)
    full_tax = tax_master_by_id[product["taxes"][0]["tax_id"]]
    item["taxes"] = [{
        "id": full_tax["id"],
        "tax_type": full_tax["tax_type"],
        "tax_name": full_tax["tax_name"],
        "tax_percentage": str(full_tax["tax_percentage"]),
    }]
    item["se"] = source_se_id
    item["custom_fields"] = []
    item.pop("category_name", None)
    item.pop("inrConversionRate", None)
    item.pop("custom_fields_parsed", None)
    item.pop("key_mappings", None)
    return item


def build_se_payload(self_id: int, buyer_row: dict, items: list,
                     doc_num_resp: dict, store: dict,
                     self_bill: dict, buyer_bill: dict, buyer_delv: dict,
                     doc_date: str) -> dict:
    pos = {
        "city": buyer_delv["city"],
        "state": buyer_delv["state"],
        "state_code": buyer_delv["state_code"],
        "country": buyer_delv["country"],
    }
    total_amount = sum(i["total_cost"] + i["tax"] for i in items)
    buyer_id = buyer_row["company_id"]
    return {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "sales_enquiry", "service": 0, "export": 0,
            "supplierId": self_id, "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"}, "id": None,
        },
        "transaction_id": 0,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "itemDetails": {
            "items": items,
            "optionalColumns": OPTIONAL_COLUMNS,
            "itemCustomFields": [[]],
            "customFields": {"0": [], "1": []},
            "fgItems": [],
            "customFieldTableHeader": {"0": [], "1": []},
        },
        "primaryDocumentDetails": {
            "doc_number": doc_num_resp["doc_number"][0],
            "customize_doc_number": "",
            "doc_date": doc_date,
            "delivery_date": "",
            "payment_terms": {},
            "store_details": store,
            "customer_enquiry_details": {
                "customer_enquiry_number": "", "customer_enquiry_date": "",
                "expected_reply_date": "", "poc_name": "", "poc_contact": "",
            },
            "exportDetails": {"show_igst": False, "originCountry": "India",
                              "dischargeCountry": "India", "finalDestinationCountry": "India"},
            "documentSeriesList": doc_num_resp["doc_number"],
            "customFields": [],
        },
        "buyerDetails": {
            "buyerCompanyDetails": buyer_row,
            "selectedBuyerBillingAddress": buyer_bill,
            "selectedBuyerDeliveryLocation": buyer_delv,
            "placeOfSupply": pos,
            "addressPermission": 1,
            "kindAttention": "",
        },
        "buyerId": buyer_id,
        "deliveryLocationCompanyId": buyer_id,
        "supplierDetails": {
            "supplierCompanyDetails": {"company_id": self_id, "name": ""},
            "selectedSupplierBillingAddress": self_bill,
            "addressPermission": 1,
        },
        "supplierId": self_id,
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": EMPTY_DOC_DISCOUNT,
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": None,
            "baseAdvanceToPay": None,
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 0,
        "documentBlockDetails": {"docType": "sales_enquiry", "action": "create"},
        "document_config": {"price_type": "default"},
        "tcsDetails": {"text": "", "value": 0, "amount": 0},
        "totalAmount": total_amount,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": "-1",
    }


def build_sq_from_se_payload(self_id: int, source_se_id: int, source_se_doc: dict,
                             buyer_row: dict, items: list,
                             sq_doc_num_resp: dict, store: dict,
                             self_bill: dict, buyer_bill: dict, buyer_delv: dict,
                             doc_date: str) -> dict:
    pos = {
        "city": buyer_delv["city"],
        "state": buyer_delv["state"],
        "state_code": buyer_delv["state_code"],
        "country": buyer_delv["country"],
    }
    total_amount = sum(i["total_cost"] + i["tax"] for i in items)
    buyer_id = buyer_row["company_id"]
    return {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "sales_quotation", "service": 0, "export": 0,
            "supplierId": self_id, "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": source_se_doc.get("uuid"),
        },
        "transaction_id": 0,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "itemDetails": {
            "items": items,
            "optionalColumns": OPTIONAL_COLUMNS,
            "itemCustomFields": [[]],
            "customFields": {"0": [], "1": []},
            "fgItems": [],
            "customFieldTableHeader": {"0": [], "1": []},
        },
        "primaryDocumentDetails": {
            "doc_number": sq_doc_num_resp["doc_number"][0],
            "customize_doc_number": "",
            "doc_date": doc_date,
            "doc_amendment": 0,
            "delivery_date": "",
            "enquiry_details": {
                "enquiryNumber": source_se_doc.get("se_number") or source_se_doc.get("enquiry_number") or "",
                # SE view returns se_date as ISO; create endpoint expects DD/MM/YYYY.
                "enquiryDate": _iso_to_ddmmyyyy(source_se_doc.get("se_date")
                                                or source_se_doc.get("enquiry_date") or ""),
                "enquiryId": source_se_id,
            },
            "payment_terms": {},
            "store_details": store,
            "exportDetails": {"show_igst": False, "originCountry": "India",
                              "dischargeCountry": "India", "finalDestinationCountry": "India"},
            "documentSeriesList": sq_doc_num_resp["doc_number"],
            "customFields": [],
        },
        "buyerDetails": {
            "buyerCompanyDetails": {
                "company_id": buyer_id,
                "name": buyer_row.get("name", ""),
                "company_image": buyer_row.get("company_image", ""),
                "email": "",
                "mobile_no": "",
            },
            "selectedBuyerBillingAddress": buyer_bill,
            "selectedBuyerDeliveryLocation": buyer_delv,
            "placeOfSupply": pos,
            "addressPermission": 1,
            "kindAttention": "",
        },
        "buyerId": buyer_id,
        "deliveryLocationCompanyId": buyer_id,
        "supplierDetails": {
            "supplierCompanyDetails": {
                "company_id": self_id, "name": "",
                "email": "", "mobile_no": "", "company_image": "",
            },
            "selectedSupplierBillingAddress": self_bill,
            "addressPermission": 1,
        },
        "supplierId": self_id,
        "gstExtraCharges": [],
        "amountDetails": {
            "reverseCharge": False,
            "documentDiscount": EMPTY_DOC_DISCOUNT,
            "nonTaxableExtraCharges": [],
            "grandTotalRoundOff": False,
            "advanceToPay": None,
            "baseAdvanceToPay": None,
        },
        "additionalDocumentDetails": {
            "selectedLogisticDetails": {},
            "selectedTermsAndConditions": {},
            "selectedAccountDetails": {},
        },
        "emailRecipients": {
            "selectedRecipients": [],
            "cancelledRecipients": [],
            "subject": None,
            "introLine": None,
            "closingLine": None,
            "additionalRecipients": [],
        },
        "approvalData": {"approvalRuleType": "", "approvalMsg": "", "hasPermission": False},
        "attachments": [],
        "comment": {"value": ""},
        "attachSignature": 0,
        "documentBlockDetails": {"docType": "sales_quotation", "action": "sq_from_se"},
        "document_config": {"price_type": "default"},
        "tcsDetails": {"text": "", "value": 0, "amount": 0},
        "totalAmount": total_amount,
        "checkApproval": True,
        "save_action": "save_and_send",
        "action": "sq_from_se",
        "doc_id": str(source_se_id),
    }


# --- Business flow ----------------------------------------------------------


def view_doc(token: str, doc_type: int, doc_id: int) -> dict:
    return _get(token, "/documents/document/view/",
                params={"doc_type": doc_type, "doc_id": doc_id})["data"]["document_data"]


def create_se(token: str, self_id: int, buyer_row: dict, products_with_qty: list,
              tax_master_by_id: dict, store: dict, self_bill: dict,
              doc_date: str) -> int:
    buyer_id = buyer_row["company_id"]
    buyer_bill = _default_row(fetch_billing_addresses(token, buyer_id))
    buyer_delv = _default_row(fetch_delivery_locations(token, buyer_id))
    doc_num_resp = fetch_se_doc_number(token, self_id)
    items = [build_item(p, q, idx, tax_master_by_id, self_id)
             for idx, (p, q) in enumerate(products_with_qty)]
    payload = build_se_payload(self_id, buyer_row, items, doc_num_resp, store,
                               self_bill, buyer_bill, buyer_delv, doc_date)
    return _post(token, "/quotations/se/create/", payload)["data"]["doc_id"]


def set_deal_status(token: str, doc_id: int, doc_type: int, deal_status: int) -> dict:
    return _post(token, "/quotations/deal/update/", {
        "doc_id": doc_id, "doc_type": doc_type,
        "action": "deal_status_change", "deal_status": deal_status,
    })["data"]


def create_sq_from_se(token: str, self_id: int, buyer_row: dict, source_se_id: int,
                      product_qty: tuple, tax_master_by_id: dict, store: dict,
                      self_bill: dict, payment_term: dict, doc_date: str) -> int:
    buyer_id = buyer_row["company_id"]
    src = _get(token, "/quotations/sales_quotation/sq_from_se/",
               params={"type": "sales_quotation", "action": "sq_from_se",
                       "doc_id": source_se_id, "counter_company_id": buyer_id})["data"]
    source_se_doc = src["document_data"]
    product, qty = product_qty
    sq_items = [build_sq_from_se_item(product, qty, 0, tax_master_by_id, self_id, source_se_id)]
    buyer_bill = _default_row(fetch_billing_addresses(token, buyer_id))
    buyer_delv = _default_row(fetch_delivery_locations(token, buyer_id))
    sq_doc_num_resp = fetch_sq_doc_number(token, self_id)
    payload = build_sq_from_se_payload(self_id, source_se_id, source_se_doc,
                                       buyer_row, sq_items, sq_doc_num_resp, store,
                                       self_bill, buyer_bill, buyer_delv, doc_date)
    payload["primaryDocumentDetails"]["payment_terms"] = payment_term
    payload["additionalDocumentDetails"]["selectedLogisticDetails"] = fetch_first_logistic(token)
    return _post(token, "/quotations/sales_quotation/create/", payload)["data"]["doc_id"]


def main() -> None:
    token = login()
    self_id = _self_company_id(token)

    buyers = fetch_buyers(token)
    if len(buyers) < 2:
        raise RuntimeError(f"Need at least 2 buyers in the network; found {len(buyers)}.")
    buyer1, buyer2 = buyers[0], buyers[1]

    products = fetch_sell_products_with_tax(token)
    if len(products) < 3:
        raise RuntimeError(f"Need at least 3 sell-side products with a GST tax mapping; found {len(products)}.")
    # Random distinct products (varies per run) instead of always the first three.
    p1, p2, p3 = random.sample(products, 3)

    tax_master_by_id = fetch_tax_master_by_id(token)
    store = fetch_default_store(token)
    self_bill = _default_row(fetch_billing_addresses(token, self_id))
    doc_date = datetime.now().strftime("%d/%m/%Y")

    log.info("Phase 1: SE for buyer #1 (%s) with two items.", buyer1.get("name"))
    se1_id = create_se(token, self_id, buyer1,
                       [(p1, random.randint(QTY_MIN, QTY_MAX)), (p2, random.randint(QTY_MIN, QTY_MAX))],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 1: created SE doc_id=%s", se1_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)

    log.info("Phase 2: SE for buyer #2 (%s) with one item, then mark Rejected.", buyer2.get("name"))
    se2_id = create_se(token, self_id, buyer2, [(p1, random.randint(QTY_MIN, QTY_MAX))],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 2: created SE doc_id=%s", se2_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)
    log.info("Phase 2: flipping deal_status -> Rejected")
    deal_resp = set_deal_status(token, se2_id, SE_DOC_TYPE, DEAL_STATUS_REJECTED)
    if deal_resp.get("deal_status") != DEAL_STATUS_REJECTED:
        raise RuntimeError(
            f"Phase 2: deal/update did not persist deal_status=Rejected; got {deal_resp}"
        )
    log.info("Phase 2: SE %s deal_status_str = %s", se2_id, deal_resp.get("deal_status_str"))

    log.info("Phase 3: SE for buyer #1 (%s) with one item, then SQ-from-SE.", buyer1.get("name"))
    se3_qty = random.randint(QTY_MIN, QTY_MAX)
    se3_id = create_se(token, self_id, buyer1, [(p3, se3_qty)],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 3: created SE doc_id=%s", se3_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)
    payment_term = fetch_first_payment_term(token)
    sq_id = create_sq_from_se(token, self_id, buyer1, se3_id, (p3, se3_qty),
                              tax_master_by_id, store, self_bill, payment_term, doc_date)
    log.info("Phase 3: created SQ doc_id=%s (sourced from SE %s)", sq_id, se3_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)

    sq_view = view_doc(token, SQ_DOC_TYPE, sq_id)
    se3_view = view_doc(token, SE_DOC_TYPE, se3_id)
    if sq_view.get("enquiry_number") != se3_view.get("se_number"):
        raise RuntimeError(
            f"Phase 3: SQ {sq_id} enquiry_number={sq_view.get('enquiry_number')!r} "
            f"does not match source SE {se3_id} se_number={se3_view.get('se_number')!r}"
        )

    log.info(
        "Done. SE1=%s (buyer1, 2 items), SE2=%s (buyer2, 1 item, Rejected), "
        "SE3=%s (buyer1, 1 item) -> SQ=%s.",
        se1_id, se2_id, se3_id, sq_id,
    )


if __name__ == "__main__":
    main()
