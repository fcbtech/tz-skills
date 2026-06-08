# Freshdesk API Reference Notes

Source: https://developers.freshdesk.com/api/

## Basics

- The documentation covers Freshdesk API v2.
- Freshdesk APIs are REST-style JSON over HTTP.
- Standard verbs are used: `GET` fetches resources, `POST` creates resources, `PUT` updates resources, and `DELETE` removes resources.
- Requests must use HTTPS.
- API v2 works via Freshdesk domains, not custom CNAMEs.

## Authentication

Freshdesk examples use Basic Auth with the API key as the username and `X` as the password:

```sh
curl -u <freshdesk-api-key>:X https://domain.freshdesk.com/api/v2/tickets
```

Do not place real API keys in commands shown to the user, logs, committed files, or issue/PR text.

## Rate Limits

- Rate limits depend on the Freshdesk plan.
- Trial accounts have a default API limit of 50 calls per minute.
- Freshdesk returns rate-limit headers such as `X-RateLimit-Total`, `X-RateLimit-Remaining`, `X-RateLimit-Used-CurrentRequest`, and `Retry-After`.
- Invalid requests count toward the rate limit.
- When a request returns `429`, wait for the `Retry-After` value before retrying.

## Pagination

Use pagination for list endpoints:

```text
page=1
per_page=30
```

Freshdesk API v2 supports page sizes up to 100 on supported list endpoints.

## Common Endpoints

Tickets:

- `GET /api/v2/tickets`
- `POST /api/v2/tickets`
- `GET /api/v2/tickets/{id}`
- `PUT /api/v2/tickets/{id}`
- `DELETE /api/v2/tickets/{id}`
- `GET /api/v2/search/tickets?query={query}`
- `POST /api/v2/tickets/{id}/reply`
- `POST /api/v2/tickets/{ticket_id}/notes`
- `GET /api/v2/tickets/{id}/conversations`

Contacts:

- `GET /api/v2/contacts`
- `POST /api/v2/contacts`
- `GET /api/v2/contacts/{id}`
- `PUT /api/v2/contacts/{id}`
- `DELETE /api/v2/contacts/{id}`
- `GET /api/v2/contacts/autocomplete?term={keyword}`
- `GET /api/v2/search/contacts?query={query}`

Agents:

- `GET /api/v2/agents/me`
- `GET /api/v2/agents`
- `GET /api/v2/agents/{id}`
- `POST /api/v2/agents`
- `PUT /api/v2/agents/{id}`
- `DELETE /api/v2/agents/{id}`

Groups:

- `GET /api/v2/groups`
- `GET /api/v2/groups/{id}`
- `POST /api/v2/groups`
- `PUT /api/v2/groups/{id}`
- `DELETE /api/v2/groups/{id}`

Companies:

- `GET /api/v2/companies`
- `GET /api/v2/companies/{id}`
- `POST /api/v2/companies`
- `PUT /api/v2/companies/{id}`
- `DELETE /api/v2/companies/{id}`
- `GET /api/v2/companies/autocomplete?name={keyword}`
- `GET /api/v2/search/companies?query={query}`

## Ticket Field Hints

Common ticket fields when creating or updating:

- `subject`: string.
- `description`: string.
- `email`: requester email for create requests.
- `priority`: numeric priority. Confirm account conventions before assuming labels.
- `status`: numeric status. Confirm account conventions before assuming labels.
- `type`, `source`, `group_id`, `responder_id`, `tags`, and `custom_fields` may be required by local workflows.
