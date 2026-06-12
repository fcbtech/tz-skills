#!/usr/bin/env python3
"""
recent-sessions-digest.py

Scans Claude Code conversation transcripts at ~/.claude/projects/*/*.jsonl
that were modified in the last N hours and prints a short "what was being
worked on" digest. With --notify, posts the digest to Slack via the local
slack skill helper (~/.claude/skills/slack/scripts/slack_helper.py).

Usage:
    recent-sessions-digest.py                              # last 2h, preview
    recent-sessions-digest.py --hours 4                    # last 4h
    recent-sessions-digest.py --notify --channel "#oncall" # post to slack
    recent-sessions-digest.py --notify                     # uses SLACK_DEFAULT_CHANNEL

Exit codes:
    0 OK; 2 nothing to digest; 3 Slack post failed.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SLACK_HELPER_CANDIDATES = [
    Path.home() / ".claude" / "skills" / "slack" / "scripts" / "slack_helper.py",
]
TRUNCATE = 160          # max characters of a user prompt to keep
MAX_SESSIONS_PER_PROJECT = 3
MAX_PROJECTS = 8


def find_recent_transcripts(hours: float) -> list[Path]:
    cutoff = time.time() - hours * 3600
    if not PROJECTS_DIR.exists():
        return []
    out: list[Path] = []
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime >= cutoff:
                out.append(path)
        except OSError:
            continue
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out


def project_label(path: Path) -> str:
    """Decode Claude Code's project directory name back into a readable path."""
    raw = path.parent.name
    label = raw.lstrip("-").replace("-", "/")
    parts = label.split("/")
    if len(parts) > 3:
        label = ".../" + "/".join(parts[-3:])
    return label


def extract_messages(path: Path) -> tuple[str | None, str | None, int]:
    """Return (first_user_message, last_user_message, total_user_messages)."""
    first: str | None = None
    last: str | None = None
    count = 0
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for raw_line in fh:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    obj = json.loads(raw_line)
                except json.JSONDecodeError:
                    continue
                if obj.get("type") != "user":
                    continue
                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                content = msg.get("content")
                text: str | None = None
                if isinstance(content, str):
                    text = content
                elif isinstance(content, list):
                    parts: list[str] = []
                    for block in content:
                        if isinstance(block, dict) and block.get("type") == "text":
                            block_text = block.get("text")
                            if isinstance(block_text, str):
                                parts.append(block_text)
                    if parts:
                        text = "\n".join(parts)
                if not text:
                    continue
                text = text.strip()
                # Skip system-y noise: tool results, sentinels, very-short tokens.
                if text.startswith("<") or text.startswith("[Request") or len(text) < 4:
                    continue
                count += 1
                if first is None:
                    first = text
                last = text
    except OSError:
        pass
    return first, last, count


def truncate(s: str, n: int = TRUNCATE) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def build_digest(hours: float) -> tuple[str, int]:
    transcripts = find_recent_transcripts(hours)
    if not transcripts:
        return ("", 0)

    grouped: dict[str, list[tuple[Path, float]]] = defaultdict(list)
    for path in transcripts:
        grouped[project_label(path)].append((path, path.stat().st_mtime))

    project_order = sorted(grouped.keys(), key=lambda k: max(m for _, m in grouped[k]), reverse=True)
    project_order = project_order[:MAX_PROJECTS]

    lines: list[str] = []
    lines.append(f"*Claude Code sessions — last {hours:g}h*")
    lines.append("")

    total_sessions = 0
    for proj in project_order:
        sessions = sorted(grouped[proj], key=lambda t: t[1], reverse=True)[:MAX_SESSIONS_PER_PROJECT]
        lines.append(f"*{proj}*")
        for path, mtime in sessions:
            first, last, count = extract_messages(path)
            ts = time.strftime("%H:%M", time.localtime(mtime))
            if first:
                lines.append(f"  • {ts} — _{count} msgs_ — {truncate(first)}")
                if last and last != first and count > 2:
                    lines.append(f"      latest: {truncate(last, 120)}")
            else:
                lines.append(f"  • {ts} — _{count} msgs_ — (no readable user prompts)")
            total_sessions += 1
        lines.append("")

    if len(grouped) > MAX_PROJECTS:
        lines.append(f"_…and {len(grouped) - MAX_PROJECTS} more project(s) trimmed for length._")

    return ("\n".join(lines).rstrip() + "\n", total_sessions)


def find_slack_helper() -> Path | None:
    for candidate in SLACK_HELPER_CANDIDATES:
        if candidate.is_file():
            return candidate
    return None


def post_to_slack(message: str, channel: str | None, pretty: bool) -> bool:
    helper = find_slack_helper()
    if helper is None:
        sys.stderr.write("Error: slack_helper.py not found at ~/.claude/skills/slack/scripts/.\n")
        sys.stderr.write("Install the slack skill first (see tz-skills README).\n")
        return False
    cmd = [sys.executable, str(helper)]
    if pretty:
        cmd.append("--pretty")
    cmd.append("post")
    if channel:
        cmd.extend(["--channel", channel])
    result = subprocess.run(cmd, input=message, capture_output=True, text=True, check=False)
    sys.stdout.write(result.stdout)
    if result.stderr:
        sys.stderr.write(result.stderr)
    return result.returncode == 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Digest of recent Claude Code sessions")
    parser.add_argument("--hours", type=float, default=2.0, help="Look back this many hours (default 2)")
    parser.add_argument("--notify", action="store_true", help="POST the digest to Slack (default: preview only)")
    parser.add_argument("--channel", help="Slack channel; falls back to SLACK_DEFAULT_CHANNEL")
    parser.add_argument("--pretty", action="store_true", help="Pretty-print the Slack API response")
    parser.add_argument("--quiet-if-empty", action="store_true", help="Exit silently with code 2 if no sessions")
    args = parser.parse_args()

    digest, n_sessions = build_digest(args.hours)
    if n_sessions == 0:
        if args.quiet_if_empty:
            return 2
        print(f"No Claude Code sessions touched in the last {args.hours:g}h.")
        return 2

    print("=" * 60)
    print(digest)
    print("=" * 60)
    print(f"({n_sessions} session(s) summarized)")

    if not args.notify:
        print("(no --notify: not posting to Slack)")
        return 0

    print("Posting to Slack…")
    return 0 if post_to_slack(digest, args.channel, args.pretty) else 3


if __name__ == "__main__":
    raise SystemExit(main())
