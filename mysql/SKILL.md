---
name: mysql
description: Query a MySQL server via named profiles (cnf files in ~/.tz-oncall/), with a built-in table-name guardrail that catches typos before the query reaches the server. Use when the user asks to query a TranZact MySQL database (production read replica, mstag staging snapshot, mstag-dmz with scrambled credentials), look up rows by id/email/etc., audit recent activity, or perform a safe single-row write via the dry-run protocol. Triggers on keywords like "mysql", "replica", "mstag", "auth_user", "production database query", "look up the user/company/document in the db".
---

# MySQL Skill

Read-mostly MySQL access via the `mysql` CLI, with named profiles and a guardrail that refuses queries against tables that don't exist on the target schema.

## Configuration

Profiles live in `~/.tz-oncall/<profile>.cnf` (mode 0600) — one MySQL `[client]` cnf per host. Override the directory with `MYSQL_PROFILES_DIR` if needed.

Existing profiles in the TranZact on-call set:

| Profile | Host | Purpose |
|---------|------|---------|
| `tz-prod-read-replica` (or `replica`) | DB_READ_REPLICA_* | Production read replica. Authoritative business state. Reads only. |
| `mstag-dmz` | mstag-dmz instance | Staging snapshot of prod with **scrambled user credentials**. Used for user-detail lookups (auth_user, mobile, last login, permissions) so a dev can log in locally as the affected user, and for the dry-run write protocol. |
| `mstag` | mstag instance | Alternate staging snapshot; less commonly needed. |

Whichever profiles exist in `~/.tz-oncall/` are auto-discovered. `mysql_helper.py list-profiles` shows them.

## First Run Setup

If a profile is missing, run:

```bash
python mysql/scripts/mysql_helper.py setup --profile <name>
```

The helper prompts for host/user/password/database and writes `~/.tz-oncall/<name>.cnf` with mode 0600. Never paste credentials on the command line directly (they appear in `ps`).

If you have an existing TranZact `tranzact-v2/.env` with `DB_READ_REPLICA_*` values, you can also create the cnf manually:

```bash
mkdir -p ~/.tz-oncall && chmod 700 ~/.tz-oncall
cat <<'EOF' > ~/.tz-oncall/tz-prod-read-replica.cnf
[client]
host=<DB_READ_REPLICA_HOST>
user=<DB_READ_REPLICA_USER>
password=<DB_READ_REPLICA_PASSWORD>
database=<DB_READ_REPLICA_NAME>
EOF
chmod 600 ~/.tz-oncall/tz-prod-read-replica.cnf
```

Verify with:

```bash
python mysql/scripts/mysql_helper.py run --profile tz-prod-read-replica --sql 'SELECT 1'
```

## Table-Name Guardrail

Every `run` and `dry-run-write` invocation passes the SQL through a regex that extracts identifiers after `FROM`, `JOIN`, `UPDATE`, `INSERT INTO`, `DELETE FROM`, and `TRUNCATE`. Those identifiers are validated against the live schema (cached at `~/.tz-oncall/schema-cache/<profile>.json`, 24h TTL).

Unknown tables → query is refused with a Levenshtein-suggested fix:

```
Table-name guardrail refused this query:
  - unknown table `auth_users` on profile `mstag-dmz`; did you mean: auth_user
```

The schema is auto-fetched on first use per profile. Force a refresh after schema changes:

```bash
python mysql/scripts/mysql_helper.py schema-refresh --profile mstag-dmz
```

Escape hatch: `--no-schema-check` bypasses the guardrail. Use only when you're creating a table in the same statement; the default is on for a reason.

## Helper Script

```bash
# Read a row
python mysql/scripts/mysql_helper.py run --profile tz-prod-read-replica --sql 'SELECT id, email FROM auth_user WHERE id = 12345'

# Read from a file with parameter substitution
python mysql/scripts/mysql_helper.py run --profile mstag-dmz --file path/to/query.sql --var USER_ID=12345

# Dry-run a write on mstag-dmz (mandatory before any commit)
python mysql/scripts/mysql_helper.py dry-run-write \
  --profile mstag-dmz \
  --pre-select  "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --post-select "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --sql "UPDATE auth_user SET is_active = 1 WHERE id = 12345"

# After the user reviews and says 'commit':
python mysql/scripts/mysql_helper.py commit-write \
  --profile mstag-dmz \
  --post-select "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --sql "UPDATE auth_user SET is_active = 1 WHERE id = 12345"
```

## Common Workflows

- **Lookup**: pick the right profile (replica for business state, mstag-dmz for user details — see `references/read-patterns.md`), write a narrow query with a column list, `LIMIT 100` default, run.
- **Audit-trail-lite**: most TranZact tables carry `updated_at` / `updated_by_id`. Useful when the ticket says "this used to work."
- **Single-row write fix**: follow `references/write-protocol.md` — dry-run with full pre/post SELECT, get explicit user `commit`, re-run with `commit-write`. No exceptions.

## Hard Refusals

- Any DML on a production replica profile. Replicas are read-only by policy; the helper has no enforcement here so the discipline lives in the parent orchestrator skill — but as a rule: writes go to `mstag-dmz` and nowhere else.
- DDL (CREATE/ALTER/DROP/TRUNCATE/RENAME) under `dry-run-write` — MySQL implicitly commits DDL, so the rollback is fiction.
- `SELECT *` on hot tables (`audit_logs`, large `documents_*`/`transaction_*` tables). Always pick a column list.
- Echoing the cnf contents back to chat. Use the helper; never `cat ~/.tz-oncall/*.cnf`.

## References

- [`references/read-patterns.md`](references/read-patterns.md) — column-list discipline, `BINARY` for case-sensitive matches, JSON_EXTRACT, audit-trail-lite, common query templates.
- [`references/write-protocol.md`](references/write-protocol.md) — full dry-run/commit protocol, refusal triggers, InnoDB pre-flight.

## Notes on Composition

This skill is intentionally generic. Domain-specific TranZact query templates (auth-user-by-id, company-overview, etc.) live in the `oncall-agent` skill's `references/sql/` because they encode TZ business semantics. The `oncall-agent` orchestrator calls this skill's helper for connection management and the guardrail.
