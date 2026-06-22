#!/usr/bin/env python3
"""
claude-digest.py

Incremental, Claude-summarized work log from your Claude Code sessions.

modes:
  recent  → covers ONLY the last N hours (per-message timestamp window, so a
            long-running session contributes only its newly-added messages).
            Emits tight 1-2 line bullets of what was *achieved*, each tagged
            with repo / branch / PR. Each run is appended to a daily history
            store so the scrum can aggregate it later.
  scrum   → weekday standup. Aggregates the history entries logged across
            "yesterday + today so far" (Mon reaches back to Fri) into a
            standup. Falls back to re-reading transcripts if the store is empty.

Input to the summarizer = your prompts + Claude's responses (insight blocks and
other meaningful text), all secret-redacted before anything leaves the machine.

Usage:
    claude-digest.py --mode recent --hours 2  --notify
    claude-digest.py --mode scrum             --notify
    claude-digest.py --mode recent --hours 2  --dry-run     # show the prompt only

Exit codes: 0 OK; 2 nothing to report; 3 Slack post failed; 4 Claude call failed.
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

PROJECTS_DIR = Path.home() / ".claude" / "projects"
HISTORY_DIR = Path.home() / ".tz-oncall" / "digest-history"
SLACK_HELPER_CANDIDATES = [Path.home() / ".claude" / "skills" / "slack" / "scripts" / "slack_helper.py"]
CLAUDE_BIN_CANDIDATES = [
    Path.home() / ".local" / "bin" / "claude",
    Path("/opt/homebrew/bin/claude"),
    Path("/usr/local/bin/claude"),
]

USER_MSG_TRUNCATE = 240
ASST_SNIPPET_TRUNCATE = 500
MAX_USER_MSGS = 25
MAX_ASST_SNIPPETS = 15
MAX_SESSIONS = 20
CLAUDE_TIMEOUT_SECONDS = 180

# Only count high-confidence PR refs: /pull/NNNN URLs or an explicit "PR #NNNN".
# A bare "#33" is too ambiguous (often a board/issue ticket) to treat as a PR.
PR_RE = re.compile(r"(?:/pull/(\d{2,6})\b|\bPR\s*#?(\d{2,6})\b|\bpull request\s*#?(\d{2,6})\b)", re.IGNORECASE)
INSIGHT_RE = re.compile(r"★\s*Insight.*?─{5,}.*?\n(.*?)\n[^\n]*─{5,}", re.DOTALL)

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


def redact(text: str) -> str:
    for pattern, repl in _SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def truncate(s: str, n: int) -> str:
    s = " ".join(s.split())
    return s if len(s) <= n else s[: n - 1] + "…"


def parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        return datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None


def repo_from_cwd(cwd: str | None, project_dir: str) -> str:
    if cwd:
        return Path(cwd).name
    label = project_dir.lstrip("-").replace("-", "/")
    return label.split("/")[-1] if label else project_dir


def text_from_content(content: object) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text" and isinstance(b.get("text"), str)
        ]
        return "\n".join(parts)
    return ""


# ---------------------------------------------------------------- windowed extraction


class SessionActivity:
    def __init__(self, repo: str) -> None:
        self.repo = repo
        self.branch: str | None = None
        self.prs: set[str] = set()
        self.user_msgs: list[str] = []
        self.asst_snippets: list[str] = []

    def has_content(self) -> bool:
        return bool(self.user_msgs or self.asst_snippets)


def extract_window(path: Path, since: datetime) -> SessionActivity | None:
    """Collect only the messages whose timestamp is >= since (incremental)."""
    act = SessionActivity(repo_from_cwd(None, path.parent.name))
    saw = False
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
                typ = obj.get("type")
                if typ not in ("user", "assistant"):
                    continue
                ts = parse_ts(obj.get("timestamp"))
                if ts is None or ts < since:
                    continue
                saw = True

                if obj.get("cwd"):
                    act.repo = repo_from_cwd(obj["cwd"], path.parent.name)
                gb = obj.get("gitBranch")
                if gb and gb not in ("HEAD", ""):
                    act.branch = gb

                msg = obj.get("message")
                if not isinstance(msg, dict):
                    continue
                text = text_from_content(msg.get("content")).strip()
                if not text:
                    continue

                for m in PR_RE.finditer(text):
                    act.prs.add(next(g for g in m.groups() if g))

                if typ == "user":
                    if text.startswith("<") or text.startswith("[Request") or text.startswith("Caveat:"):
                        continue
                    if len(text) < 4:
                        continue
                    if len(act.user_msgs) < MAX_USER_MSGS:
                        act.user_msgs.append(truncate(redact(text), USER_MSG_TRUNCATE))
                else:  # assistant — keep insight blocks AND other meaningful lead text
                    insight_m = INSIGHT_RE.search(text)
                    snippet = ""
                    if insight_m:
                        snippet = "[insight] " + insight_m.group(1).strip()
                    lead = truncate(text, 350)
                    if lead and (not snippet or lead[:40] not in snippet):
                        snippet = (snippet + " | " if snippet else "") + lead
                    snippet = truncate(redact(snippet), ASST_SNIPPET_TRUNCATE)
                    if snippet and len(act.asst_snippets) < MAX_ASST_SNIPPETS:
                        act.asst_snippets.append(snippet)
    except OSError:
        return None
    if not saw or not act.has_content():
        return None
    return act


def collect_activity(since: datetime) -> list[SessionActivity]:
    if not PROJECTS_DIR.exists():
        return []
    since_epoch = since.timestamp()
    candidates: list[Path] = []
    for path in PROJECTS_DIR.glob("*/*.jsonl"):
        try:
            if path.stat().st_mtime >= since_epoch:
                candidates.append(path)
        except OSError:
            continue
    candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    out: list[SessionActivity] = []
    for path in candidates[:MAX_SESSIONS]:
        act = extract_window(path, since)
        if act:
            out.append(act)
    return out


def render_activity_context(acts: list[SessionActivity]) -> str:
    blocks: list[str] = []
    for a in acts:
        tags = [f"repo={a.repo}"]
        if a.branch:
            tags.append(f"branch={a.branch}")
        if a.prs:
            tags.append("PRs=" + ",".join("#" + p for p in sorted(a.prs)))
        head = "## session (" + ", ".join(tags) + ")"
        lines = [head]
        for m in a.user_msgs:
            lines.append(f"  me> {m}")
        for s in a.asst_snippets:
            lines.append(f"  claude> {s}")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


# ---------------------------------------------------------------- prompts

RECENT_PROMPT = """\
You are writing a TIGHT incremental work-log covering ONLY the last {window_label}. \
Below is my activity in that window — my prompts ("me>") and Claude's responses ("claude>", \
including insight blocks and meaningful conclusions) — grouped by session and tagged with repo, branch, PRs.

Produce 1-4 work items. For each, judge what was ACHIEVED or moved forward from the responses \
(not just what I asked). Merge work on the same repo/PR into one item. Ignore setup/debugging \
chatter that produced no outcome.

Output ONLY a JSON object, no markdown fence, of this exact shape:
{{"items": [
  {{"summary": "<ONE crisp sentence, MAX 25 words — the outcome, not the steps>",
    "repo": "<repo name or empty>",
    "branch": "<branch or empty; omit if it is HEAD>",
    "pr": "<PR number only, e.g. 5605, or empty>"}}
]}}

Hard rules for summary: max 25 words, one sentence, no semicolon-chained lists, lead with the verb \
(e.g. "Fixed…", "Shipped…", "Reviewed…"). If multiple things happened on one repo, keep only the most significant.
If genuinely nothing was accomplished, output {{"items": []}}.

--- ACTIVITY (last {window_label}) ---
{context}
"""

SCRUM_PROMPT = """\
You are writing my daily standup by synthesizing my own earlier work-log entries (below), \
each tagged with the time window it covered. Merge duplicates and collapse progressive work \
on the same item into its latest state. Name the repo and PR inline in each line where known.

Output ONLY a JSON object, no markdown fence, of this exact shape (use empty arrays for empty sections):
{{"done": ["<completed / shipped>", ...],
  "in_progress": ["<still open / being worked on>", ...],
  "blockers": ["<only if clearly blocked>", ...]}}

First person, concise, concrete.

--- MY LOGGED UPDATES ({covers_label}) ---
{context}
"""


# ---------------------------------------------------------------- json + block kit


def parse_json_lenient(text: str) -> dict | None:
    """Parse a JSON object from model output, tolerating ```json fences / prose."""
    t = text.strip()
    if t.startswith("```"):
        t = re.sub(r"^```[a-zA-Z]*\n?", "", t)
        t = re.sub(r"\n?```$", "", t).strip()
    try:
        obj = json.loads(t)
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, re.DOTALL)  # first {...} span
    if m:
        try:
            obj = json.loads(m.group(0))
            return obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            return None
    return None


def _meta_line(repo: str, branch: str, pr: str) -> str | None:
    chips: list[str] = []
    if repo:
        chips.append(f":package: `{repo}`")
    if branch and branch.upper() != "HEAD":
        chips.append(f":herb: `{branch}`")
    if pr:
        chips.append(f":twisted_rightwards_arrows: PR #{str(pr).lstrip('#')}")
    return "   ·   ".join(chips) if chips else None


def header_block(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text[:150], "emoji": True}}


def section_block(mrkdwn: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": mrkdwn}}


def context_block(mrkdwn: str) -> dict:
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": mrkdwn}]}


def build_recent_blocks(items: list[dict], window_label: str) -> tuple[list[dict], str]:
    blocks: list[dict] = [header_block(f"🤖 Last 2h · {window_label}")]
    fallback_lines: list[str] = []
    for i, it in enumerate(items):
        summary = str(it.get("summary", "")).strip()
        if not summary:
            continue
        if i:
            blocks.append({"type": "divider"})
        blocks.append(section_block(f"• {summary}"))
        meta = _meta_line(str(it.get("repo", "")), str(it.get("branch", "")), str(it.get("pr", "")))
        if meta:
            blocks.append(context_block(meta))
        fallback_lines.append(f"• {summary}" + (f"  ({meta})" if meta else ""))
    return blocks, "\n".join(fallback_lines)


def build_scrum_blocks(data: dict, today_label: str, covers_label: str) -> tuple[list[dict], str]:
    sections = [
        ("✅ *Done*", data.get("done") or []),
        ("🔨 *In progress*", data.get("in_progress") or []),
        ("⚠️ *Blockers*", data.get("blockers") or []),
    ]
    blocks: list[dict] = [header_block(f"📋 Daily scrum · {today_label}")]
    fallback_lines: list[str] = []
    first = True
    for title, lines in sections:
        lines = [str(x).strip() for x in lines if str(x).strip()]
        if not lines:
            continue
        if not first:
            blocks.append({"type": "divider"})
        first = False
        body = title + "\n" + "\n".join(f"• {ln}" for ln in lines)
        blocks.append(section_block(body))
        fallback_lines.append(body)
    blocks.append(context_block(f"covers {covers_label}"))
    return blocks, ("\n\n".join(fallback_lines) or "No tracked activity.")


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
    return result.stdout.strip() or None


def post_to_slack(text_fallback: str, blocks: list[dict] | None, channel: str | None) -> bool:
    helper = find_binary(SLACK_HELPER_CANDIDATES, "slack_helper.py")
    if helper is None:
        sys.stderr.write("Error: slack_helper.py not found. Install the slack skill first.\n")
        return False
    cmd = [sys.executable, str(helper), "post"]
    if channel:
        cmd.extend(["--channel", channel])
    if blocks:
        cmd.extend(["--blocks", json.dumps(blocks)])
    # text is the notification preview / fallback when blocks can't render.
    result = subprocess.run(cmd, input=text_fallback, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        sys.stderr.write(result.stdout + result.stderr)
    return result.returncode == 0


# ---------------------------------------------------------------- history store


HISTORY_RETENTION_DAYS = 7  # scrum only reaches back to Fri (~3d); keep a week, prune the rest.


def history_path(day: datetime) -> Path:
    return HISTORY_DIR / f"{day.strftime('%Y-%m-%d')}.jsonl"


def prune_history(retention_days: int = HISTORY_RETENTION_DAYS) -> None:
    """Delete daily history files older than the retention window."""
    if not HISTORY_DIR.exists():
        return
    cutoff = (datetime.now().astimezone() - timedelta(days=retention_days)).date()
    for p in HISTORY_DIR.glob("*.jsonl"):
        try:
            file_day = datetime.strptime(p.stem, "%Y-%m-%d").date()
        except ValueError:
            continue
        if file_day < cutoff:
            try:
                p.unlink()
            except OSError:
                pass


def append_history(window_label: str, items: list[dict], acts: list[SessionActivity]) -> None:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    try:
        HISTORY_DIR.chmod(0o700)
    except OSError:
        pass
    rec = {
        "ts": datetime.now().astimezone().isoformat(timespec="seconds"),
        "window": window_label,
        "items": [
            {
                "summary": redact(str(it.get("summary", "")).strip()),
                "repo": str(it.get("repo", "")).strip(),
                "branch": str(it.get("branch", "")).strip(),
                "pr": str(it.get("pr", "")).strip(),
            }
            for it in items
            if str(it.get("summary", "")).strip()
        ],
        "repos": sorted({a.repo for a in acts if a.repo}),
    }
    with history_path(datetime.now().astimezone()).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(rec) + "\n")
    prune_history()


def read_history(start_local: datetime, now_local: datetime) -> list[dict]:
    entries: list[dict] = []
    day = start_local.date()
    while day <= now_local.date():
        p = HISTORY_DIR / f"{day.strftime('%Y-%m-%d')}.jsonl"
        if p.is_file():
            for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                ts = parse_ts(rec.get("ts"))
                if ts and ts >= start_local:
                    entries.append(rec)
        day += timedelta(days=1)
    return entries


# ---------------------------------------------------------------- modes


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def hours_label(hours: float) -> str:
    if hours <= 1:
        return "hour"
    if hours < 36:
        return f"{hours:g} hours"
    return f"{hours / 24:g} days"


def do_recent(args: argparse.Namespace) -> int:
    now_l = datetime.now().astimezone()
    since = now_utc() - timedelta(hours=args.hours)
    window_label = f"{(now_l - timedelta(hours=args.hours)).strftime('%H:%M')}–{now_l.strftime('%H:%M')}"

    acts = collect_activity(since)
    if not acts:
        if args.quiet_if_empty:
            return 2
        print(f"No activity in the last {hours_label(args.hours)}.")
        return 2

    prompt = RECENT_PROMPT.format(window_label=hours_label(args.hours), context=render_activity_context(acts))

    if args.dry_run:
        print(prompt)
        print("\n(--dry-run: not calling Claude or Slack)")
        return 0

    raw = run_claude(prompt, args.model)
    if not raw:
        return 4
    data = parse_json_lenient(raw)
    items = (data or {}).get("items") if isinstance(data, dict) else None
    items = [it for it in items if isinstance(it, dict) and str(it.get("summary", "")).strip()] if items else []

    if not items:
        if args.quiet_if_empty:
            return 2
        print("Nothing notable in this window.")
        return 2

    append_history(window_label, items, acts)
    blocks, fallback = build_recent_blocks(items, window_label)
    fallback = redact(f"🤖 Last 2h · {window_label}\n{fallback}")

    print(fallback)
    if not args.notify:
        print("\n(no --notify: not posting to Slack)")
        return 0
    return 0 if post_to_slack(fallback, blocks, args.channel) else 3


def do_scrum(args: argparse.Namespace) -> int:
    now_l = datetime.now().astimezone()
    # "yesterday + today so far"; Monday reaches back to Friday.
    days_back = 3 if now_l.weekday() == 0 else 1
    start_local = (now_l - timedelta(days=days_back)).replace(hour=0, minute=0, second=0, microsecond=0)

    covers_from = start_local.strftime("%a %d %b")
    covers_label = f"{covers_from} → today ({now_l.strftime('%a %d %b')})"

    entries = read_history(start_local, now_l)
    if entries:
        lines: list[str] = []
        for e in entries:
            win = e.get("window", "?")
            for it in e.get("items", []):
                if not isinstance(it, dict):
                    continue
                s = str(it.get("summary", "")).strip()
                if not s:
                    continue
                tags = " ".join(
                    t for t in (it.get("repo", ""), ("PR #" + str(it["pr"]).lstrip("#")) if it.get("pr") else "") if t
                )
                lines.append(f"[{win}] {s}" + (f"  ({tags})" if tags else ""))
        context = "\n".join(lines) if lines else ""
        source = "history"
        if not context:
            entries = []  # nothing usable; drop to fallback below
    if not entries:
        # Fallback: re-window the raw transcripts over the same period.
        acts = collect_activity(start_local.astimezone(timezone.utc))
        if not acts:
            if args.quiet_if_empty:
                return 2
            print(f"No activity to report for {covers_label}.")
            return 2
        context = render_activity_context(acts)
        source = "transcripts (fallback)"

    prompt = SCRUM_PROMPT.format(covers_label=covers_label, context=context)

    if args.dry_run:
        print(f"[scrum source: {source}]")
        print(prompt)
        print("\n(--dry-run: not calling Claude or Slack)")
        return 0

    raw = run_claude(prompt, args.model)
    if not raw:
        return 4
    data = parse_json_lenient(raw) or {}
    today_label = now_l.strftime("%a %d %b")
    covers = f"{covers_from} → today"

    if any((data.get("done"), data.get("in_progress"), data.get("blockers"))):
        blocks, fallback = build_scrum_blocks(data, today_label, covers)
    else:
        # JSON missing/empty but Claude returned prose — degrade gracefully.
        body = raw.strip() or "No tracked activity."
        blocks = [header_block(f"📋 Daily scrum · {today_label}"), section_block(body), context_block(f"covers {covers}")]
        fallback = f"📋 Daily scrum · {today_label}\n{body}"

    fallback = redact(fallback)
    print(fallback)
    if not args.notify:
        print("\n(no --notify: not posting to Slack)")
        return 0
    return 0 if post_to_slack(fallback, blocks, args.channel) else 3


def main() -> int:
    parser = argparse.ArgumentParser(description="Incremental Claude-summarized session digest")
    parser.add_argument("--mode", choices=["recent", "scrum"], default="recent")
    parser.add_argument("--hours", type=float, default=2.0, help="recent mode: window size (default 2)")
    parser.add_argument("--model", default="claude-sonnet-4-6")
    parser.add_argument("--notify", action="store_true", help="POST to Slack (default: print only)")
    parser.add_argument("--channel", help="Slack channel/user id; falls back to SLACK_DEFAULT_CHANNEL")
    parser.add_argument("--quiet-if-empty", action="store_true", help="Exit 2 silently when there's nothing to report")
    parser.add_argument("--dry-run", action="store_true", help="Show the prompt; do NOT call Claude or Slack")
    args = parser.parse_args()

    return do_scrum(args) if args.mode == "scrum" else do_recent(args)


if __name__ == "__main__":
    raise SystemExit(main())
