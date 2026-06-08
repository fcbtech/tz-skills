---
name: freshdesk
description: Interact with the Freshdesk API. Use when the user asks to list, search, read, create, update, or delete Freshdesk tickets, contacts, agents, companies, groups, conversations, notes, or other Freshdesk helpdesk resources.
---

# Freshdesk API Skill

Use this skill to work with Freshdesk API v2 from an agent session.

## Configuration

Freshdesk requires an account domain and an API key.

- `FRESHDESK_DOMAIN`: Freshdesk account domain, for example `acme.freshdesk.com` or `acme`.
- `FRESHDESK_API_KEY`: Freshdesk API key.

Read configuration in this order:

1. Environment variables.
2. `scripts/.env` adjacent to `scripts/freshdesk_helper.py`.

Never commit a real API key. The repo ignores `freshdesk/scripts/.env`.

## First Run

If the key is missing, ask the user to paste the Freshdesk API key. If the domain is missing, ask for the Freshdesk domain too. Then save both values for future sessions:

```sh
python freshdesk/scripts/freshdesk_helper.py setup
```

The helper writes `freshdesk/scripts/.env` with mode `0600`.

## Helper Script

Prefer the Python helper for API calls:

```sh
python freshdesk/scripts/freshdesk_helper.py me
python freshdesk/scripts/freshdesk_helper.py list-tickets --page 1 --per-page 30
python freshdesk/scripts/freshdesk_helper.py get-ticket 123
python freshdesk/scripts/freshdesk_helper.py search-tickets "status:2 AND priority:1"
python freshdesk/scripts/freshdesk_helper.py create-ticket --subject "Printer down" --email user@example.com --description "Cannot print" --priority 2 --status 2
python freshdesk/scripts/freshdesk_helper.py update-ticket 123 --json '{"status": 3}'
python freshdesk/scripts/freshdesk_helper.py raw GET /api/v2/groups
```

Use `--pretty` to print formatted JSON. Use `--json` for arbitrary request bodies.

## API Rules

- Base URL: `https://<domain>.freshdesk.com/api/v2`.
- Use HTTPS only.
- Freshdesk API v2 works via Freshdesk domains, not custom CNAMEs.
- Authentication is HTTP Basic Auth with the API key as the username and a dummy password such as `X`.
- API requests and responses are JSON unless the endpoint explicitly requires multipart uploads.
- Watch `X-RateLimit-Remaining` and `Retry-After` headers. On `429`, wait the retry duration before trying again.
- Use pagination parameters (`page`, `per_page`) for list endpoints. Maximum page size is commonly 100.

## Common Workflows

- Ticket lookup: search tickets first when the user gives a partial title, email, or vague issue; call `get-ticket` once an ID is known.
- Ticket creation: confirm requester email, subject, description, priority, and status before creating.
- Ticket updates: read the current ticket first unless the user gives an exact ID and exact field update.
- Contacts: use contact autocomplete/search before creating a new contact to avoid duplicates.
- Destructive actions: confirm before delete, hard delete, merge, or bulk operations.

## References

Read `references/api-reference.md` when you need endpoint groups, authentication details, pagination behavior, rate-limit notes, or response conventions.

