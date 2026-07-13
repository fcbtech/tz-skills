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

Logo source, in priority order:
  1. LOGO_PATH in data.md — a local image file (png/jpg/webp/…).
  2. COMPANY_WEBSITE in data.md — if no usable file is given, best-effort
     fetch a logo from the company website (apple-touch-icon / sized icon /
     og:image / favicon), download it, and upload that.
If neither is set (or the website yields no usable image), the upload is
skipped (the script exits 0 without failing the run).

See the cataloged endpoint:
  qa/.ai/memory/knowledge/endpoints/profile-pic-upload-v3.md
"""

from __future__ import annotations

import html
import logging
import re
import sys
import time
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

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

# Best-effort website logo fetch (used only when no LOGO_PATH file is available).
_WEBSITE_UA = {"User-Agent": "Mozilla/5.0 (compatible; TranzactDemoBot/1.0)"}
_LOGO_IMG_EXT = {"png", "jpg", "jpeg", "webp", "gif", "ico", "svg"}

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


def _fetch_logo_from_website(url: str) -> tuple[str, bytes, str] | None:
    """Best-effort: fetch a logo image from a company website.

    Parses the homepage for logo candidates and returns the first that
    downloads as a real image, preferring the cleanest logo mark:
    apple-touch-icon → sized `<link rel=icon>` → og:image / twitter:image →
    small favicon → `/favicon.ico`. Returns (filename, bytes, content_type),
    or None if nothing usable is found. Network/parse errors propagate to the
    caller, which treats the logo as skippable.
    """
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    log.info("No logo file — fetching logo from website: %s", url)
    resp = requests.get(url, headers=_WEBSITE_UA, timeout=TIMEOUT)
    resp.raise_for_status()
    page, base = resp.text, resp.url

    def _size(tag: str) -> int:
        m = re.search(r'sizes=["\'](\d+)x\d+["\']', tag, re.I)
        return int(m.group(1)) if m else 0

    # (priority, -size, href) — lower priority number is tried first.
    scored: list[tuple[int, int, str]] = []
    for m in re.finditer(r'<link[^>]+rel=["\'][^"\']*apple-touch-icon[^"\']*["\'][^>]*>', page, re.I):
        h = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I)
        if h:
            scored.append((1, -_size(m.group(0)), h.group(1)))
    for m in re.finditer(r'<link[^>]+rel=["\'][^"\']*icon[^"\']*["\'][^>]*>', page, re.I):
        h = re.search(r'href=["\']([^"\']+)["\']', m.group(0), re.I)
        if h:
            sz = _size(m.group(0))  # big icons (>=64px) beat og:image; small favicons come after
            scored.append((2 if sz >= 64 else 4, -sz, h.group(1)))
    for m in re.finditer(r'<meta[^>]+(?:property|name)=["\'](?:og:image(?::secure_url)?|twitter:image)["\'][^>]*>', page, re.I):
        c = re.search(r'content=["\']([^"\']+)["\']', m.group(0), re.I)
        if c:
            scored.append((3, 0, c.group(1)))
    scored.append((5, 0, "/favicon.ico"))
    scored.sort(key=lambda t: (t[0], t[1]))

    tried: set[str] = set()
    for _prio, _neg, href in scored:
        img_url = urljoin(base, html.unescape(href))
        if img_url in tried:
            continue
        tried.add(img_url)
        try:
            r = requests.get(img_url, headers=_WEBSITE_UA, timeout=TIMEOUT)
        except requests.RequestException:
            continue
        if not r.ok or not r.content:
            continue
        ctype = (r.headers.get("Content-Type") or "").split(";")[0].strip().lower()
        ext = img_url.lower().rsplit(".", 1)[-1].split("?")[0]
        if not ctype.startswith("image/"):
            if ext not in _LOGO_IMG_EXT:
                continue  # not an image (e.g. an HTML error page) — skip
            ctype = "image/" + ("jpeg" if ext == "jpg" else ext)
        name = (img_url.rsplit("/", 1)[-1].split("?")[0]) or "logo"
        log.info("Selected website logo: %s (%s, %d bytes)", img_url, ctype, len(r.content))
        return name, r.content, ctype
    return None


def resolve_logo(logo_path: str, website: str) -> tuple[str, bytes, str] | None:
    """Resolve logo bytes from an explicit file, else from the company website.

    Priority: (1) LOGO_PATH file if it exists; (2) COMPANY_WEBSITE fetch.
    Returns (filename, bytes, content_type), or None when no logo can be
    resolved (the caller then skips the upload without failing the run).
    """
    if logo_path:
        path = Path(logo_path).expanduser()
        if path.is_file():
            suffix = path.suffix.lower().lstrip(".") or "png"
            content_type = "image/jpeg" if suffix in {"jpg", "jpeg"} else f"image/{suffix}"
            log.info("Using logo file %s", path)
            return path.name, path.read_bytes(), content_type
        if not website:
            sys.exit(f"LOGO_PATH file not found: {path}")
        log.warning("LOGO_PATH file not found (%s); falling back to COMPANY_WEBSITE.", path)

    if website:
        try:
            logo = _fetch_logo_from_website(website)
        except Exception as exc:  # best-effort — never fail the run over a logo
            log.warning("Could not fetch logo from website %r: %s", website, exc)
            return None
        if logo is None:
            log.warning("No usable logo image found on website %r.", website)
        return logo

    return None


# --- Business flow ----------------------------------------------------------


def upload_logo(token: str, company_uuid: str, logo: tuple[str, bytes, str]) -> dict:
    filename, image_bytes, content_type = logo
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
    logo_path = (DATA.get("LOGO_PATH") or "").strip()
    website = (DATA.get("COMPANY_WEBSITE") or "").strip()
    if not logo_path and not website:
        log.info("No LOGO_PATH or COMPANY_WEBSITE in data.md — skipping logo upload.")
        return

    token = login()
    company_uuid = fetch_company_uuid(token)
    logo = resolve_logo(logo_path, website)
    if logo is None:
        log.info("No logo could be resolved — skipping logo upload (non-fatal).")
        return
    result = upload_logo(token, company_uuid, logo)

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
