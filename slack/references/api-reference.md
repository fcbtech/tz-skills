# Slack API reference (operational notes)

This is the on-call-relevant slice of the Slack API, not the full reference. For full docs see https://api.slack.com/methods.

## Auth modes

### Incoming webhooks

- One URL = one channel. The channel is fixed when the webhook is created.
- POST JSON body to `https://hooks.slack.com/services/<T>/<B>/<X>`.
- Body keys: `text`, `blocks`, `attachments`, `username`, `icon_emoji`. No `channel` key — the URL determines the channel.
- Returns plain text `ok` on success. Non-2xx ≈ failure.
- No scopes, no OAuth. Safe to deploy to CI/cron environments.

### Bot tokens (Web API)

- Format: `xoxb-...`. Belong to a Slack app installed in the workspace.
- POST JSON body to `https://slack.com/api/<method>` with `Authorization: Bearer <token>` and `Content-Type: application/json; charset=utf-8`.
- Common scopes used by this skill:

| Scope | Why |
|-------|-----|
| `chat:write` | Post messages as the bot |
| `chat:write.public` | Post to channels the bot isn't a member of |
| `channels:read` | `conversations.list` (needed for `lookup-channel`) |
| `users:read.email` | `users.lookupByEmail` |
| `files:write` | File upload (not used by this skill yet) |

## Creating a bot token

1. https://api.slack.com/apps → **Create New App** → **From scratch** → workspace.
2. **OAuth & Permissions** → add the scopes above to **Bot Token Scopes**.
3. **Install to Workspace** → admin approval if required → returns `xoxb-...`.
4. Paste into `SLACK_BOT_TOKEN` (env or `setup`).
5. Invite the bot into the channels you want to post to: `/invite @<bot-name>` from the channel.

## Creating an incoming webhook

1. Same app dashboard → **Incoming Webhooks** → toggle on.
2. **Add New Webhook to Workspace** → pick a channel → returns a URL.
3. Paste into `SLACK_WEBHOOK_URL`.

## Common methods (Web API)

| Method | Purpose | Required fields |
|--------|---------|-----------------|
| `chat.postMessage` | Post a message | `channel`, `text` (or `blocks`) |
| `chat.update` | Edit a message | `channel`, `ts`, `text`/`blocks` |
| `chat.delete` | Delete a message | `channel`, `ts` |
| `conversations.list` | List channels (for name → id) | — |
| `conversations.history` | Read recent messages | `channel` |
| `users.lookupByEmail` | Email → user id | `email` |
| `users.info` | User id → profile | `user` |
| `files.upload` | Upload a file | `channels`, `file` or `content` |

`channel` accepts either an id (`C0123ABCD`) or a name (`#oncall`) — id is faster and unambiguous.

## Threading

- A message's `ts` is its timestamp id (e.g. `1700000000.000100`).
- To reply in that thread: pass `thread_ts: <parent_ts>` to `chat.postMessage`.
- To make the threaded reply visible in the channel too, also pass `reply_broadcast: true`.
- **Webhooks can't thread.** Threading needs the Web API.

## Common error codes

| Code | Meaning | Likely fix |
|------|---------|------------|
| `not_in_channel` | Bot isn't a member of the channel | `/invite @<bot>` in the channel, OR add `chat:write.public` scope |
| `channel_not_found` | Bad channel id / name | `lookup-channel` to resolve |
| `not_authed` / `invalid_auth` | Missing or wrong token | Rotate the bot token |
| `missing_scope` | Token lacks the required scope | Add the scope, reinstall the app |
| `rate_limited` | Hit the per-method limit | Honor `Retry-After`; backoff |
| `users_not_found` | Email isn't a Slack user (or hidden by privacy) | Confirm the email; some users hide their email |

## Block Kit (for richer formatting)

Pass `--blocks` with a JSON array. Useful structures:

```json
[
  {"type": "header",  "text": {"type": "plain_text", "text": "Ticket FD#17268"}},
  {"type": "section", "text": {"type": "mrkdwn", "text": "*Diagnosis*: ..."}},
  {"type": "context", "elements": [{"type": "mrkdwn", "text": "company_id 721303 · user_id 633444"}]}
]
```

For interactive components (buttons, dropdowns) you'd need a request URL configured on the Slack app — not used by this skill.

## Mentions

- `<@U0123ABCD>` mentions a user by id. Get the id from `lookup-user`.
- `<#C0123ABCD|channel-name>` links a channel.
- `<!channel>`, `<!here>`, `<!subteam^S012ABCD>` — broadcast mentions. Use sparingly.
