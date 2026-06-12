# Polyrepo navigation

Pick where to look from ticket symptoms — not by grepping everywhere.

| Symptom shape | Primary look | Secondary |
|---------------|--------------|-----------|
| Backend 4xx/5xx, missing data, permission error | `tranzact-v2/` + NR (`Log`, `TransactionError`) | `tz-core/` for shared models |
| UI rendering / routing on `/v3/*` | `tz-vue-3/` | API call → backend logs |
| UI on `/v2/*` (legacy) | `tranzact-frontend/` | API call → backend logs |
| AI / PO-creation chat | `tz-agents-be/` (FastAPI) + `tz-agents-fe/` (React 19) | NR if instrumented |
| Integration (HubSpot/Razorpay/GST/Freshdesk/etc.) | `tz-micros/<service>/` (~122 services — narrow by ticket keyword) | Backend webhook handler in `tranzact-v2/` |
| Reporting / analytics | `tz-reporting/` | Redshift (deferred v1) |
| Cross-cutting model | `tz-core/` first, always |

## Rules

1. **Never grep `tz-micros/` whole.** Pin to a specific service by ticket keyword (e.g. "Razorpay" → `tz-micros/*razorpay*`; "WhatsApp"/"SMS" → `tz-micros/*sms*` or `*gupshup*`; "Freshdesk" → `tz-micros/*freshdesk*`).
2. **Models live in `tz-core/`** for cross-cutting concerns (`Company`, `User`, document base types). App-specific models live in `tranzact-v2/<app>/models.py`. Always check `tz-core/` first.
3. **Frontend issues usually require a backend check.** The UI rarely fails alone; it usually fails because the API returned an unexpected shape. Plan to look at both.
4. **`tz-vue-3` requires `.npmrc` with `@fcbtech` registry token** to even install. If a ticket says "frontend won't build," that's a reasonable first guess.
5. **`/v2/*` URLs → `tranzact-frontend` (Vue 2 legacy); `/*` → `tz-vue-3` (modern).** Ticket URLs are a strong routing hint.

## Microservice keyword index

| Ticket keyword | Likely service folder pattern |
|----------------|--------------------------------|
| Razorpay, payment gateway | `*razorpay*` |
| WhatsApp, SMS, Gupshup | `*sms*`, `*gupshup*`, `*whatsapp*` |
| Freshdesk, support ticket | `*freshdesk*` |
| HubSpot, CRM | `*hubspot*` |
| GSTIN, e-invoice, IRN | `*gstin*`, `*einvoice*`, `*edocument*` |
| Eway bill | `*eway*` |
| BigQuery, ETL | `*bigquery*`, `*etl*` |
| Audit, compliance | `*audit*` |
| Print, document download | `*print*`, `*zipper*` |

Confirm with `ls ~/Work/tranzact/tranzact-fullstack/tz-micros | grep <pattern>` before grepping.
