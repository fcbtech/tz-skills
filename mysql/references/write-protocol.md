# MySQL write protocol — dry-run, then commit

The helper enforces a two-phase protocol for any DML. Phase 1 (`dry-run-write`) runs the DML inside `BEGIN ... ROLLBACK` with pre- and post-state SELECTs, so you see exactly what would change. Phase 2 (`commit-write`) re-runs the same DML under `BEGIN ... COMMIT` — only after explicit user confirmation.

## Why it exists

- **Catches WHERE-clause bugs.** A typo'd WHERE shows `rows_affected: 0` in the dry-run; you never commit it.
- **Catches over-broad updates.** Forgot a clause and it would update 100 rows instead of 1? You see 100 in the dry-run output.
- **The post-state SELECT is the evidence.** Not "what would happen" prose — the literal row the user reviews.
- **DDL is excluded.** MySQL implicitly commits DDL, so the rollback is fiction. The helper refuses DDL in `dry-run-write`.

## Phase 1 — Dry run (always; never skipped)

```bash
python mysql/scripts/mysql_helper.py dry-run-write \
  --profile mstag-dmz \
  --pre-select  "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --post-select "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --sql         "UPDATE auth_user SET is_active = 1 WHERE id = 12345"
```

The helper assembles and runs:

```sql
START TRANSACTION;
-- pre-state
SELECT id, is_active FROM auth_user WHERE id = 12345;
-- DML
UPDATE auth_user SET is_active = 1 WHERE id = 12345;
SELECT ROW_COUNT() AS rows_affected;
-- post-state
SELECT id, is_active FROM auth_user WHERE id = 12345;
ROLLBACK;
```

Show the user, in this exact order:
1. **`Writing to mstag-dmz: <one-line description>`** — bold, mandatory.
2. The full wrapped SQL block, fenced.
3. The pre-state, `rows_affected`, post-state rows.
4. The literal sentence: *"Reply `commit` to re-run with COMMIT, anything else aborts."*
5. **Stop.** Do not call any further tool until the user replies.

## Phase 2 — Commit (only after explicit `commit`)

```bash
python mysql/scripts/mysql_helper.py commit-write \
  --profile mstag-dmz \
  --post-select "SELECT id, is_active FROM auth_user WHERE id = 12345" \
  --sql         "UPDATE auth_user SET is_active = 1 WHERE id = 12345"
```

Helper assembles:

```sql
START TRANSACTION;
UPDATE auth_user SET is_active = 1 WHERE id = 12345;
SELECT ROW_COUNT() AS rows_affected;
SELECT id, is_active FROM auth_user WHERE id = 12345;
COMMIT;
```

Show the post-commit state. Stop.

## Refusal triggers — abort the dry-run, do NOT proceed

| Condition | Reason |
|-----------|--------|
| Pre-state SELECT returned 0 rows | WHERE matched nothing; almost certainly a bad filter |
| `rows_affected` is 0 | Same reason; skip the commit |
| `rows_affected` >> expected | Demand re-confirmation |
| Target table engine is not InnoDB (`SHOW TABLE STATUS LIKE '<table>'`) | MyISAM / ARCHIVE silently ignore transactions |
| Statement is DDL (ALTER/CREATE/DROP/TRUNCATE/RENAME) | DDL implicitly commits in MySQL |
| User asked to skip the dry-run | Refuse; restate this protocol |

The helper itself refuses when:

- Profile is not `mstag-dmz`. Writes go nowhere else.
- The user's `--sql` contains `BEGIN` / `COMMIT` / `ROLLBACK`. The wrapper adds those; nesting transactions silently breaks rollback.
- The guardrail (see `SKILL.md`) flags an unknown table.

## InnoDB pre-flight

Before the first write of a session, confirm the target table is InnoDB:

```bash
python mysql/scripts/mysql_helper.py run --profile mstag-dmz --sql "SHOW TABLE STATUS LIKE 'auth_user'"
```

If `Engine` ≠ `InnoDB`, refuse. Don't even try the dry-run — MyISAM tables don't honor transactions, so the rollback is silently a no-op.

## Multi-statement writes

Multiple DML statements in one transaction are fine. The helper still runs the whole block under `BEGIN ... ROLLBACK` (or `... COMMIT`), and `ROW_COUNT()` only reflects the last statement — rely on the pre/post diff to confirm per-table row counts.
