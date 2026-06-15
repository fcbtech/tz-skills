#!/usr/bin/env python3
"""
claude-digest.py

Reads your recent Claude Code session activity, asks Claude (headless
`claude --print`) to summarize it, and posts the summary to Slack via the
local slack skill helper.

Two modes:
  --mode recent  → 4-5 bullets of "what am I actively working on" (for the
                   every-2-hours ping).
  --mode scrum   → a standup-formatted update (done / in progress / blockers)
                   for the weekday-morning daily scrum.

Usage:
    claude-digest.py --mode recent --hours 2  --notify
    claude-digest.py --mode scrum  --hours 72 --notify --channel "U0AK4AM68A3"
    claude-digest.py --mode recent --hours 2  --dry-run   # show prompt, don't call Claude/Slack

Exit codes:
    0 OK; 2 nothing to summarize; 3 Slack post failed; 4 Claude call failed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
import time
from collections import defaultdict
from pathlib import Path

# Redact secret-looking strings before any transcript text leaves the machine
# (it gets sent to Claude and may be echoed into Slack). Prefix-anchored so we
# don't over-redact ordinary text.
_SECRET_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (re.compile(r"xox[baprs]-[A-Za-z0-9-]{8,}"), "[REDACTED:slack-token]"),
    (re.compile(r"xapp-[A-Za-z0-9-]{8,}"), "[REDACTED:slack-app-token]"),
    (re.compile(r"xoxe(?:\.xox[pb])?-[A-Za-z0-9-]{8,}"), "[REDACTED:slack-config-token]"),
    (re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"), "[REDACTED:github-token]"),
    (re.compile(r"github_pat_[A-Za-z0-9_]{20,}"), "[REDACTED:github-pat]"),
    (re.compile(r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}"), "[REDACTED:api-key]"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "[REDACTED:aws-key]"),
    (re.compile(r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"), "[REDACTED:jwt]"),
]


def redact_secrets(text: str) -> str:
    for pattern, replacement in _SECRET_PATTERNS:
        text = pattern.sub(replacement, text)
    return text

PROJECTS_DIR = Path.home() / ".claude" / "projects"
SLACK_HELPER_CANDIDATES = [
    Path.home() / ".claude" / "skills" / "slack" / "scripts" / "slack_helper.py",
]
CLAUDE_BIN_CANDIDATES = [
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]

# Context-shaping caps (keep the prompt bounded regardless of how busy you were).
MSG_TRUNCATE = 220          # max chars per user message
MAX_MSGS_PER_SESSION = 20
MAX_SESSIONS_PER_PROJECT = 6
MAX_PROJECTS = 12
CLAUDE_TIMEOUT_SECONDS = 180


# ---------------------------------------------------------------- transcripts


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
    raw = path.parent.name
    label = raw.lstrip("-").replace("-", "/")
    parts = label.split("/")
    if len(parts) > 3:
        label = ".../" + "/".join(parts[-3:])
    return label


def truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def extract_user_messages(path: Path) -> list[str]:
    """All human-authored prompts in a session (best-effort, noise-filtered)."""
    msgs: list[str] = []
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
                    parts = [
                        b.get("text", "")
                        for b in content
                        if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
                    ]
                    if parts:
                        text = "\n".join(parts)
                if not text:
                    continue
                text = text.strip()
                # Skip tool-result wrappers, sentinels, slash-command echoes, tiny tokens.
                if text.startswith("<") or text.startswith("[Request") or text.startswith("Caveat:"):
                    continue
                if len(text) < 4:
                    continue
                msgs.append(truncate(redact_secrets(text), MSG_TRUNCATE))
    except OSError:
        pass
    return msgs


def build_context(hours: float) -> tuple[str, int]:
    """Return (context_text_for_claude, n_sessions_with_content)."""
    transcripts = find_recent_transcripts(hours)
    if not transcripts:
        return ("", 0)

    grouped: dict[str, list[tuple[Path, float]]] = defaultdict(list)
    for path in transcripts:
        grouped[project_label(path)].append((path, path.stat().st_mtime))

    project_order = sorted(grouped.keys(), key=lambda k: max(m for _, m in grouped[k]), reverse=True)
    project_order = project_order[:MAX_PROJECTS]

    blocks: list[str] = []
    n_sessions = 0
    for proj in project_order:
        sessions = sorted(grouped[proj], key=lambda t: t[1], reverse=True)[:MAX_SESSIONS_PER_PROJECT]
        session_blocks: list[str] = []
        for path, mtime in sessions:
            msgs = extract_user_messages(path)
            if not msgs:
                continue
            msgs = msgs[:MAX_MSGS_PER_SESSION]
            when = time.strftime("%a %H:%M", time.localtime(mtime))
            bullets = "\n".join(f"    - {m}" for m in msgs)
            session_blocks.append(f"  Session (last active {when}, {len(msgs)} prompts shown):\n{bullets}")
            n_sessions += 1
        if session_blocks:
            blocks.append(f"## Project: {proj}\n" + "\n".join(session_blocks))

    return ("\n\n".join(blocks), n_sessions)


# ---------------------------------------------------------------- prompts

RECENT_PROMPT = """\
Below are the prompts I gave Claude Code across my coding sessions in the last {hours_label}, grouped by project. \
In 4-5 concise bullet points, tell me what I am actively working on right now. \
Be concrete: name PRs (#NNNN), files, features, and tickets where they appear. \
Group related work into a single bullet. Ignore environment/setup chatter and one-off lookups. \
Output ONLY the bullets in Slack mrkdwn (lead each with "• "). No preamble, no closing line.

--- MY RECENT SESSION ACTIVITY ---
{context}
"""

SCRUM_PROMPT = """\
You are writing my daily scrum / standup update from my Claude Code activity. \
Below are the prompts I gave Claude Code over roughly the last {hours_label}, grouped by project. \
Write a concise standup update I can paste into my team channel, in first person, using this exact Slack mrkdwn structure:

*:white_check_mark: Done / shipped:*
• <completed work — name PRs merged, features finished, tickets closed>

*:hammer_and_wrench: In progress:*
• <what is still open / being worked on>

*:warning: Blockers:*
• <anything stuck — OMIT this whole section if there are none>

Focus on the most recent day of progress; use older activity only as background so you don't repeat yesterday's standup. \
Be concrete (PRs, files, tickets). No preamble before the first heading, no closing line.

--- MY SESSION ACTIVITY ---
{context}
"""


def hours_label(hours: float) -> str:
    if hours <= 1:
        return "hour"
    if hours < 36:
        return f"{hours:g} hours"
    return f"{hours / 24:g} days"


def build_prompt(mode: str, hours: float, context: str) -> str:
    template = RECENT_PROMPT if mode == "recent" else SCRUM_PROMPT
    return template.format(hours_label=hours_label(hours), context=context)


# ---------------------------------------------------------------- claude + slack


def find_binary(candidates: list[Path], name: str) -> Path | None:
    for c in candidates:
        if c.is_file():
            return c
    which = shutil.which(name)
    return Path(which) if which else None


def run_claude(prompt: str, model: str) -> str | None:
    claude = find_binary(CLAUDE_BIN_CANDIDATES, "claude")
    if claude is None:
        sys.stderr.write("Error: `claude` binary not found.\n")
        return None
    try:
        result = subprocess.run(
            [str(claude), "--print", "--model", model],
            input=prompt,
            capture_output=True,
            text=True,
            timeout=CLAUDE_TIMEOUT_SECONDS,
            check=False,
        )
    except subprocess.TimeoutExpired:
        sys.stderr.write(f"Error: claude timed out after {CLAUDE_TIMEOUT_SECONDS}s.\n")
        return None
    if result.returncode != 0:
        sys.stderr.write(f"Error: claude exited {result.returncode}.\n{result.stderr}\n")
        return None
    out = result.stdout.strip()
    return out or None


def post_to_slack(message: str, channel: str | None) -> bool:
    helper = find_binary(SLACK_HELPER_CANDIDATES, "slack_helper.py")
    if helper is None:
        sys.stderr.write("Error: slack_helper.py not found. Install the slack skill first.\n")
        return False
    cmd = [sys.executable, str(helper), "post"]
    if channel:
        cmd.extend(["--channel", channel])
    result = subprocess.run(cmd, input=message, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout)
        sys.stderr.write(result.stderr)
    return result.returncode == 0


# ---------------------------------------------------------------- main


def header(mode: str, hours: float) -> str:
    if mode == "scrum":
        return f"*:scroll: Daily scrum — last {hours_label(hours)}* ({time.strftime('%a %d %b')})"
    return f"*:robot_face: Working on — last {hours_label(hours)}* ({time.strftime('%H:%M')})"


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude-summarized digest of recent Claude Code sessions")
    parser.add_argument("--mode", choices=["recent", "scrum"], default="recent")
    parser.add_argument("--hours", type=float, default=2.0)
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--notify", action="store_true", help="POST to Slack (default: print only)")
    parser.add_argument("--channel", help="Slack channel/user id; falls back to SLACK_DEFAULT_CHANNEL")
    parser.add_argument("--quiet-if-empty", action="store_true", help="Exit 2 silently if no activity (skips Claude)")
    parser.add_argument("--dry-run", action="store_true", help="Show the prompt; do NOT call Claude or Slack")
    args = parser.parse_args()

    context, n_sessions = build_context(args.hours)
    if n_sessions == 0:
        if args.quiet_if_empty:
            return 2
        print(f"No Claude Code activity in the last {hours_label(args.hours)}.")
        return 2

    prompt = build_prompt(args.mode, args.hours, context)

    if args.dry_run:
        print("=" * 60)
        print(f"MODE={args.mode}  HOURS={args.hours}  MODEL={args.model}  SESSIONS={n_sessions}")
        print("=" * 60)
        print(prompt)
        print("=" * 60)
        print("(--dry-run: not calling Claude or Slack)")
        return 0

    summary = run_claude(prompt, args.model)
    if not summary:
        return 4

    # Defense in depth: redact again in case the model echoed a secret back.
    message = redact_secrets(f"{header(args.mode, args.hours)}\n\n{summary}")
    print(message)

    if not args.notify:
        print("\n(no --notify: not posting to Slack)")
        return 0

    print("\nPosting to Slack…")
    return 0 if post_to_slack(message, args.channel) else 3


if __name__ == "__main__":
    raise SystemExit(main())
