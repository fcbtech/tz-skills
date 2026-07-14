# MySQL read patterns

Generic discipline for reading from any MySQL profile via this skill's helper. TranZact-specific table choices live in the `oncall-agent` skill.

## Discipline

- **Always pick a column list.** Never `SELECT *` on hot tables (`audit_logs`, large `documents_*`, `transaction_*`).
- **Always include a narrowing `WHERE`.** Filter on a primary key, a tenant id (e.g. `company_id`), or a time window (`created_at > NOW() - INTERVAL 4 HOUR`).
- **Always include `LIMIT`** on production reads. Default to 100 unless the user gave one.
- **Cite the source of truth** for any non-obvious schema. For TranZact: "schema verified from `tz-core/tranzact/<module>/models.py:<Class>`."

## Helper invocations

```bash
# Inline
python mysql/scripts/mysql_helper.py run --profile <name> --sql 'SELECT id, email FROM auth_user WHERE id = 42'

# From a file with variable substitution
python mysql/scripts/mysql_helper.py run --profile <name> --file query.sql --var USER_ID=42 --var COMPANY_ID=12345
```

`--var KEY=VALUE` replaces `${KEY}` literally in the SQL before sending. Quote values that contain spaces. There is no SQL escaping — keep substitutions to numeric ids and known-safe identifiers.

## Common shapes

### Recent activity by tenant on a document type

```sql
SELECT id, document_number, status, created_at, created_by_id
FROM <document_table>
WHERE company_id = ${COMPANY_ID}
  AND created_at > NOW() - INTERVAL ${HOURS} HOUR
ORDER BY created_at DESC
LIMIT 50;
```

### Feature-flag / setting probe

```sql
SELECT company_id, ${FLAG_COLUMN}
FROM <feature_settings_table>
WHERE company_id = ${COMPANY_ID};
```

### Status distribution

```sql
SELECT status, COUNT(*) AS n
FROM <document_table>
WHERE company_id = ${COMPANY_ID}
  AND created_at > NOW() - INTERVAL 24 HOUR
GROUP BY status
ORDER BY n DESC;
```

### Audit-trail-lite via `updated_at`

```sql
SELECT id, ${RELEVANT_COLUMNS}, updated_at, updated_by_id
FROM <settings_table>
WHERE company_id = ${COMPANY_ID}
  AND updated_at > NOW() - INTERVAL 7 DAY
ORDER BY updated_at DESC
LIMIT 20;
```

### Case-sensitive string match

MySQL's default collation is case-insensitive. For exact-case matches (e.g. comparing unit codes like `NOS` vs `nos`):

```sql
SELECT id, name FROM products WHERE BINARY name = 'NOS' LIMIT 10;
```

### JSON field extraction

```sql
SELECT id, JSON_EXTRACT(metadata, '$.flag_name') AS flag
FROM some_settings_table
WHERE company_id = ${COMPANY_ID}
LIMIT 10;
```

## What not to do

- `SELECT *` on `audit_logs` — these tables are huge and contain JSON blobs; will DoS the server.
- Cross-join two large tables without a tenant filter.
- Put credentials on the CLI (`-pPASSWORD`) — they appear in `ps`. The helper always uses `--defaults-extra-file`.
- Leave off `LIMIT` "just to see how big this is." Use `SELECT COUNT(*) FROM <t> WHERE ...` instead.
- Bypass the table-name guardrail with `--no-schema-check` for routine reads. Reserve it for cases where you're truly creating a table in the same statement.
