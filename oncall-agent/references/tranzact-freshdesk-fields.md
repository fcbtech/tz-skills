# TranZact Freshdesk custom fields

TZ-specific overlay for the generic `freshdesk` skill. The custom-field IDs below are what the on-call form populates.

## Primary identification fields

| Field | TranZact meaning | Notes |
|-------|------------------|-------|
| `custom_fields.cf_company_id` | TranZact `company_id` | **Always** use this — not Freshdesk's top-level `company_id` |
| `custom_fields.cf_user_id*` | TranZact `user_id` | Key may carry a numeric Freshdesk-internal suffix, e.g. `cf_user_id892854`. **Match by prefix.** |
| `custom_fields.cf_approval_id` | TranZact `approval_id` (e.g. `IAP01436`) | Present on approval-related tickets |
| `custom_fields.cf_entity_id` | TranZact entity id (document / object) | Present when the ticket is tied to a specific document |
| `custom_fields.cf_entity_type` | TranZact entity type code | Pairs with `cf_entity_id` |

## Reading them

Use the `freshdesk` skill helper:

```bash
python freshdesk/scripts/freshdesk_helper.py get-ticket <ticket-id> --pretty | jq '.custom_fields'
```

Or use this orchestrator's `scripts/fetch-ticket-context.sh <ticket-id>` which extracts the fields, handles the `cf_user_id*` prefix matching, and prints a ready-to-use resolution block.

## What NOT to use

- **`requester_id`** (top-level) — Freshdesk-internal numeric id; not a TranZact `user_id`. Will return zero rows from MySQL.
- **`company_id`** (top-level) — Freshdesk-internal numeric id; not a TranZact `company_id`.
- **Requester email as primary key** — emails route to the wrong tenant when a user belongs to multiple companies. Use email only as a confirmed fallback.

## Resolution rule

If `cf_company_id` OR `cf_user_id*` is missing / null / empty:

1. **Do not** silently fall back to email lookup.
2. **Stop and ask the user** which identifier they want to use.
3. Then run the fallback recipes in `identifier-recipes.md`.
