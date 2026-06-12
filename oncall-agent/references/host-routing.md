# MySQL host routing

Which profile answers which kind of question. Wrong host → wrong answer (silently).

| Query type | Profile | Why |
|------------|---------|-----|
| Business state: documents, transactions, settings, company-scoped data (`documents_*`, `transaction_*`, `profile_mgt_*`, audit-trail-lite via `updated_at`) | `tz-prod-read-replica` | Authoritative production state, read-only enforced |
| **User details**: `auth_user`, mobile / email / last login / scrambled password, user permissions — anything you'd need to **log in locally as the affected user** to reproduce the ticket | `mstag-dmz` | mstag-dmz holds a staging snapshot of prod with **scrambled credentials**, so on-call can impersonate from local without ever touching prod auth. The prod replica's `auth_user` row is unusable for impersonation |
| Any write (correctional fix, status flip, flag toggle) under the `mysql/references/write-protocol.md` dry-run protocol | `mstag-dmz` | The only environment where the `mysql` skill allows writes |

## Hard rules

- **Never** look up user details on the prod replica.
- **Never** use `mstag-dmz` for current business-state questions (documents, transactions): its snapshot is drift-prone.
- If `~/.tz-oncall/mstag-dmz.cnf` is missing → **refuse** user-detail queries and write attempts. Point the user at `mysql/SKILL.md` first-run setup. Do not silently fall back to the replica.

## Profile naming

The `mysql` skill auto-discovers profiles from `~/.tz-oncall/*.cnf`. Standard names used by this orchestrator's composite scripts:

| Profile name (preferred) | Fallback | Use |
|--------------------------|----------|-----|
| `tz-prod-read-replica` | `replica` | Prod read |
| `mstag-dmz` | — | User details + writes |
| `mstag` | — | Alternate staging snapshot, rarely needed |

Composite scripts probe for both names where applicable: if `tz-prod-read-replica` is missing they try `replica`.
