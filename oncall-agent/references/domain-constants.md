# TranZact domain constants

Codes and conventions that show up across investigations and aren't obvious from the schema. Update as new ones are encountered.

## Document type codes

Observed in `inventory_inventorydocumentapproval.document_type`:

| Code | Meaning |
|------|---------|
| 70 | Process FG (Finished Goods) |
| (others) | TBD as encountered — verify against `tranzact-v2/inventory/` or `tz-core/` constants |

## Inventory / approval flags

- **`production_processitemissue.expired = 1`** — publish-race orphan marker. The single most reliable signal that an approval got stuck because its underlying snapshot was invalidated by a concurrent publish.
- **`inventory_inventorydocumentapproval.active = 1` + `status = 0`** — "approval is pending and live." Pair with `expired = 1` on the linked item to detect stuck approvals.
- **`stock_moved` (derived)**: `inventory_change_date IS NOT NULL` on the approval row. If null, the stock movement hasn't been recorded — usually means the approval hasn't been fully committed.

## Approval id format

- `IAP${NNNNNN}` — Inventory Approval id used throughout Freshdesk `cf_approval_id` and inventory tables. Always uppercase `IAP`; the numeric suffix is zero-padded.

## String comparison

- **Default MySQL collation is case-insensitive.** Use `BINARY` for case-sensitive matches:
  - `WHERE BINARY unit = 'NOS'` matches only `NOS`, not `nos` or `Nos`.
- Use this whenever a column's case is semantically meaningful (unit codes, currency codes, hashes).

## APM app names

See `tranzact-apm-and-nrql.md`. Observed in NR: `tranzact-v2`, `tz-data-agent-be`, `tz-reporting`, `subhub`, `zipper`, `tz-comms-publisher`.

## TranZact user_id vs Freshdesk requester_id

- **Freshdesk `requester_id`** is a Freshdesk-internal numeric id (11+ digits typically). It is **not** a TranZact id.
- **TranZact `user_id`** lives in `auth_user.id`. It is what NRQL `extra.user_id` and all MySQL `*.user_id` columns reference.
- Same applies for `company_id` — the Freshdesk one is a Freshdesk id; the TranZact one comes from `cf_company_id`.

## Why these belong here, not in the generic skills

These constants are TranZact business semantics — they don't generalize to other MySQL servers, other Freshdesk instances, or other New Relic accounts. The generic `mysql` / `freshdesk` / `newrelic` skills stay clean; the TZ overlay lives in this skill's `references/`.
