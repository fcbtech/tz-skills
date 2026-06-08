# Keka API Reference Notes

Use the current public Keka API documentation when endpoint details are uncertain:

- `https://apidocs.keka.com/`
- `https://developers.keka.com/docs/getting-started-with-keka-apis`

## Documentation Caveats

- Some URLs in examples are blank or inconsistent.
- Some examples include `keka.com`, `kekademo.com`, and occasionally suspicious domains like `kekad.com`.
- Some paths have likely typos, for example `/api/v1/v1/...` or `candiates`.
- Always keep base URLs configurable and verify live against the tenant.

## Authentication

Keka uses OAuth-style token auth.

Token endpoint patterns:

- Production: `https://login.keka.com/connect/token`
- Sandbox/demo: `https://login.kekademo.com/connect/token`
- General pattern from docs: `https://login.{environment}.com/connect/token`

Form fields:

```text
grant_type=kekaapi
scope=kekaapi
client_id=<client_id>
client_secret=<client_secret>
api_key=<api_key>
```

Token response fields usually include:

```json
{
  "access_token": "...",
  "expires_in": 86400,
  "token_type": "Bearer",
  "scope": "kekaapi"
}
```

## Base URL

API base URL pattern:

```text
https://{subdomain}.{environment}.com/api/v1
```

Examples:

- Production: `https://{subdomain}.keka.com/api/v1`
- Sandbox/demo: `https://{subdomain}.kekademo.com/api/v1`

## Pagination

Common query params:

- `pageNumber`, default `1`
- `pageSize`, default `100`, often max `200`

Paginated responses may include:

- `pageNumber`
- `pageSize`
- `totalPages`
- `totalRecords`
- `nextPage`
- `previousPage`
- `succeeded`
- `data`

## Rate Limit

Keka docs mention:

- 50 requests/minute
- Refill every 60 seconds
- HTTP 429 with reason like `rateLimitExceeded`

Rules:

- Prefer page size 200 for list exports.
- Sleep/retry on 429.
- Avoid concurrent fan-out unless explicitly needed.
- For large exports, log progress locally but do not spam chat with PII.

## Module Coverage

### Auth

Minimum workflows:

- Validate credentials by fetching token.
- Validate tenant/base URL by making a small read request.
- Cache token in memory for the current script only.

Safe test endpoint options:

- `GET /hris/employees?pageNumber=1&pageSize=1`
- Or another small org metadata endpoint if employee scope is unavailable.

### Core HR

Common endpoints from docs:

- `GET /hris/employees`
- `POST /hris/employees`
- `GET /hris/employees/{id}`
- Departments
- Groups
- Group types
- Locations
- Job titles
- Currencies
- Notice periods
- Exit reasons
- Exit requests

### Attendance

Common endpoints/workflows:

- `GET /time/attendance`
- Capture schemes
- Shift policies
- Holiday calendar
- Tracking policies
- Weekly-off policies
- Attendance punch push endpoint observed in docs: `POST https://cin01.a.keka.com/v1/logs`

Date conventions:

- Common params: `from`, `to`
- If omitted, docs say last 30 days may be returned.
- Date range max often 90 days.

### Payroll

Common endpoints:

- `GET /payroll/salarycomponents`
- `GET /payroll/paygroups`
- `GET /payroll/paygroups/{payGroupId}/paycycles`
- `GET /payroll/paygroups/{payGroupId}/paycycles/{payCycleId}/payregister`
- Pay batches
- Payments
- Pay grades/bands
- Salaries
- Full and final settlement data

### Hire

Common resources:

- Jobs
- Application fields
- Candidates
- Candidate updates
- Notes
- Interviews
- Scorecards
- Preboarding

Known docs issues:

- Some hire URLs appear blank.
- Candidate creation path may include a duplicated `/v1` in docs.
- Preboarding docs may contain typo `candiates`.

### Expense

Common endpoints:

- `GET /expense/categories`
- `GET /expense/claims`
- `GET /expensepolicies`

### Leave

Common endpoints:

- `GET /time/leaverequests`
- `POST /time/leaverequests`
- Leave types
- Leave balances
- Leave plans

