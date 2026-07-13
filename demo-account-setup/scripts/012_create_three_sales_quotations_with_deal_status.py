"""Seed a demo account with three direct Sales Quotations and deal-status flips.

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Logs into the account identified by EMAIL/PASSWORD in data.md and runs:
  Phase 1: SQ for the first available buyer (2 items).
  Phase 2: SQ for the second available buyer (1 item, the second available
           product), then flips its deal_status to Lost via
           /quotations/deal/update/.
  Phase 3: SQ for the first available buyer (1 item, the first available
           product), then creates an Order Confirmation (OC) from that SQ via
           /documents/oc/create/ (oc_from_sq). Creating the OC inherently
           flips the source SQ's deal_status to Won.

Buyers and sellable items are discovered dynamically from the account's
counter-party network and product catalog.
"""

from __future__ import annotations

import logging
import random
import re
import sys
import time
import tomllib
import uuid
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

# Tranzact doc_type integer (from OBJECT_DICT).
SQ_DOC_TYPE = 20
OC_DOC_TYPE = 4
# deal_status enum for an SQ — 4 = Won, 5 = Lost.
DEAL_STATUS_WON = 4
DEAL_STATUS_LOST = 5

# Title for the transaction opened by the OC created from the phase-3 SQ.
OC_TRANSACTION_TITLE = "Sales Demo OC (from SQ)"

# Per-line item quantity, randomised at run time.
QTY_MIN = 250
QTY_MAX = 750

# Seconds to pause after each SQ is created.
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
        r = requests.get(url, params=params, headers=_auth_headers(token), timeout=TIMEOUT)
        if r.status_code != 429 or _tt == _THROTTLE_RETRIES:
            break
        _throttle_sleep(r, "GET", path, _tt)
    log.info("<<< GET %s -> %d", path, r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"GET {path} failed (HTTP {r.status_code}): {r.text[:500]}")
    return r.json()


def _post(token: str, path: str, payload: dict) -> dict:
    url = f"{BASE_URL}{path}"
    log.info(">>> POST %s", path)
    for _tt in range(_THROTTLE_RETRIES + 1):
        r = requests.post(url, json=payload, headers=_auth_headers(token), timeout=TIMEOUT)
        if r.status_code != 429 or _tt == _THROTTLE_RETRIES:
            break
        _throttle_sleep(r, "POST", path, _tt)
    log.info("<<< POST %s -> %d", path, r.status_code)
    if r.status_code >= 400:
        raise RuntimeError(f"POST {path} failed (HTTP {r.status_code}): {r.text[:500]}")
    return r.json()


# --- Small utilities --------------------------------------------------------


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
                   params={"product_type": "sell", "place": "product_name",
                           "service_type": -1})["data"]["results"]
    return [p for p in results if p.get("taxes")]


def fetch_tax_master_by_id(token: str) -> dict:
    rows = _get(token, "/settings/tax/", params={})["data"]["results"]
    return {t["id"]: t for t in rows}


def fetch_billing_addresses(token: str, company_id: int) -> list:
    r = _get(token, "/settings/billing-address/get-addresses/",
             params={"company_id": company_id})
    return r if isinstance(r, list) else r["data"]


def fetch_delivery_locations(token: str, company_id: int) -> list:
    r = _get(token, "/settings/delivery-location/get-locations/",
             params={"company_id": company_id})
    return r if isinstance(r, list) else r["data"]


def fetch_default_store(token: str) -> dict:
    return _get(token, "/inventory/store/",
                params={"type": "store", "default": 1,
                        "doc_type": "sales_quotation"})["data"]["results"][0]


def fetch_sq_doc_number(token: str, self_id: int) -> dict:
    return _get(token, "/settings/document-number/get_document_no/",
                params={"doc_type": "sales_quotation", "is_service": 0,
                        "company_id": self_id})["data"]


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


def build_sq_payload(self_id: int, buyer_row: dict, items: list,
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
            "id": None,
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
            "enquiry_details": {},
            "payment_terms": {},
            "store_details": store,
            "exportDetails": {"show_igst": False, "originCountry": "India",
                              "dischargeCountry": "India",
                              "finalDestinationCountry": "India"},
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
        "documentBlockDetails": {"docType": "sales_quotation", "action": "create"},
        "document_config": {"price_type": "default"},
        "tcsDetails": {"text": "", "value": 0, "amount": 0},
        "totalAmount": total_amount,
        "checkApproval": True,
        "save_action": "save_and_send",
        "action": "create",
        "doc_id": "-1",
    }


# --- Business flow ----------------------------------------------------------


def view_doc(token: str, doc_type: int, doc_id: int) -> dict:
    return _get(token, "/documents/document/view/",
                params={"doc_type": doc_type, "doc_id": doc_id})["data"]["document_data"]


def create_sq(token: str, self_id: int, buyer_row: dict, products_with_qty: list,
              tax_master_by_id: dict, store: dict, self_bill: dict,
              doc_date: str) -> int:
    buyer_id = buyer_row["company_id"]
    buyer_bill = _default_row(fetch_billing_addresses(token, buyer_id))
    buyer_delv = _default_row(fetch_delivery_locations(token, buyer_id))
    sq_doc_num_resp = fetch_sq_doc_number(token, self_id)
    items = [build_item(p, q, idx, tax_master_by_id, self_id)
             for idx, (p, q) in enumerate(products_with_qty)]
    payload = build_sq_payload(self_id, buyer_row, items, sq_doc_num_resp, store,
                               self_bill, buyer_bill, buyer_delv, doc_date)
    return _post(token, "/quotations/sales_quotation/create/", payload)["data"]["doc_id"]


def set_deal_status(token: str, doc_id: int, doc_type: int, deal_status: int) -> dict:
    return _post(token, "/quotations/deal/update/", {
        "doc_id": doc_id, "doc_type": doc_type,
        "action": "deal_status_change", "deal_status": deal_status,
    })["data"]


# --- OC-from-SQ -------------------------------------------------------------


def _format_doc_date(raw: str | None, fallback: str) -> str:
    """Normalise a backend doc-date (ISO or dd/mm/yyyy) to dd/mm/yyyy."""
    if not raw:
        return fallback
    try:
        if "-" in raw:
            return datetime.fromisoformat(raw.split(".")[0].replace("T", " ")).strftime("%d/%m/%Y")
    except ValueError:
        pass
    return raw or fallback


def resolve_oc_doc_number(token: str, self_id: int) -> tuple[dict, list]:
    resp = _get(token, "/settings/document-number/get_document_no/",
                params={"doc_type": "oc", "is_service": 0, "company_id": self_id})["data"]
    series_list = resp["doc_number"]
    if resp.get("manual_number"):
        return {"id": None, "value": f"OC-AUTO-{uuid.uuid4().hex[:8]}"}, series_list
    chosen = series_list[0]
    return {"id": chosen["id"], "value": chosen["value"]}, series_list


def fetch_oc_store(token: str) -> dict:
    stores = _get(token, "/inventory/store/",
                  params={"type": "main", "doc_type": "oc"})["data"]["results"]
    if not stores:
        raise RuntimeError("No stores found for the logged-in company — add a main store first.")
    return stores[0]


def build_oc_from_sq_payload(self_id: int, buyer_row: dict, items: list,
                             sq_id: int, sq_number: str, sq_date: str,
                             oc_doc_num_obj: dict, oc_series_list: list, store: dict,
                             self_bill: dict, buyer_bill: dict, buyer_delv: dict,
                             doc_date: str) -> dict:
    """OC-from-SQ create payload.

    Mirrors the direct-OC shape proven on this account (006_*), with the
    OC-from-SQ discriminators confirmed by the INR chain:
      (a) top-level sq_id=<source-sq-id>
      (b) primaryDocumentDetails.sq_details={sqNumber, sqDate}
      (c) per-line items[].sq=<sq_id>
    details.id is a FRESH UUID — the OC opens the transaction.
    """
    buyer_id = buyer_row["company_id"]
    buyer_company_payload = {
        "company_id": buyer_id,
        "name": buyer_row.get("name", ""),
        "email": "",
        "mobile_no": "",
        "company_image": buyer_row.get("company_image", ""),
    }
    place_of_supply = {
        "city": buyer_delv.get("city") or "",
        "state": buyer_delv.get("state") or "",
        "state_code": buyer_delv.get("state_code"),
        "country": buyer_delv.get("country") or "India",
    }
    # Tag each line with its source SQ — the OC-from-SQ per-line linkage.
    for pos, it in enumerate(items):
        it["sq"] = sq_id
        it["position"] = pos
    return {
        "userCompany": {"id": self_id, "type": "supplier"},
        "details": {
            "docType": "oc", "service": 0, "export": 0,
            "supplierId": self_id, "buyerId": buyer_id,
            "userCompany": {"id": self_id, "type": "supplier"},
            "id": str(uuid.uuid4()),
        },
        "transaction_id": 0,
        "transaction": {"title": OC_TRANSACTION_TITLE, "uuid": str(uuid.uuid4())},
        "sq_id": sq_id,
        "currency": INR_CURRENCY,
        "exportOption": 0,
        "counterPartyId": buyer_id,
        "supplierId": self_id,
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
        "buyerDetails": {
            "buyerCompanyDetails": buyer_company_payload,
            "selectedBuyerBillingAddress": buyer_bill,
            "selectedBuyerDeliveryLocation": buyer_delv,
            "addressPermission": 1,
            "kindAttention": "",
            "placeOfSupply": place_of_supply,
        },
        "itemDetails": {"optionalColumns": OPTIONAL_COLUMNS, "items": items, "fgItems": []},
        "primaryDocumentDetails": {
            "doc_number": oc_doc_num_obj,
            "customize_doc_number": "",
            "doc_date": doc_date,
            "doc_amendment": 0,
            "delivery_date": "",
            "po_details": {},
            "sq_details": {"sqNumber": sq_number, "sqDate": sq_date},
            "payment_terms": {},
            "store_details": store,
            "exportDetails": {"show_igst": False, "originCountry": "India",
                              "dischargeCountry": "India",
                              "finalDestinationCountry": "India"},
            "documentSeriesList": oc_series_list,
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
            "subject": None, "introLine": None, "closingLine": None,
            "additionalRecipients": [],
        },
        "approvalData": {"approvalRuleType": "", "approvalMsg": "", "hasPermission": False},
        "checkApproval": False,
    }


def create_oc_from_sq(token: str, self_id: int, buyer_row: dict, sq_id: int,
                      sq_view: dict, products_with_qty: list,
                      tax_master_by_id: dict, doc_date: str) -> int:
    buyer_id = buyer_row["company_id"]
    # Backend prerequisite: rehydrate the SQ into an OC draft structure.
    _get(token, "/documents/oc/get_doc_structure_sq/",
         params={"type": "oc", "doc_id": -1, "transaction_id": 0,
                 "oc_type": "oc_from_sq", "action": "create",
                 "sq_id": sq_id, "counter_company_id": buyer_id})

    sq_number = sq_view.get("sq_number") or sq_view.get("document_no_text") or ""
    sq_date = _format_doc_date(sq_view.get("sq_date") or sq_view.get("doc_date"), doc_date)

    self_bill = _default_row(fetch_billing_addresses(token, self_id))
    buyer_bill = _default_row(fetch_billing_addresses(token, buyer_id))
    buyer_delv = _default_row(fetch_delivery_locations(token, buyer_id))
    store = fetch_oc_store(token)
    oc_doc_num_obj, oc_series_list = resolve_oc_doc_number(token, self_id)

    items = [build_item(p, q, idx, tax_master_by_id, self_id)
             for idx, (p, q) in enumerate(products_with_qty)]
    payload = build_oc_from_sq_payload(self_id, buyer_row, items, sq_id, sq_number, sq_date,
                                       oc_doc_num_obj, oc_series_list, store,
                                       self_bill, buyer_bill, buyer_delv, doc_date)
    resp = _post(token, "/documents/oc/create/", payload)
    oc_id = (resp.get("data") or {}).get("doc_id")
    if not oc_id:
        raise RuntimeError(f"OC-from-SQ create returned no doc_id: {resp}")
    return oc_id


def main() -> None:
    token = login()
    self_id = _self_company_id(token)

    buyers = fetch_buyers(token)
    if len(buyers) < 2:
        raise RuntimeError(f"Need at least 2 buyers in the network; found {len(buyers)}.")
    buyer1, buyer2 = buyers[0], buyers[1]

    products = fetch_sell_products_with_tax(token)
    if len(products) < 2:
        raise RuntimeError(f"Need at least 2 sell-side products with a GST tax mapping; found {len(products)}.")
    p1, p2 = products[0], products[1]

    tax_master_by_id = fetch_tax_master_by_id(token)
    store = fetch_default_store(token)
    self_bill = _default_row(fetch_billing_addresses(token, self_id))
    doc_date = datetime.now().strftime("%d/%m/%Y")

    log.info("Phase 1: SQ for buyer #1 (%s) with two items.", buyer1.get("name"))
    sq1_id = create_sq(token, self_id, buyer1,
                       [(p1, random.randint(QTY_MIN, QTY_MAX)), (p2, random.randint(QTY_MIN, QTY_MAX))],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 1: created SQ doc_id=%s", sq1_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)

    log.info("Phase 2: SQ for buyer #2 (%s) with one item, then mark Lost.", buyer2.get("name"))
    sq2_id = create_sq(token, self_id, buyer2, [(p2, random.randint(QTY_MIN, QTY_MAX))],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 2: created SQ doc_id=%s", sq2_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)
    log.info("Phase 2: flipping deal_status -> Lost")
    deal2 = set_deal_status(token, sq2_id, SQ_DOC_TYPE, DEAL_STATUS_LOST)
    if deal2.get("deal_status") != DEAL_STATUS_LOST:
        raise RuntimeError(
            f"Phase 2: deal/update did not persist deal_status=Lost; got {deal2}"
        )
    log.info("Phase 2: SQ %s deal_status_str = %s", sq2_id, deal2.get("deal_status_str"))

    log.info("Phase 3: SQ for buyer #1 (%s) with one item, then create an OC from it.",
             buyer1.get("name"))
    sq3_qty = random.randint(QTY_MIN, QTY_MAX)
    sq3_id = create_sq(token, self_id, buyer1, [(p1, sq3_qty)],
                       tax_master_by_id, store, self_bill, doc_date)
    log.info("Phase 3: created SQ doc_id=%s", sq3_id)
    log.info("Pausing %d seconds...", PAUSE_SECONDS)
    time.sleep(PAUSE_SECONDS)
    log.info("Phase 3: creating OC from SQ %s (auto-marks the deal Won)", sq3_id)
    sq3_view = view_doc(token, SQ_DOC_TYPE, sq3_id)
    oc_id = create_oc_from_sq(token, self_id, buyer1, sq3_id, sq3_view, [(p1, sq3_qty)],
                              tax_master_by_id, doc_date)
    log.info("Phase 3: created OC doc_id=%s from SQ %s", oc_id, sq3_id)
    # Creating the OC from the SQ should inherently flip the SQ's deal_status to Won.
    sq3_after = view_doc(token, SQ_DOC_TYPE, sq3_id)
    if sq3_after.get("deal_status") == DEAL_STATUS_WON:
        log.info("Phase 3: SQ %s deal_status_str = %s (auto-flipped by OC)",
                 sq3_id, sq3_after.get("deal_status_str"))
    else:
        log.warning(
            "Phase 3: OC %s created, but SQ %s deal_status did not flip to Won "
            "(got deal_status=%r, deal_status_str=%r).",
            oc_id, sq3_id, sq3_after.get("deal_status"), sq3_after.get("deal_status_str"),
        )

    log.info(
        "Done. SQ1=%s (buyer1, 2 items), SQ2=%s (buyer2, 1 item, Lost), "
        "SQ3=%s (buyer1, 1 item) -> OC=%s (auto-Won).",
        sq1_id, sq2_id, sq3_id, oc_id,
    )


if __name__ == "__main__":
    main()
