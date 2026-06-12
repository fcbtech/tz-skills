#!/usr/bin/env python3
# pyright: reportAny=false
# pyright: reportUnusedCallResult=false
"""
Slack helper. Posts messages via either an incoming webhook OR the Web API.

Auth auto-detection (no flags needed):
- If SLACK_BOT_TOKEN is set, use the Web API (richer: threading, mentions,
  channel lookup, user lookup, file upload).
- Else if SLACK_WEBHOOK_URL is set, use the incoming webhook (simpler:
  single channel, plain text + blocks only).

Configuration is read from environment variables first, then from .env beside
this script. If values are missing, run:

  python slack_helper.py setup
"""

from __future__ import annotations

import argparse
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
WEB_API_BASE = "https://slack.com/api"


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


def load_config() -> tuple[str | None, str | None, str | None]:
    """Returns (bot_token, webhook_url, default_channel)."""
    file_values = parse_env_file(ENV_PATH)
    bot_token = os.environ.get("SLACK_BOT_TOKEN") or file_values.get("SLACK_BOT_TOKEN")
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL") or file_values.get("SLACK_WEBHOOK_URL")
    default_channel = os.environ.get("SLACK_DEFAULT_CHANNEL") or file_values.get("SLACK_DEFAULT_CHANNEL")
    return (bot_token or None, webhook_url or None, default_channel or None)


def save_config(bot_token: str | None, webhook_url: str | None, default_channel: str | None) -> None:
    lines: list[str] = []
    if bot_token:
        lines.append(f'SLACK_BOT_TOKEN="{bot_token.strip()}"')
    if webhook_url:
        lines.append(f'SLACK_WEBHOOK_URL="{webhook_url.strip()}"')
    if default_channel:
        lines.append(f'SLACK_DEFAULT_CHANNEL="{default_channel.strip()}"')
    ENV_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    ENV_PATH.chmod(stat.S_IRUSR | stat.S_IWUSR)


def prompt_setup() -> None:
    current_bot, current_webhook, current_channel = load_config()

    print("Slack helper supports two auth modes. Configure one or both.")
    print("- Bot token (xoxb-...): richer features (threading, mentions, channels).")
    print("- Webhook URL: simplest, posts to a single channel only.")
    print("Press Enter to skip a field.\n")

    bot_prompt = "Slack bot token"
    if current_bot:
        bot_prompt += " [keep existing]"
    bot_prompt += ": "
    bot_token = getpass.getpass(bot_prompt).strip() or current_bot

    webhook_prompt = "Slack incoming webhook URL"
    if current_webhook:
        webhook_prompt += " [keep existing]"
    webhook_prompt += ": "
    webhook_url = input(webhook_prompt).strip() or current_webhook

    channel_prompt = "Default channel (e.g. #oncall)"
    if current_channel:
        channel_prompt += f" [{current_channel}]"
    channel_prompt += ": "
    default_channel = input(channel_prompt).strip() or current_channel

    if not bot_token and not webhook_url:
        sys.exit("Provide at least a bot token OR a webhook URL.")

    save_config(bot_token, webhook_url, default_channel)
    modes = [m for m, v in [("bot", bot_token), ("webhook", webhook_url)] if v]
    print(f"Saved Slack config ({', '.join(modes)} mode) to {ENV_PATH}")


def require_bot_token() -> str:
    bot_token, _, _ = load_config()
    if not bot_token:
        sys.exit(
            "Missing SLACK_BOT_TOKEN. This action needs the Web API.\n"
            "Set it in env or run: python slack/scripts/slack_helper.py setup"
        )
    return bot_token


def require_webhook_url() -> str:
    _, webhook_url, _ = load_config()
    if not webhook_url:
        sys.exit("Missing SLACK_WEBHOOK_URL. Set it in env or run: setup")
    return webhook_url


def resolve_channel(arg_channel: str | None) -> str:
    if arg_channel:
        return arg_channel
    _, _, default_channel = load_config()
    if default_channel:
        return default_channel
    sys.exit("--channel is required (no SLACK_DEFAULT_CHANNEL set).")


def request_json(url: str, body: JsonDict, headers: dict[str, str]) -> JsonValue:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            text = response.read().decode("utf-8")
            if not text:
                return {"ok": True, "status": response.status}
            try:
                return cast(JsonValue, json.loads(text))
            except json.JSONDecodeError:
                return {"ok": True, "status": response.status, "raw": text}
    except urllib.error.HTTPError as error:
        body_text = error.read().decode("utf-8", errors="replace")
        return {"ok": False, "status": error.code, "error": body_text}
    except urllib.error.URLError as error:
        return {"ok": False, "error": str(error.reason)}


def call_web_api(method: str, body: JsonDict) -> JsonValue:
    token = require_bot_token()
    return request_json(
        f"{WEB_API_BASE}/{method}",
        body,
        {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )


def call_webhook(body: JsonDict) -> JsonValue:
    return request_json(require_webhook_url(), body, {"Content-Type": "application/json"})


def read_text_arg(args: argparse.Namespace) -> str:
    if args.text:
        return args.text
    if args.file:
        return Path(args.file).expanduser().read_text(encoding="utf-8")
    if not sys.stdin.isatty():
        return sys.stdin.read()
    sys.exit("Provide message via --text, --file, or stdin.")


def cmd_setup(_args: argparse.Namespace) -> JsonValue | None:
    prompt_setup()
    return None


def cmd_post_message(args: argparse.Namespace) -> JsonValue:
    """Post via Web API. Requires SLACK_BOT_TOKEN. Supports threading + mentions."""
    body: JsonDict = {
        "channel": resolve_channel(args.channel),
        "text": read_text_arg(args),
    }
    if args.thread_ts:
        body["thread_ts"] = args.thread_ts
    if args.blocks:
        body["blocks"] = json.loads(args.blocks)
    return call_web_api("chat.postMessage", body)


def cmd_post_webhook(args: argparse.Namespace) -> JsonValue:
    """Post via incoming webhook. Simplest path. Channel comes from the webhook URL."""
    body: JsonDict = {"text": read_text_arg(args)}
    if args.blocks:
        body["blocks"] = json.loads(args.blocks)
    return call_webhook(body)


def cmd_post(args: argparse.Namespace) -> JsonValue:
    """Auto-pick the mode based on what's configured. Bot token preferred."""
    bot_token, webhook_url, _ = load_config()
    if bot_token:
        return cmd_post_message(args)
    if webhook_url:
        if args.thread_ts:
            sys.exit("Threading requires SLACK_BOT_TOKEN — webhooks can't reply in a thread.")
        return cmd_post_webhook(args)
    sys.exit("Neither SLACK_BOT_TOKEN nor SLACK_WEBHOOK_URL configured. Run: setup")


def cmd_lookup_user(args: argparse.Namespace) -> JsonValue:
    return call_web_api("users.lookupByEmail", {"email": args.email})


def cmd_lookup_channel(args: argparse.Namespace) -> JsonValue:
    # conversations.list is the only way to resolve a name → id in the Web API.
    token = require_bot_token()
    name = args.name.lstrip("#")
    query = urllib.parse.urlencode({"limit": "1000", "exclude_archived": "true"})
    url = f"{WEB_API_BASE}/conversations.list?{query}"
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            payload = cast(JsonDict, json.loads(response.read().decode("utf-8")))
    except urllib.error.HTTPError as error:
        return {"ok": False, "status": error.code, "error": error.read().decode("utf-8", errors="replace")}
    channels = payload.get("channels")
    if isinstance(channels, list):
        for ch in channels:
            if isinstance(ch, dict) and ch.get("name") == name:
                return cast(JsonValue, ch)
    return {"ok": False, "error": f"channel #{name} not found"}


def cmd_raw(args: argparse.Namespace) -> JsonValue:
    body = json.loads(args.json) if args.json else {}
    return call_web_api(args.method, body)


def print_json(value: JsonValue, pretty: bool) -> None:
    if pretty:
        print(json.dumps(value, indent=2, sort_keys=True))
    else:
        print(json.dumps(value, separators=(",", ":")))


def add_message_args(p: argparse.ArgumentParser, *, channel: bool = True) -> None:
    if channel:
        p.add_argument("--channel", help="Channel id or name (e.g. #oncall). Falls back to SLACK_DEFAULT_CHANNEL.")
    group = p.add_mutually_exclusive_group()
    group.add_argument("--text", help="Message text")
    group.add_argument("--file", help="Read message text from a file")
    p.add_argument("--blocks", help="Slack Block Kit JSON (array)")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Slack helper (webhook OR Web API)")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print JSON output")
    subparsers = parser.add_subparsers(dest="command", required=True)

    setup = subparsers.add_parser("setup", help="Prompt for token/webhook/default channel; save to .env")
    setup.set_defaults(func=cmd_setup)

    post = subparsers.add_parser("post", help="Post a message; auto-picks Web API or webhook")
    add_message_args(post)
    post.add_argument("--thread-ts", help="Reply in thread (Web API only)")
    post.set_defaults(func=cmd_post)

    post_message = subparsers.add_parser("post-message", help="Force Web API (chat.postMessage)")
    add_message_args(post_message)
    post_message.add_argument("--thread-ts", help="Reply in thread")
    post_message.set_defaults(func=cmd_post_message)

    post_webhook = subparsers.add_parser("post-webhook", help="Force incoming webhook")
    add_message_args(post_webhook, channel=False)
    post_webhook.set_defaults(func=cmd_post_webhook)

    lookup_user = subparsers.add_parser("lookup-user", help="Look up a Slack user by email")
    lookup_user.add_argument("email")
    lookup_user.set_defaults(func=cmd_lookup_user)

    lookup_channel = subparsers.add_parser("lookup-channel", help="Resolve a channel name to its id")
    lookup_channel.add_argument("name", help="Channel name (with or without leading #)")
    lookup_channel.set_defaults(func=cmd_lookup_channel)

    raw = subparsers.add_parser("raw", help="Call any Slack Web API method with a JSON body")
    raw.add_argument("method", help="API method, e.g. chat.postMessage")
    raw.add_argument("--json", help="JSON object request body")
    raw.set_defaults(func=cmd_raw)

    return parser


def main() -> int:
    args = build_parser().parse_args()
    try:
        result = args.func(args)
    except ValueError as error:
        raise SystemExit(str(error))
    if result is not None:
        print_json(cast(JsonValue, result), args.pretty)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
