---
name: oncall-agent
description: "On-call investigation orchestrator for TranZact production tickets. Auto-activate when the prompt contains a 'Freshdesk Ticket Context' header, or invoke as /oncall <description-or-ticket-ref>. Composes the freshdesk, newrelic, and mysql skills under a TranZact-specific discipline: mandatory customer ID resolution, polyrepo navigation, transactional dry-run protocol for any mstag-dmz write, constrained output. User-detail lookups (auth_user etc.) go through mstag-dmz so a dev can log in locally as the affected user. Read-only Freshdesk; reads on the prod replica; writes only on mstag-dmz under the dry-run protocol."
---

# oncall-agent — TranZact on-call investigation orchestrator

Disciplined evidence-gathering for production tickets across the `~/Work/tranzact/tranzact-fullstack/` polyrepo. This skill is an **orchestrator** — it provides the gates, the routing, and the output discipline. It does **not** re-implement Freshdesk, New Relic, or MySQL access.

## Required sibling skills

Before activating, confirm these skills are present in the host's skill registry. If any is missing, halt and report which one — do not try to re-implement.

| Sibling | Required | Used for | Reference |
|---------|----------|----------|-----------|
| `freshdesk` | Yes | Ticket + conversation fetch (read-only) | `freshdesk/SKILL.md` |
| `newrelic` | Yes | NRQL queries via `newrelic` CLI | `newrelic/SKILL.md` |
| `mysql` | Yes | Profile-based MySQL access with the table-name guardrail and dry-run write protocol | `mysql/SKILL.md` |
| `slack` | Optional | Posting investigation results / alerting a developer. Only used when explicitly invoked via `scripts/notify-investigation.sh --notify` | `slack/SKILL.md` |

The TZ-specific overlays for each (APM app names, custom-field IDs, host routing) live entirely in this skill's `references/`. Generic siblings stay generic.

## Notifying a developer (optional)

By default, investigations print to stdout — the orchestrator does **not** auto-post to Slack. To deliver the diagnosis into Slack:

```bash
# Preview (no post)
echo "<diagnosis>" | scripts/notify-investigation.sh --ticket-id <id> --channel "#oncall"

# Actually post (explicit opt-in)
echo "<diagnosis>" | scripts/notify-investigation.sh --ticket-id <id> --notify --channel "#oncall"

# Threaded follow-up (Web API only)
echo "<update>" | scripts/notify-investigation.sh --ticket-id <id> --notify --channel "#oncall" --thread-ts <parent-ts>
```

Without `--notify`, the wrapper formats and prints but does not touch Slack — safer for interactive investigations. The autonomous polling loop (see `references/polling-loop.md`, future work) is the primary user of `--notify`.

## When to activate

- **Auto** — the prompt contains the literal header `# Freshdesk Ticket Context`.
- **Manual (free-form)** — the user typed `/oncall <description>`.
- **Manual (ticket ref)** — `/oncall` plus any of: a Freshdesk URL (`https://tranzact.freshdesk.com/a/tickets/17249`), `FD#17249` / `#17249`, or a bare integer when context makes it unambiguously a ticket id.
- **Stay dormant otherwise.** Don't volunteer NRQL or SQL just because you noticed a bug.

## First action on activation

1. **Manual with a ticket ref** — fetch the ticket via the `freshdesk` skill (one call for ticket + custom fields + tags + requester/company; one call for conversations). Then state the hypothesis below using the fetched data.
2. **Auto (pasted `# Freshdesk Ticket Context`)** — if a ticket id is extractable from the pasted block, fetch only `/conversations`. Don't re-fetch the ticket; the pasted block is authoritative.
3. **Manual free-form** — no fetch; go straight to the hypothesis step.

Then, before any NR / DB query: state a one-line **hypothesis** + 1–2 specific **evidence checks** you intend to run. Wait for the user to nod or redirect.

## Customer identification (mandatory before any NR / DB query)

Production tickets only make sense once the affected tenant is pinned. Resolve **TranZact ids** (not Freshdesk ids):

1. **Primary** — Freshdesk sidebar custom fields:
   - `custom_fields.cf_company_id` → TranZact `company_id`
   - `custom_fields.cf_user_id*` (key may have a numeric suffix like `cf_user_id892854` — match by prefix) → TranZact `user_id`
   - Other observed: `cf_approval_id`, `cf_entity_id`, `cf_entity_type`. See `references/tranzact-freshdesk-fields.md`.
2. **If either is missing / null / empty** — stop and ask. Do **not** silently fall back to resolving by requester email; custom fields are agent-curated and emails can land on the wrong tenant.
3. **Never** use the top-level Freshdesk `requester_id` / `company_id` (Freshdesk-internal ids) as MySQL or NRQL filters — they return zero rows and look like a real "no data" answer.

Use `scripts/fetch-ticket-context.sh <ticket-id>` to extract the resolved ids in one shot.

Announce the resolution before running anything else:

> Resolved from FD#17268 sidebar: `tz_company_id=721303`, `tz_user_id=633444` (AGNEE INNOVATES PRIVATE LIMITED).

Email-based resolution from `references/identifier-recipes.md` is a **user-confirmed fallback**, not a default.

## Investigation discipline

- **Defer to the ticket's `Investigation Contract`** if present. Apply silently — do NOT restate its rules in your output.
- **Default if no contract**: see `references/investigation-contract.md` — ≤3-sentence diagnosis, ≤400 words total, uncertainty markers (`[unverified]` / `[inferred from X]`), stop after diagnosis + one next-action.
- **Never propose code fixes** unless explicitly asked.
- **Schema authority**: read model definitions from `~/Work/tranzact/tranzact-fullstack/tz-core/` first, then the originating repo (e.g. `tranzact-v2/`). Cite the model inline: "schema from `tz-core/...models.py:Class`."
- **Host routing**: see `references/host-routing.md`. Business state → `tz-prod-read-replica`. User details + writes → `mstag-dmz`.
- **Product behaviour authority**: `~/Work/tranzact/tz-documentation/content/`. Check before concluding "this is a bug."
- **Repo-map authority**: ticket's embedded `Repo Map` if present; otherwise `references/repo-map.md`.

## Hard refusals

| Action | Response |
|--------|----------|
| DML on `tz-prod-read-replica` | Refuse. No override. |
| Any write on a profile that is not `mstag-dmz` | Refuse — the `mysql` skill also refuses this at the helper layer. |
| DDL on any host via this skill | Refuse — DDL implicitly commits in MySQL. |
| Read with no `WHERE` | Refuse, ask for filter. |
| `SELECT *` on `audit_logs` / `documents_*` / `transaction_*` | Refuse, ask for column list. |
| Skipping the dry-run | Refuse, restate the write protocol in `mysql/references/write-protocol.md`. |
| Activated, no ticket context, no `/oncall` | Stay dormant. |
| `POST`/`PUT`/`PATCH`/`DELETE` on Freshdesk | Refuse — Freshdesk access is read-only. |
| Bulk Freshdesk list with no filter | Refuse, ask for a filter. Same spirit as the SQL "no `WHERE`" rule. |
| Echoing `FRESHDESK_API_KEY`, `Authorization` headers, or `~/.tz-oncall/*.cnf` contents | Refuse. Use env-var placeholders in any command shown to the user. |

## Output shape (default — when no Investigation Contract overrides)

```
**Diagnosis**: <≤3 sentences>

Evidence:
- NR:    <NRQL one-liner + key result>
- DB:    <SQL + interpretation; cite tz-core model>
- Code:  <repo path + file:line if behaviour traced>
- Docs:  <tz-documentation/content/... reference if relevant>

Next support action: <one sentence>

Notes (only if non-obvious): ...
```

## Bootstrap requirement

Before the first query, verify:

- `freshdesk` skill: `~/.tz-oncall/freshdesk.env` exists (mode 600) OR the `freshdesk/scripts/.env` file is configured. Required for ticket fetching.
- `newrelic` skill: `newrelic` CLI on PATH; `~/.newrelic/credentials.json` exists.
- `mysql` skill: at least `~/.tz-oncall/tz-prod-read-replica.cnf` (or `replica.cnf`) and `~/.tz-oncall/mstag-dmz.cnf` exist (mode 600). Without `mstag-dmz.cnf`, refuse user-detail queries and writes rather than silently falling back to the replica.

If any are missing, point the user at the relevant skill's first-run setup and stop — do not attempt a query.

## Composite scripts (ready to run)

| Script | Use case | Calls |
|--------|----------|-------|
| `scripts/fetch-ticket-context.sh <ticket-id>` | Extract resolved customer ids from a Freshdesk ticket (cf_company_id, cf_user_id*, any cf_*) | `freshdesk/scripts/freshdesk_helper.py get-ticket` |
| `scripts/user-impersonation-lookup.sh --user-id <id>` or `--email <email>` | Build the "log in as this user locally" block from mstag-dmz auth_user + profile + company | `mysql/scripts/mysql_helper.py run` against `mstag-dmz` |
| `scripts/customer-state-snapshot.sh --company-id <id>` | First-60-seconds baseline: company row, owner, user count, recent-activity counters | `mysql/scripts/mysql_helper.py run` against `tz-prod-read-replica` |
| `scripts/notify-investigation.sh --ticket-id <id> --notify --channel <name>` | Format and POST an investigation result to Slack (opt-in via `--notify`) | `slack/scripts/slack_helper.py post` |

These call out to the sibling skills' helpers so the table-name guardrail and credential management stay in one place.

## References (read on demand)

- [`references/repo-map.md`](references/repo-map.md) — symptom → primary repo routing.
- [`references/identifier-recipes.md`](references/identifier-recipes.md) — email / name / GST / mobile / document number → TranZact ids (fallback path).
- [`references/host-routing.md`](references/host-routing.md) — which mysql profile for which question.
- [`references/tranzact-apm-and-nrql.md`](references/tranzact-apm-and-nrql.md) — TZ APM app names, NRQL caveats (entity.name, extra.request_id, extra.user_id).
- [`references/tranzact-freshdesk-fields.md`](references/tranzact-freshdesk-fields.md) — custom-field IDs (cf_company_id, cf_user_id*, cf_approval_id), ticket-context parsing.
- [`references/domain-constants.md`](references/domain-constants.md) — TZ domain codes that show up across investigations.
- [`references/investigation-contract.md`](references/investigation-contract.md) — default output discipline.
- [`references/sql/`](references/sql/) — parameterized, broadly-applicable SQL templates referenced by the composite scripts.
- [`references/polling-loop.md`](references/polling-loop.md) — design for the future autonomous polling agent that consumes this skill (Freshdesk → diagnose → Slack notify → optional draft PR). Not yet implemented.
