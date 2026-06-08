#!/usr/bin/env python3
# pyright: reportAny=false
# pyright: reportUnusedCallResult=false
"""
Freshdesk API helper.

Configuration is read from environment variables first, then from .env beside
this script. If values are missing, run:

  python freshdesk_helper.py setup
"""

from __future__ import annotations

import argparse
import base64
import getpass
import json
import os
import stat
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict: TypeAlias = dict[str, JsonValue]

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
DEFAULT_DOMAIN = "tranzact.freshdesk.com"


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


def normalize_domain(domain: str) -> str:
    value = domain.strip()
    if value.startswith("http://") or value.startswith("https://"):
        parsed = urllib.parse.urlparse(value)
        value = parsed.netloc or parsed.path
    value = value.strip("/")
    if not value:
        return value
    if "." not in value:
        value = f"{value}.freshdesk.com"
    return value


def load_config() -> tuple[str | None, str | None]:
    file_values = parse_env_file(ENV_PATH)
    domain = os.environ.get("FRESHDESK_DOMAIN") or file_values.get("FRESHDESK_DOMAIN") or DEFAULT_DOMAIN
    api_key = os.environ.get("FRESHDESK_API_KEY") or file_values.get("FRESHDESK_API_KEY")
    return (normalize_domain(domain) if domain else None, api_key)


def save_config(domain: str, api_key: str) -> None:
    ENV_PATH.write_text(
        "\n".join(
            [
                f'FRESHDESK_DOMAIN="{normalize_domain(domain)}"',
                f'FRESHDESK_API_KEY="{api_key.strip()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt_setup() -> None:
    current_domain, current_key = load_config()
    domain_prompt = "Freshdesk domain"
    if current_domain:
        domain_prompt += f" [{current_domain}]"
    domain_prompt += ": "

    domain = input(domain_prompt).strip() or current_domain
    if not domain:
        sys.exit("Freshdesk domain is required.")

    if current_key:
        use_existing = input("Existing API key found. Keep it? [Y/n]: ").strip().lower()
        if use_existing in {"", "y", "yes"}:
            api_key = current_key
        else:
            api_key = getpass.getpass("Freshdesk API key: ").strip()
    else:
        api_key = getpass.getpass("Freshdesk API key: ").strip()

    if not api_key:
        sys.exit("Freshdesk API key is required.")

    save_config(domain, api_key)
    print(f"Saved Freshdesk config to {ENV_PATH}")


def require_config() -> tuple[str, str]:
    domain, api_key = load_config()
    missing: list[str] = []
    if not api_key:
        missing.append("FRESHDESK_API_KEY")
    if missing:
        sys.exit(
            "\n".join(
                [
                    f"Missing required config: {', '.join(missing)}.",
                    "Ask the user for the missing API key, then run:",
                    "  python freshdesk/scripts/freshdesk_helper.py setup",
                ]
            )
        )
    assert domain is not None
    assert api_key is not None
    return domain, api_key


def as_dict(value: JsonValue) -> JsonDict:
    if isinstance(value, dict):
        return cast(JsonDict, value)
    return {}


def parse_json_text(value: str | None) -> JsonDict | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("--json must be a JSON object")
    return cast(JsonDict, parsed)


def build_query(params: dict[str, str | int | None]) -> str:
    clean = {key: str(value) for key, value in params.items() if value is not None}
    return urllib.parse.urlencode(clean)


def request(method: str, path: str, body: JsonDict | None = None, query: str | None = None) -> JsonValue:
    domain, api_key = require_config()
    clean_path = path if path.startswith("/") else f"/{path}"
    if not clean_path.startswith("/api/"):
        clean_path = f"/api/v2{clean_path}"

    url = f"https://{domain}{clean_path}"
    if query:
        url = f"{url}?{query}"

    token = base64.b64encode(f"{api_key}:X".encode("utf-8")).decode("ascii")
    data = None if body is None else json.dumps(body).encode("utf-8")
    headers = {
        "Authorization": f"Basic {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())

    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            if not text:
                return {"ok": True, "status": response.status}
            return cast(JsonValue, json.loads(text))
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        retry_after = error.headers.get("Retry-After")
        try:
            error_body = cast(JsonValue, json.loads(body_text)) if body_text else {}
        except json.JSONDecodeError:
            error_body = body_text
        return {
            "ok": False,
            "status": error.code,
            "retry_after": retry_after,
            "error": error_body,
        }
    except urllib.error.URLError as error:
        return {"ok": False, "error": str(error.reason)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON response from Freshdesk"}


def print_json(value: JsonValue, pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, separators=(",", ":")))


def cmd_setup(_args: argparse.Namespace) -> JsonValue | None:
    prompt_setup()
    return None


def cmd_me(_args: argparse.Namespace) -> JsonValue:
    return request("GET", "/agents/me")


def cmd_list_tickets(args: argparse.Namespace) -> JsonValue:
    query = build_query({"page": args.page, "per_page": args.per_page, "updated_since": args.updated_since})
    return request("GET", "/tickets", query=query)


def cmd_get_ticket(args: argparse.Namespace) -> JsonValue:
    return request("GET", f"/tickets/{args.ticket_id}")


def cmd_search_tickets(args: argparse.Namespace) -> JsonValue:
    query = build_query({"query": args.query})
    return request("GET", "/search/tickets", query=query)


def cmd_create_ticket(args: argparse.Namespace) -> JsonValue:
    body: JsonDict = {
        "subject": args.subject,
        "description": args.description,
        "email": args.email,
        "priority": args.priority,
        "status": args.status,
    }
    extra = parse_json_text(args.json)
    if extra:
        body.update(extra)
    return request("POST", "/tickets", body=body)


def cmd_update_ticket(args: argparse.Namespace) -> JsonValue:
    body = parse_json_text(args.json)
    if body is None:
        raise ValueError("update-ticket requires --json with a JSON object body")
    return request("PUT", f"/tickets/{args.ticket_id}", body=body)


def cmd_list_contacts(args: argparse.Namespace) -> JsonValue:
    query = build_query({"page": args.page, "per_page": args.per_page})
    return request("GET", "/contacts", query=query)


def cmd_get_contact(args: argparse.Namespace) -> JsonValue:
    return request("GET", f"/contacts/{args.contact_id}")


def cmd_search_contacts(args: argparse.Namespace) -> JsonValue:
    query = build_query({"term": args.term})
    return request("GET", "/contacts/autocomplete", query=query)


def cmd_create_contact(args: argparse.Namespace) -> JsonValue:
    body: JsonDict = {"name": args.name, "email": args.email}
    extra = parse_json_text(args.json)
    if extra:
        body.update(extra)
    return request("POST", "/contacts", body=body)


def cmd_raw(args: argparse.Namespace) -> JsonValue:
    body = parse_json_text(args.json)
    return request(args.method, args.path, body=body, query=args.query)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freshdesk API helper")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Prompt for domain/API key and save scripts/.env")
    setup.set_defaults(func=cmd_setup)

    me = subparsers.add_parser("me", help="Show the currently authenticated Freshdesk agent")
    me.set_defaults(func=cmd_me)

    list_tickets = subparsers.add_parser("list-tickets", help="List tickets")
    list_tickets.add_argument("--page", type=int, default=1)
    list_tickets.add_argument("--per-page", type=int, default=30)
    list_tickets.add_argument("--updated-since")
    list_tickets.set_defaults(func=cmd_list_tickets)

    get_ticket = subparsers.add_parser("get-ticket", help="Read a ticket by ID")
    get_ticket.add_argument("ticket_id")
    get_ticket.set_defaults(func=cmd_get_ticket)

    search_tickets = subparsers.add_parser("search-tickets", help="Search tickets")
    search_tickets.add_argument("query")
    search_tickets.set_defaults(func=cmd_search_tickets)

    create_ticket = subparsers.add_parser("create-ticket", help="Create a ticket")
    create_ticket.add_argument("--subject", required=True)
    create_ticket.add_argument("--email", required=True)
    create_ticket.add_argument("--description", required=True)
    create_ticket.add_argument("--priority", type=int, required=True)
    create_ticket.add_argument("--status", type=int, required=True)
    create_ticket.add_argument("--json", help="Additional JSON object fields")
    create_ticket.set_defaults(func=cmd_create_ticket)

    update_ticket = subparsers.add_parser("update-ticket", help="Update a ticket with a JSON body")
    update_ticket.add_argument("ticket_id")
    update_ticket.add_argument("--json", required=True, help="JSON object body")
    update_ticket.set_defaults(func=cmd_update_ticket)

    list_contacts = subparsers.add_parser("list-contacts", help="List contacts")
    list_contacts.add_argument("--page", type=int, default=1)
    list_contacts.add_argument("--per-page", type=int, default=30)
    list_contacts.set_defaults(func=cmd_list_contacts)

    get_contact = subparsers.add_parser("get-contact", help="Read a contact by ID")
    get_contact.add_argument("contact_id")
    get_contact.set_defaults(func=cmd_get_contact)

    search_contacts = subparsers.add_parser("search-contacts", help="Autocomplete contacts by keyword")
    search_contacts.add_argument("term")
    search_contacts.set_defaults(func=cmd_search_contacts)

    create_contact = subparsers.add_parser("create-contact", help="Create a contact")
    create_contact.add_argument("--name", required=True)
    create_contact.add_argument("--email", required=True)
    create_contact.add_argument("--json", help="Additional JSON object fields")
    create_contact.set_defaults(func=cmd_create_contact)

    raw = subparsers.add_parser("raw", help="Call any Freshdesk API path")
    raw.add_argument("method", choices=["GET", "POST", "PUT", "DELETE", "get", "post", "put", "delete"])
    raw.add_argument("path", help="Path such as /tickets or /api/v2/groups")
    raw.add_argument("--query", help="Raw URL query string without '?'")
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
