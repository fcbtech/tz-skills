#!/usr/bin/env python3
# pyright: reportAny=false
"""
Outline API Helper Script
Usage: python outline_helper.py <command> [args]

Commands:
  setup                         Prompt for config and save scripts/.env
  list-collections              List all collections
  list-docs [collection_id]     List documents (optionally in a collection)
  search <query>                Search documents
  read <doc_id>                 Read a document
  create <collection_id> <title> <content_file>  Create document from file
  update <doc_id> <content_file>                 Update document from file
  delete <doc_id>               Delete a document
"""

from __future__ import annotations

import json
import getpass
import os
import stat
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import TypeAlias, cast

JsonValue: TypeAlias = str | int | float | bool | None | list["JsonValue"] | dict[str, "JsonValue"]
JsonDict: TypeAlias = dict[str, JsonValue]

SCRIPT_DIR = Path(__file__).resolve().parent
ENV_PATH = SCRIPT_DIR / ".env"
DEFAULT_BASE_URL = "https://outline.letstranzact.com"


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


def normalize_base_url(value: str) -> str:
    return value.strip().rstrip("/")


def load_config() -> tuple[str, str | None]:
    file_values = parse_env_file(ENV_PATH)
    base_url = (
        os.environ.get("OUTLINE_BASE_URL")
        or file_values.get("OUTLINE_BASE_URL")
        or DEFAULT_BASE_URL
    )
    api_token = os.environ.get("OUTLINE_API_TOKEN") or file_values.get("OUTLINE_API_TOKEN")
    return normalize_base_url(base_url), api_token


def save_config(base_url: str, api_token: str) -> None:
    _ = ENV_PATH.write_text(
        "\n".join(
            [
                f'OUTLINE_BASE_URL="{normalize_base_url(base_url)}"',
                f'OUTLINE_API_TOKEN="{api_token.strip()}"',
                "",
            ]
        ),
        encoding="utf-8",
    )
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt_setup() -> None:
    current_base_url, current_token = load_config()
    base_url_prompt = f"Outline base URL [{current_base_url}]: "
    base_url = input(base_url_prompt).strip() or current_base_url

    if current_token:
        use_existing = input("Existing API token found. Keep it? [Y/n]: ").strip().lower()
        if use_existing in {"", "y", "yes"}:
            api_token = current_token
        else:
            api_token = getpass.getpass("Outline API token: ").strip()
    else:
        api_token = getpass.getpass("Outline API token: ").strip()

    if not api_token:
        sys.exit("Outline API token is required.")

    save_config(base_url, api_token)
    print(f"Saved Outline config to {ENV_PATH}")


def require_config() -> tuple[str, str]:
    base_url, api_token = load_config()
    if not api_token:
        sys.exit(
            "\n".join(
                [
                    "Error: OUTLINE_API_TOKEN is required.",
                    "Ask the user for the missing API token, then run:",
                    "  python outline/scripts/outline_helper.py setup",
                ]
            )
        )
    return base_url, api_token


def api_call(endpoint: str, data: JsonDict) -> JsonDict:
    """Make API call to Outline."""
    base_url, api_token = require_config()
    request = urllib.request.Request(
        f"{base_url}/api/{endpoint}",
        data=json.dumps(data).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return cast(JsonDict, json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            return cast(JsonDict, json.loads(body))
        except json.JSONDecodeError:
            return {"ok": False, "status": error.code, "error": body}
    except urllib.error.URLError as error:
        return {"ok": False, "error": str(error.reason)}
    except json.JSONDecodeError:
        return {"ok": False, "error": "Invalid JSON response from Outline"}


def as_dict(value: JsonValue) -> JsonDict:
    """Return a JSON object or an empty object for unexpected shapes."""
    if isinstance(value, dict):
        return cast(JsonDict, value)
    return {}


def as_list(value: JsonValue) -> list[JsonValue]:
    """Return a JSON list or an empty list for unexpected shapes."""
    if isinstance(value, list):
        return cast(list[JsonValue], value)
    return []


def as_text(value: JsonValue) -> str:
    """Render a JSON scalar safely for terminal output."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return json.dumps(value)


def error_message(result: JsonDict) -> str:
    message = as_text(result.get("message"))
    return message or "Unknown error"


def truncate_title(value: JsonValue) -> str:
    title = as_text(value)
    return title[:47] + "..." if len(title) > 50 else title


def list_collections():
    """List all collections."""
    result = api_call("collections.list", {})
    if result.get("ok"):
        print(f"{'Name':<40} {'ID'}")
        print("-" * 80)
        for item in as_list(result.get("data")):
            collection = as_dict(item)
            print(f"{as_text(collection.get('name')):<40} {as_text(collection.get('id'))}")
    else:
        print(f"Error: {error_message(result)}")


def list_documents(collection_id: str | None = None):
    """List documents."""
    data: JsonDict = {"limit": 100}
    if collection_id:
        data["collectionId"] = collection_id

    result = api_call("documents.list", data)
    if result.get("ok"):
        print(f"{'Title':<50} {'ID'}")
        print("-" * 90)
        for item in as_list(result.get("data")):
            doc = as_dict(item)
            print(f"{truncate_title(doc.get('title')):<50} {as_text(doc.get('id'))}")
        pagination = as_dict(result.get("pagination"))
        print(f"\nTotal: {as_text(pagination.get('total'))}")
    else:
        print(f"Error: {error_message(result)}")


def search_documents(query: str):
    """Search documents."""
    result = api_call("documents.search", {"query": query, "limit": 25})
    if result.get("ok"):
        print(f"Search results for '{query}':\n")
        print(f"{'Title':<50} {'ID'}")
        print("-" * 90)
        for item in as_list(result.get("data")):
            doc = as_dict(as_dict(item).get("document"))
            print(f"{truncate_title(doc.get('title')):<50} {as_text(doc.get('id'))}")
    else:
        print(f"Error: {error_message(result)}")


def read_document(doc_id: str):
    """Read a document."""
    result = api_call("documents.info", {"id": doc_id})
    if result.get("ok"):
        doc = as_dict(result.get("data"))
        print(f"Title: {as_text(doc.get('title'))}")
        print(f"Collection: {as_text(doc.get('collectionId'))}")
        print(f"Updated: {as_text(doc.get('updatedAt'))}")
        print("-" * 40)
        print(as_text(doc.get("text")))
    else:
        print(f"Error: {error_message(result)}")


def create_document(collection_id: str, title: str, content_file: str):
    """Create a document from a file."""
    content_path = Path(content_file)
    if not content_path.exists():
        print(f"Error: File not found: {content_file}")
        return

    content = content_path.read_text()
    result = api_call("documents.create", {
        "collectionId": collection_id,
        "title": title,
        "text": content,
        "publish": True
    })

    if result.get("ok"):
        doc = as_dict(result.get("data"))
        print(f"Created document: {as_text(doc.get('id'))}")
        base_url, _api_token = require_config()
        print(f"URL: {base_url}{as_text(doc.get('url'))}")
    else:
        print(f"Error: {error_message(result)}")


def update_document(doc_id: str, content_file: str):
    """Update a document from a file."""
    content_path = Path(content_file)
    if not content_path.exists():
        print(f"Error: File not found: {content_file}")
        return

    content = content_path.read_text()
    result = api_call("documents.update", {
        "id": doc_id,
        "text": content,
        "publish": True
    })

    if result.get("ok"):
        doc = as_dict(result.get("data"))
        print(f"Updated document: {as_text(doc.get('id'))}")
    else:
        print(f"Error: {error_message(result)}")


def delete_document(doc_id: str):
    """Delete a document."""
    result = api_call("documents.delete", {"id": doc_id})
    if result.get("ok"):
        print(f"Deleted document: {doc_id}")
    else:
        print(f"Error: {error_message(result)}")


def main():
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    command = sys.argv[1]

    if command == "setup":
        prompt_setup()
    elif command == "list-collections":
        list_collections()
    elif command == "list-docs":
        collection_id = sys.argv[2] if len(sys.argv) > 2 else None
        list_documents(collection_id)
    elif command == "search":
        if len(sys.argv) < 3:
            print("Usage: outline_helper.py search <query>")
            sys.exit(1)
        search_documents(sys.argv[2])
    elif command == "read":
        if len(sys.argv) < 3:
            print("Usage: outline_helper.py read <doc_id>")
            sys.exit(1)
        read_document(sys.argv[2])
    elif command == "create":
        if len(sys.argv) < 5:
            print("Usage: outline_helper.py create <collection_id> <title> <content_file>")
            sys.exit(1)
        create_document(sys.argv[2], sys.argv[3], sys.argv[4])
    elif command == "update":
        if len(sys.argv) < 4:
            print("Usage: outline_helper.py update <doc_id> <content_file>")
            sys.exit(1)
        update_document(sys.argv[2], sys.argv[3])
    elif command == "delete":
        if len(sys.argv) < 3:
            print("Usage: outline_helper.py delete <doc_id>")
            sys.exit(1)
        delete_document(sys.argv[2])
    else:
        print(f"Unknown command: {command}")
        print(__doc__)
        sys.exit(1)


if __name__ == "__main__":
    main()
