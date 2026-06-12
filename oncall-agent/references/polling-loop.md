# Polling loop — design (future work)

This document describes the **autonomous polling agent** that will sit on top of the `oncall-agent` skill. It is **not implemented in this PR**; it lands in a follow-up. The doc exists so the architecture is captured before code is written.

## Goal

An agent that:

1. **Polls Freshdesk** every N minutes for new tickets raised by the CX team into a tech-routing group / tag.
2. **Filters to in-scope tickets** (correct group, tags, status; skips bot-replies, internal noise, already-investigated tickets).
3. **For each new in-scope ticket**: composes a `# Freshdesk Ticket Context` block, invokes Claude headlessly with the `oncall-agent` skill loaded, captures the diagnosis.
4. **Notifies a developer** via Slack (`oncall-agent/scripts/notify-investigation.sh --notify`) with the diagnosis, the ticket link, and the suggested next action.
5. **Optionally raises a draft PR** in the affected repo with a proposed fix — gated behind explicit per-ticket approval (the PR-raising capability is its own follow-up; see "Autonomous PR raising" below).

## Why this isn't a skill

Skills are **reactive** — they activate when a human sends a prompt. Autonomous polling is a **scheduler-driven** process: a cron / launchd / systemd job that wakes a fresh Claude session at a cadence, feeds it ticket context, and dispatches notifications.

The polling loop's natural home is a **Python daemon**, not a skill. It composes the same `# Freshdesk Ticket Context` block a human would paste, then invokes either `claude --print` (Claude Code in non-interactive mode) or the Claude API directly. The skill itself stays unchanged.

## Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  Scheduler (launchd / systemd / cron)                                  │
│      ↓ every N min                                                     │
│  scripts/poll.py                                                       │
│      1. fetch new tickets via `freshdesk` skill helper                 │
│      2. filter by group_id / tags / status / age                       │
│      3. dedupe against state file                                      │
│      4. for each fresh ticket:                                         │
│           - compose Freshdesk Ticket Context block                     │
│           - invoke Claude headlessly with oncall-agent loaded          │
│           - capture diagnosis                                          │
│           - call notify-investigation.sh --notify --channel ...        │
│      5. update state file                                              │
└────────────────────────────────────────────────────────────────────────┘
```

## Where it lives

Option A (recommended): a new skill `oncall-poller/` next to `oncall-agent/`. Houses `scripts/poll.py`, `scripts/install-launchd.sh`, `references/launchd-plist.template.xml`. Keeps it discoverable in this repo.

Option B: a `tz-micros/oncall-poller/` Cloud Function. Better if the polling needs to run when no developer machine is up, but adds deployment surface.

V1 should be A. Promote to B if the team wants 24/7 coverage.

## State and dedupe

A small JSON file at `~/.tz-oncall/poller-state.json`:

```json
{
  "last_poll_at": "2026-06-12T08:00:00Z",
  "investigated": {
    "17268": {
      "investigated_at": "2026-06-12T07:55:00Z",
      "diagnosis_summary": "stuck IAP — publish race",
      "slack_thread_ts": "1700000000.000100"
    }
  }
}
```

Skip any ticket whose id is already in `investigated`. The first key under `investigated` lets follow-up runs reply in the same Slack thread instead of starting a new one.

## Filter (which tickets are in scope)

Configurable in `~/.tz-oncall/poller-config.json`. Suggested defaults:

```json
{
  "group_id": <freshdesk-tech-group-id>,
  "status_in": [2, 3],
  "tags_any": ["bug", "production", "tech"],
  "created_within_hours": 24,
  "max_per_poll": 5
}
```

Hard cap on `max_per_poll` to avoid the rare "100 new tickets dropped in the last 10 min" scenario from spawning 100 Claude sessions.

## Headless Claude invocation

```bash
claude --print --output-format text \
    --append-system-prompt "Use only oncall-agent and its siblings." \
    < composed_ticket_context.md
```

The composed file is the same `# Freshdesk Ticket Context` block a human would paste. Claude Code activates `oncall-agent` automatically. The session ends after the diagnosis is emitted.

Capture stdout into a temp file → pass to `notify-investigation.sh --file <path> --ticket-id <id> --notify`.

## Failure modes to handle

| Failure | Response |
|---------|----------|
| Freshdesk returns 429 | Honor `Retry-After`; skip this poll cycle |
| Claude session errors (timeout, OOM) | Log; mark ticket as `investigation_failed` with a counter; retry up to 2× then alert ops |
| Slack post fails | Log, fall back to `--file <local-log>` — don't lose the diagnosis |
| State file corrupted | Rebuild from `investigated` keys in the last 24h of Slack history (only if bot mode + channel history scope) |
| Disk full | Hard exit; supervisor restart |

## Autonomous PR raising (separate follow-up)

This deserves its own scoped design and review. Key constraints when it lands:

- **Draft PRs only.** Never auto-merge.
- **Bounded scope.** One file per PR by default; explicit override needed for multi-file fixes.
- **Mandatory dev gate.** PR body says *"This PR was auto-raised by the on-call agent based on Freshdesk ticket FD#XXXXX. Please review before marking ready-for-review."*
- **Per-repo opt-in.** A repo allowlist in `~/.tz-oncall/pr-allowlist.json`. Initial allowlist is empty — devs add `tranzact-v2`, `tz-vue-3`, etc. one at a time after manual review of the agent's first few proposed PRs.
- **Slack hand-off.** The notification names the PR url and asks "merge / request changes / discard?" so the dev can ack in-thread.
- **Revertable.** Each PR's commit message embeds the ticket id and a "to revert: `git revert <sha>`" line.

The PR-raising capability is opt-in per-repo, off-by-default, draft-only. It's the riskiest piece of the whole agent and should land last.

## Observability

- **Structured logs** to `~/.tz-oncall/poller.log` (rotated daily). Fields: `poll_id`, `ticket_id`, `duration_ms`, `result`, `slack_thread_ts`.
- **Optional NR custom event** via `newrelic events post` so the polling agent is itself observable in NR.

## Out of scope (explicitly)

- Two-way Slack: the agent posts; humans reply. The agent does NOT listen for replies. (That needs a Slack Events API endpoint, which is a separate service.)
- Multi-tenant: this agent runs as one operator's identity. If multiple devs want their own polling instances, they each run their own daemon with their own state file.
- Cross-channel routing by ticket type: v1 posts everything to one channel. Conditional routing by `cf_entity_type` or `priority` is a v2 feature.

## Open questions for the follow-up PR

- Which scheduler? `launchd` (mac-only, easy) vs `systemd` (linux servers, harder mac) vs both. Start with launchd if v1 ships locally.
- Slack mode? Webhook (simpler, no threading) vs bot token (richer, threading). Threading wins if the team wants follow-ups to thread under the original notification.
- Polling cadence? 5 min feels right for tech tickets; 1 min is overkill; 15 min loses urgency. Make it config-driven.
