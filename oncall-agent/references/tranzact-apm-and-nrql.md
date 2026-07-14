# TranZact APM registry + NRQL caveats

TZ-specific overlay for the generic `newrelic` skill. Read this before running any NRQL against a TZ entity.

## APM app names (verified)

| Repo | NR `appName` / `entity.name` |
|------|-------------------------------|
| `tranzact-v2` | `tranzact-v2` |
| `tz-reporting` | `tz-reporting` |
| `tz-agents-be` | `tz-data-agent-be` *(differs from repo name)* |
| `tz-micros/subhub*` | `subhub` |
| `tz-micros/zipper*` | `zipper` |
| `tz-micros/*comms*` | `tz-comms-publisher` |
| `tz-vue-3` / `tranzact-frontend` | no APM entity — SPAs; NR Browser may be separate if instrumented |

Re-discover if a name seems stale:

```bash
newrelic apm application search
```

## NRQL caveats specific to TranZact instrumentation

1. **`company_id` appears on `TransactionError` (and JS errors), NOT on `Transaction`.** Filtering normal `Transaction` rows by `company_id` returns nothing. Use `TransactionError` or `Log` for tenant-scoped filtering.
2. **Log attribute namespace is `extra.*`.** TZ Python services emit structured logs with `extra.request_id`, `extra.user_id`, `extra.user_identity`, `extra.company_id`. Top-level `request_id` / `user_id` will not exist.
3. **The JSON log formatter is only enabled in production.** Locally, logs are plain text and `extra.*` fields don't carry over. Don't try to reproduce the NR shape from local logs.
4. **Frontend SPAs are not APM entities.** They may have NR Browser instrumented separately; check the Browser app list if a UI-side issue is reported.

## Common templates (parameterized — passed to the `newrelic` skill)

```sql
-- Log lookup by entity + request_id
SELECT * FROM Log
WHERE entity.name = '${APP_NAME}'
  AND extra.request_id IN ('${RID_1}','${RID_2}')
SINCE '${T0}'
```

```sql
-- Transaction count in window by app
SELECT count(*) FROM Transaction
WHERE appName = '${APP_NAME}'
SINCE '${T0}' UNTIL '${T1}'
```

```sql
-- User-scoped error trace
SELECT timestamp, message, extra.request_id
FROM Log
WHERE entity.name = '${APP_NAME}'
  AND extra.user_id = '${USER_ID}'
SINCE '${T0}'
```

```sql
-- Log time-range probe (do we have data?)
SELECT count(*), earliest(timestamp), latest(timestamp)
FROM Log
WHERE entity.name = '${APP_NAME}'
SINCE 30 days ago
```

## Discovery commands

```bash
# Find an app by partial name
newrelic apm application search --name "tranzact"

# Find an entity by alert state
newrelic entity search --alert-severity CRITICAL

# Run NRQL (the canonical primitive)
newrelic nrql query --query 'SELECT count(*) FROM Transaction WHERE appName = "tranzact-v2" SINCE 1 hour ago'
```

For exhaustive CLI reference see `newrelic/cli-docs/` in the `newrelic` skill.
