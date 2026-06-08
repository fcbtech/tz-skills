#!/usr/bin/env python3
# pyright: reportAny=false
# pyright: reportUnusedCallResult=false
"""
Keka API helper.

Configuration is read from environment variables first, then from .env beside
this script. If values are missing, run:

  python keka_helper.py setup
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import stat
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict: TypeAlias = dict[str, JsonValue]

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
TOKEN_CACHE: dict[str, JsonValue] = {"access_token": None, "expires_at": 0}
REQUIRED_KEYS = ("KEKA_SUBDOMAIN", "KEKA_CLIENT_ID", "KEKA_CLIENT_SECRET", "KEKA_API_KEY")


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        return values

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


def load_config() -> dict[str, str]:
    file_values = parse_env_file(ENV_PATH)
    config: dict[str, str] = {}
    for key in (*REQUIRED_KEYS, "KEKA_ENV"):
        value = os.environ.get(key) or file_values.get(key)
        if value:
            config[key] = value
    config.setdefault("KEKA_ENV", "keka")
    return config


def save_config(config: dict[str, str]) -> None:
    ordered_keys = ("KEKA_SUBDOMAIN", "KEKA_ENV", "KEKA_CLIENT_ID", "KEKA_CLIENT_SECRET", "KEKA_API_KEY")
    lines = [f'{key}="{config[key].strip()}"' for key in ordered_keys if config.get(key)]
    _ = ENV_PATH.write_text("\n".join([*lines, ""]), encoding="utf-8")
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt_setup() -> None:
    current = load_config()
    config: dict[str, str] = {}

    subdomain_prompt = f"Keka tenant subdomain [{current.get('KEKA_SUBDOMAIN', '')}]: "
    subdomain = input(subdomain_prompt).strip() or current.get("KEKA_SUBDOMAIN", "")
    if not subdomain:
        sys.exit("KEKA_SUBDOMAIN is required.")
    config["KEKA_SUBDOMAIN"] = subdomain.strip().strip("/")

    env_prompt = f"Keka environment [{current.get('KEKA_ENV', 'keka')}]: "
    config["KEKA_ENV"] = input(env_prompt).strip() or current.get("KEKA_ENV", "keka")

    client_id_prompt = f"Keka client ID [{current.get('KEKA_CLIENT_ID', '')}]: "
    client_id = input(client_id_prompt).strip() or current.get("KEKA_CLIENT_ID", "")
    if not client_id:
        sys.exit("KEKA_CLIENT_ID is required.")
    config["KEKA_CLIENT_ID"] = client_id

    config["KEKA_CLIENT_SECRET"] = prompt_secret("Keka client secret", current.get("KEKA_CLIENT_SECRET"))
    config["KEKA_API_KEY"] = prompt_secret("Keka API key", current.get("KEKA_API_KEY"))

    save_config(config)
    print(f"Saved Keka config to {ENV_PATH}")


def prompt_secret(label: str, existing: str | None) -> str:
    if existing:
        keep = input(f"Existing {label} found. Keep it? [Y/n]: ").strip().lower()
        if keep in {"", "y", "yes"}:
            return existing
    value = getpass.getpass(f"{label}: ").strip()
    if not value:
        sys.exit(f"{label} is required.")
    return value


def require_config() -> dict[str, str]:
    config = load_config()
    missing = [key for key in REQUIRED_KEYS if not config.get(key)]
    if missing:
        sys.exit(
            "\n".join(
                [
                    f"Missing required config: {', '.join(missing)}.",
                    "Ask the user for the missing value(s), then run:",
                    "  python keka/scripts/keka_helper.py setup",
                ]
            )
        )
    return config


def token_url(config: dict[str, str]) -> str:
    return f"https://login.{config.get('KEKA_ENV', 'keka')}.com/connect/token"


def base_url(config: dict[str, str]) -> str:
    subdomain = config["KEKA_SUBDOMAIN"].strip().strip("/")
    environment = config.get("KEKA_ENV", "keka").strip().strip("/")
    return f"https://{subdomain}.{environment}.com/api/v1"


def as_dict(value: JsonValue) -> JsonDict:
    if isinstance(value, dict):
        return cast(JsonDict, value)
    return {}


def as_list(value: JsonValue) -> list[JsonValue]:
    if isinstance(value, list):
        return cast(list[JsonValue], value)
    return []


def as_text(value: JsonValue) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value)


def parse_json_text(value: str | None) -> JsonDict | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--json must be a JSON object")
    return cast(JsonDict, parsed)


def parse_param_items(items: list[str] | None) -> dict[str, str]:
    params: dict[str, str] = {}
    for item in items or []:
        if "=" not in item:
            raise ValueError(f"Invalid --param value {item!r}; expected key=value")
        key, value = item.split("=", 1)
        params[key] = value
    return params


def encode_form(data: dict[str, str]) -> bytes:
    return urllib.parse.urlencode(data).encode("utf-8")


def get_token() -> str:
    now = int(time.time())
    cached_token = TOKEN_CACHE.get("access_token")
    expires_at = TOKEN_CACHE.get("expires_at")
    if isinstance(cached_token, str) and isinstance(expires_at, int) and expires_at - now > 300:
        return cached_token

    config = require_config()
    data = {
        "grant_type": "kekaapi",
        "scope": "kekaapi",
        "client_id": config["KEKA_CLIENT_ID"],
        "client_secret": config["KEKA_CLIENT_SECRET"],
        "api_key": config["KEKA_API_KEY"],
    }
    request = urllib.request.Request(
        token_url(config),
        data=encode_form(data),
        headers={
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    payload = perform_request(request)
    token = as_text(as_dict(payload).get("access_token"))
    if not token:
        raise RuntimeError("Keka token response did not include access_token")

    expires_in_text = as_text(as_dict(payload).get("expires_in"))
    expires_in = int(expires_in_text) if expires_in_text.isdigit() else 86400
    TOKEN_CACHE["access_token"] = token
    TOKEN_CACHE["expires_at"] = now + expires_in
    return token


def perform_request(request: urllib.request.Request) -> JsonValue:
    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            body = response.read().decode("utf-8")
            if not body:
                return {"ok": True, "status": response.status}
            return cast(JsonValue, json.loads(body))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        retry_after = error.headers.get("Retry-After")
        try:
            error_body = cast(JsonValue, json.loads(body_text)) if body_text else {}
        except json.JSONDecodeError:
            error_body = body_text
        return {"ok": False, "status": error.code, "retry_after": retry_after, "error": error_body}
    except urllib.error.URLError as error:
        return {"ok": False, "error": str(error.reason)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON response from Keka"}


def api_request(method: str, path: str, *, params: dict[str, str] | None = None, body: JsonDict | None = None) -> JsonValue:
    config = require_config()
    clean_path = path if path.startswith("/") else f"/{path}"
    url = path if path.startswith("http://") or path.startswith("https://") else f"{base_url(config)}{clean_path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Bearer {get_token()}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }

    last_result: JsonValue = {}
    for attempt in range(3):
        request = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
        result = perform_request(request)
        last_result = result
        result_dict = as_dict(result)
        if result_dict.get("status") == 429 and attempt < 2:
            retry_after_text = as_text(result_dict.get("retry_after"))
            sleep_seconds = int(retry_after_text) if retry_after_text.isdigit() else 60
            time.sleep(sleep_seconds)
            continue
        return result
    return last_result


def get_all(path: str, *, params: dict[str, str] | None = None, page_size: int = 200) -> list[JsonValue]:
    working_params = dict(params or {})
    rows: list[JsonValue] = []
    page = 1
    while True:
        working_params.update({"pageNumber": str(page), "pageSize": str(page_size)})
        payload = api_request("GET", path, params=working_params)
        payload_dict = as_dict(payload)
        batch = as_list(payload_dict.get("data"))
        rows.extend(batch)
        total_pages_text = as_text(payload_dict.get("totalPages"))
        if not total_pages_text or page >= int(total_pages_text):
            break
        page += 1
    return rows


def write_json(path: str, value: JsonValue) -> None:
    output_path = Path(path)
    _ = output_path.write_text(json.dumps(value, indent=2), encoding="utf-8")
    print(f"Wrote JSON to {output_path}")


def print_json(value: JsonValue, pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, separators=(",", ":")))


def cmd_setup(_args: argparse.Namespace) -> JsonValue | None:
    prompt_setup()
    return None


def cmd_token_test(_args: argparse.Namespace) -> JsonValue:
    token = get_token()
    return {"ok": True, "token_present": bool(token), "token_preview": "<redacted>"}


def cmd_get(args: argparse.Namespace) -> JsonValue:
    return api_request("GET", args.path, params=parse_param_items(args.param))


def cmd_get_all(args: argparse.Namespace) -> JsonValue:
    rows = get_all(args.path, params=parse_param_items(args.param), page_size=args.page_size)
    if args.output:
        write_json(args.output, rows)
        return {"ok": True, "rows": len(rows), "output": args.output}
    return rows


def cmd_export_employees(args: argparse.Namespace) -> JsonValue:
    rows = get_all("/hris/employees", page_size=args.page_size)
    write_json(args.output, rows)
    return {"ok": True, "rows": len(rows), "output": args.output}


def cmd_raw(args: argparse.Namespace) -> JsonValue:
    body = parse_json_text(args.json)
    return api_request(args.method, args.path, params=parse_param_items(args.param), body=body)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Keka API helper")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Prompt for config and save scripts/.env")
    setup.set_defaults(func=cmd_setup)

    token_test = subparsers.add_parser("token-test", help="Validate credentials without printing the token")
    token_test.set_defaults(func=cmd_token_test)

    get = subparsers.add_parser("get", help="GET a Keka API path")
    get.add_argument("path", help="Path such as /hris/employees")
    get.add_argument("--param", action="append", help="Query parameter as key=value")
    get.set_defaults(func=cmd_get)

    get_all_parser = subparsers.add_parser("get-all", help="Fetch all pages for a list endpoint")
    get_all_parser.add_argument("path", help="Path such as /hris/employees")
    get_all_parser.add_argument("--param", action="append", help="Extra query parameter as key=value")
    get_all_parser.add_argument("--page-size", type=int, default=200)
    get_all_parser.add_argument("--output", help="Write rows to JSON file")
    get_all_parser.set_defaults(func=cmd_get_all)

    export_employees = subparsers.add_parser("export-employees", help="Export all employees to JSON")
    export_employees.add_argument("--page-size", type=int, default=200)
    export_employees.add_argument("--output", default="keka_employees.json")
    export_employees.set_defaults(func=cmd_export_employees)

    raw = subparsers.add_parser("raw", help="Call a Keka API path with any method")
    raw.add_argument("method", choices=["GET", "POST", "PUT", "PATCH", "DELETE", "get", "post", "put", "patch", "delete"])
    raw.add_argument("path", help="Path such as /time/leaverequests")
    raw.add_argument("--param", action="append", help="Query parameter as key=value")
    raw.add_argument("--json", help="JSON object request body")
    raw.set_defaults(func=cmd_raw)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    try:
        result = args.func(args)
    except ValueError as error:
        parser.error(str(error))

    if result is not None:
        print_json(cast(JsonValue, result), args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

