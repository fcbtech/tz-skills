# Identifier resolution recipes (fallback)

**Primary source — Freshdesk sidebar custom fields** (see `tranzact-freshdesk-fields.md`). If `cf_company_id` and `cf_user_id*` are populated, use them directly and skip everything below.

The recipes here are the **fallback** path when sidebar fields are empty / null and the user has confirmed which lookup key to use.

Verify table names from `tz-core/` models before running. The literal table names below are placeholders likely correct but not guaranteed.

**Host routing for user lookups.** Anything touching `auth_user` (email, mobile, name, last_login, permissions) runs on `mstag-dmz` — never on the prod replica. mstag-dmz holds a snapshot of prod with scrambled credentials so the on-call can log in as the user from local. Company-only lookups (`core_company` by name / GST) stay on `tz-prod-read-replica`.

Run via the `mysql` skill helper so the table-name guardrail applies:

```bash
python mysql/scripts/mysql_helper.py run --profile <profile> --sql '<query>'
```

## Email → user

```sql
SELECT id AS user_id, email, is_active
FROM auth_user
WHERE email = '${EMAIL}';
```

Profile: `mstag-dmz`.

## Email → all companies for that user

See `sql/user-profile-across-companies.sql`.

## Company name → company (fuzzy)

```sql
SELECT id, name, is_active, creation_date
FROM profile_mgt_company
WHERE name LIKE '%${FRAGMENT}%'
ORDER BY id DESC
LIMIT 20;
```

Profile: `tz-prod-read-replica`. Multiple matches are common — surface them all and let the user pick.

## GST → company

```sql
SELECT id, name, gst, is_active
FROM profile_mgt_company
WHERE gst = '${GST}';
```

Profile: `tz-prod-read-replica`. GST is unique per company. Escalate on multiple matches.

## Document number → document

Document tables are per-type. Verify in `tz-core/tranzact/documents/models.py` or `tranzact-v2/documents/`.

| Document type | Likely table |
|---------------|--------------|
| Proforma Invoice (PI) | `documents_proforma` |
| Tax Invoice | `documents_invoice` |
| Purchase Order (PO) | `documents_po` |
| Order Confirmation (OC) | `documents_oc` |
| GRN / Inward | `documents_inward` |
| Challan | `documents_challan` |
| Credit Note (CN) | `documents_cn` |
| Debit Note (DN) | `documents_dn` |
| Sales Return (SR) | `documents_sr` |
| QIR | `documents_qir` |

```sql
SELECT id, document_number, status, company_id, created_at, created_by_id
FROM <document_table>
WHERE document_number = '${NUMBER}'
  AND company_id = ${COMPANY_ID};
```

Always pin by `company_id` — document numbers recur across tenants.

## User by name (last resort, fuzzy)

```sql
SELECT id, email, first_name, last_name
FROM auth_user
WHERE LOWER(CONCAT(first_name, ' ', last_name)) LIKE LOWER('%${FRAGMENT}%')
ORDER BY id DESC
LIMIT 20;
```

Profile: `mstag-dmz`. Names are not unique; prefer email when available.

## Mobile / phone → user

```sql
SELECT id, email, mobile
FROM auth_user
WHERE mobile = '${MOBILE}' OR mobile = '+91${MOBILE}'
LIMIT 20;
```

Profile: `mstag-dmz`. Indian numbers may carry `+91` or not.

## Resolution announcement

After resolution, announce on its own line before any further query:

> Resolved: `someone@example.com` → `user_id=9381`, `company_id=12345` (Acme Industries).

This makes the resolution auditable so a mis-resolution gets caught before NR/DB queries spend time on the wrong entity.
