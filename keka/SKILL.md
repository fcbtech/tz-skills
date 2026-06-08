---
name: keka
description: Use when working with Keka HRMS APIs. Covers authentication, Core HR, attendance, payroll, hire, expense, leave, reporting, exports, and safe Keka API troubleshooting.
license: MIT
metadata:
  version: "1.0.0"
  tags: "keka, hrms, api, hr, payroll, attendance, leave, expense, hire"
---

# Keka API Skill

Use this skill to work with Keka HRMS APIs from an agent session.

## When To Use

Use this skill when the user asks to:

- Query Keka employees, departments, locations, job titles, or org metadata.
- Fetch attendance, leave, payroll, hire, or expense data.
- Build a report/export from Keka data.
- Test Keka API credentials or diagnose auth/base URL issues.
- Create a focused Keka integration using Python or curl.

Do not guess payroll, employee, attendance, or leave data without live API access. Do not perform bulk writes unless the user explicitly approves scope and the request is tested in sandbox first.

## Configuration

Keka uses OAuth-style token authentication. Required local configuration:

- `KEKA_SUBDOMAIN`: Tenant subdomain.
- `KEKA_ENV`: Environment, usually `keka`; use `kekademo` for sandbox/demo.
- `KEKA_CLIENT_ID`: Keka API client ID.
- `KEKA_CLIENT_SECRET`: Keka API client secret.
- `KEKA_API_KEY`: Keka API key.

Read configuration in this order:

1. Environment variables.
2. `scripts/.env` adjacent to `scripts/keka_helper.py`.

Never commit real Keka credentials. The repo ignores `keka/scripts/.env`.

## First Run

If any required value is missing, ask the user for it and save it for future sessions:

```sh
python keka/scripts/keka_helper.py setup
```

The helper writes `keka/scripts/.env` with mode `0600`.

Validate credentials without printing tokens:

```sh
python keka/scripts/keka_helper.py token-test
```

## Helper Script

Prefer the Python helper for reusable API work:

```sh
python keka/scripts/keka_helper.py token-test
python keka/scripts/keka_helper.py get /hris/employees --param pageNumber=1 --param pageSize=1 --pretty
python keka/scripts/keka_helper.py get-all /hris/employees --page-size 200 --output keka_employees.json
python keka/scripts/keka_helper.py export-employees --output keka_employees.json
python keka/scripts/keka_helper.py raw GET /time/leaverequests --param pageNumber=1 --param pageSize=50
```

The helper handles token generation, local `.env` loading, pagination, JSON output, and HTTP 429 retries.

## Workflow

1. Confirm credentials are available or run `scripts/keka_helper.py setup`.
2. Run `token-test`.
3. Run a tiny read, usually `GET /hris/employees?pageNumber=1&pageSize=1`.
4. Inspect one response page before paginating or exporting.
5. Use local files for sensitive exports; avoid pasting raw employee/payroll/expense data in chat.
6. Require explicit confirmation before POST/PATCH/PUT/DELETE operations.

## References

- Read `references/api-reference.md` for auth, base URLs, pagination, rate limits, endpoint groups, and documentation caveats.
- Read `references/workflows.md` for module-specific safety rules, common pitfalls, and verification checklist.

