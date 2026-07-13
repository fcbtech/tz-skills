"""Upload the company profile picture (logo) for a Tranzact account.

Original prompt: "create a new script in qa/generated/automations/demo_account_setup
to upload company profile picture."

Standalone — no imports from the qa framework. Reads inputs from data.md
adjacent to this script. Designed to be uploaded directly to a Lambda
function (or any host with `requests` available).

Flow:
  - Log into the account identified by EMAIL/PASSWORD in data.md.
  - GET /profile/info/fetch/ → resolve the company UUID (data.company.uuid).
  - PUT /api/v3/profile/company/profile-pic-upload/{company_uuid}
        (multipart/form-data: image_of=own_company + company_image file)
    The backend converts the image to WebP and stores it on the company.
  - Verify the response echoes image_of=own_company with a company_image path.

Logo source: LOGO_PATH in data.md. If LOGO_PATH is empty, the upload is
skipped (the script exits 0 without calling the API). When only a company
website is known, the calling agent fetches a logo from it, saves it to a
file, and points LOGO_PATH at that file before this script runs.

See the cataloged endpoint:
  qa/.ai/memory/knowledge/endpoints/profile-pic-upload-v3.md
"""

from __future__ import annotations

import logging
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

# image_of selects the variant: own_company = update the logged-in company's
# own logo (enforces ownership server-side). company = a counterparty image,
# user = the user's profile picture.
IMAGE_OF = "own_company"

# Multipart requests must NOT carry a manual Content-Type — requests sets the
# boundary itself. So this header set omits Content-Type on purpose.
DEFAULT_HEADERS = {
    "Accept": "application/json",
    "Tz-Request-Source": "webapp",
}


# --- Logging ----------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(message)s")
log = logging.getLogger("automation")

# --- Throttle-aware retry (per-call backoff on HTTP 429) --------------------

_THROTTLE_RETRIES = 4
_THROTTLE_BACKOFF = [3, 6, 12, 24]


def _throttle_sleep(resp, method, path, attempt):
    """Back off before retrying a rate-limited (429) request. Honors Retry-After."""
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


def _send(method, url, path_label, **kwargs):
    """Issue a request, retrying the single call on HTTP 429 with backoff."""
    for _tt in range(_THROTTLE_RETRIES + 1):
        resp = requests.request(method, url, **kwargs)
        if resp.status_code != 429 or _tt == _THROTTLE_RETRIES:
            return resp
        _throttle_sleep(resp, method.upper(), path_label, _tt)


# --- Auth -------------------------------------------------------------------


def login() -> str:
    """POST /main/login/password-login/ → access token."""
    url = f"{BASE_URL}/main/login/password-login/"
    log.info(">>> POST /main/login/password-login/")
    response = _send(
        "POST",
        url,
        "/main/login/password-login/",
        json={"email": DATA["EMAIL"], "password": DATA["PASSWORD"]},
        headers={"Content-Type": "application/json", **DEFAULT_HEADERS},
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


def fetch_company_uuid(token: str) -> str:
    """GET /profile/info/fetch/ → data.company.uuid (the target_uuid for own_company)."""
    url = f"{BASE_URL}/profile/info/fetch/"
    log.info(">>> GET /profile/info/fetch/")
    response = _send("GET", url, "/profile/info/fetch/", headers={"Authorization": f"Bearer {token}", **DEFAULT_HEADERS}, timeout=TIMEOUT)
    log.info("<<< GET /profile/info/fetch/ -> %d", response.status_code)
    if response.status_code >= 400:
        sys.exit(f"profile/info/fetch failed (HTTP {response.status_code}): {response.text[:300]}")
    company = ((response.json() or {}).get("data") or {}).get("company") or {}
    company_uuid = company.get("uuid")
    if not company_uuid:
        sys.exit("Could not resolve company UUID from /profile/info/fetch/ (data.company.uuid).")
    log.info("Resolved company UUID=%s", company_uuid)
    return company_uuid


# --- Logo bytes -------------------------------------------------------------


def resolve_logo() -> tuple[str, bytes, str]:
    """Return (filename, bytes, content_type) for the logo file at LOGO_PATH."""
    path = Path((DATA.get("LOGO_PATH") or "").strip()).expanduser()
    if not path.is_file():
        sys.exit(f"LOGO_PATH file not found: {path}")
    suffix = path.suffix.lower().lstrip(".") or "png"
    content_type = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
    log.info("Using logo file %s", path)
    return path.name, path.read_bytes(), content_type


# --- Business flow ----------------------------------------------------------


def upload_logo(token: str, company_uuid: str) -> dict:
    filename, image_bytes, content_type = resolve_logo()
    url = f"{BASE_URL}/api/v3/profile/company/profile-pic-upload/{company_uuid}"
    log.info(">>> PUT /api/v3/profile/company/profile-pic-upload/%s", company_uuid)
    response = _send(
        "PUT",
        url,
        "/api/v3/profile/company/profile-pic-upload",
        headers={"Authorization": f"Bearer {token}", **DEFAULT_HEADERS},
        data={"image_of": IMAGE_OF},
        files={"company_image": (filename, image_bytes, content_type)},
        timeout=TIMEOUT,
    )
    log.info("<<< PUT profile-pic-upload -> %d", response.status_code)
    if response.status_code >= 400:
        sys.exit(f"Logo upload failed (HTTP {response.status_code}): {response.text[:300]}")
    return response.json() or {}


# --- Main -------------------------------------------------------------------


def main() -> None:
    log.info("=== Upload company logo ===")
    if not (DATA.get("LOGO_PATH") or "").strip():
        log.info("LOGO_PATH is empty in data.md — skipping logo upload.")
        return

    token = login()
    company_uuid = fetch_company_uuid(token)
    result = upload_logo(token, company_uuid)

    if result.get("image_of") != IMAGE_OF:
        sys.exit(f"Unexpected image_of in response: {result!r}")
    stored_path = result.get("company_image")
    if not stored_path:
        sys.exit(f"Upload returned no company_image path: {result!r}")

    log.info("Company logo uploaded successfully.")
    log.info("  company_uuid : %s", result.get("id"))
    log.info("  stored image : %s", stored_path)


if __name__ == "__main__":
    main()
