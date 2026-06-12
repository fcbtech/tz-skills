---
name: slack
description: Post messages and look up users/channels on Slack via either an incoming webhook OR the Web API (auto-detected). Use when the user asks to send a Slack message, notify a channel, reply in a thread, look up a Slack user by email, or resolve a channel name to an id. Triggers on keywords like "slack", "post to slack", "notify on slack", "send slack message", "slack channel", "slack thread".
---

# Slack Skill

Post messages and perform lookups on Slack from an agent session.

## Configuration

Two auth modes are supported. The helper picks whichever is configured (preferring the bot token when both are present).

| Variable | Mode | Capability |
|----------|------|------------|
| `SLACK_BOT_TOKEN` | Web API | Full capability — threading, mentions, channel/user lookup, file upload. Format: `xoxb-...`. Create via a Slack app at https://api.slack.com/apps. |
| `SLACK_WEBHOOK_URL` | Incoming webhook | Simplest path. Posts to one channel (whichever the webhook was created for). No threading, no mentions-as-users. |
| `SLACK_DEFAULT_CHANNEL` | Either | Default channel for `post` / `post-message` when `--channel` is omitted. |

Read in this order:
1. Environment variables.
2. `scripts/.env` adjacent to `scripts/slack_helper.py`.

Never commit a real token or webhook URL. The repo ignores `slack/scripts/.env`.

## First Run

If neither token nor webhook is set, run:

```bash
python slack/scripts/slack_helper.py setup
```

The helper prompts for whichever values you want to provide (you can configure both) and saves them to `slack/scripts/.env` with mode `0600`.

## Helper Script

```bash
# Auto mode — bot token if set, else webhook
python slack/scripts/slack_helper.py post --text "deploy started"
python slack/scripts/slack_helper.py post --channel "#oncall" --text "found a stuck IAP"

# Force the Web API (needed for threading / mentions / explicit channel)
python slack/scripts/slack_helper.py post-message \
    --channel "#oncall" \
    --text "Diagnosis: <see details below>" \
    --thread-ts 1700000000.000100

# Force the webhook (the simplest path)
python slack/scripts/slack_helper.py post-webhook --text "hello from CI"

# Read message body from stdin (handy for piping diagnoses)
echo "diagnosis text" | python slack/scripts/slack_helper.py post --channel "#oncall"

# Lookups (Web API only)
python slack/scripts/slack_helper.py lookup-user dev@letstranzact.com
python slack/scripts/slack_helper.py lookup-channel oncall

# Escape hatch — call any Web API method
python slack/scripts/slack_helper.py raw chat.postMessage --json '{"channel":"C123","text":"hi"}'
```

`--blocks` accepts a Slack [Block Kit](https://api.slack.com/block-kit) JSON array for rich formatting.

## API Rules

- Web API base: `https://slack.com/api/<method>`. All Web API calls are POST with `Authorization: Bearer ${SLACK_BOT_TOKEN}` and JSON body. The helper handles this.
- Webhook: a single endpoint `https://hooks.slack.com/services/...`. POST JSON. Slack returns plain text `ok` on success.
- Rate limits are per-method (Web API) or per-webhook (~1/sec sustained). The helper does not retry — caller should backoff on `429`.
- **Never echo `SLACK_BOT_TOKEN` or the full webhook URL** in chat, logs, issues, or PRs.

## Common Workflows

- **Post a one-off message**: `post --text "..."` is the default — works with either mode.
- **Reply in a thread**: `post-message --thread-ts <ts>` — needs Web API (bot token).
- **Notify a specific person**: look up their user id by email (`lookup-user`), then mention via `<@USER_ID>` in the message text.
- **Wire from CI / hooks**: webhook mode is often the simplest — no scopes, no OAuth.

## What this skill doesn't do

- **It doesn't subscribe to events.** Posting only. For a polling or push-based listener, deploy a separate service.
- **It doesn't store conversation history.** Each call is stateless.
- **It doesn't bypass workspace policies.** If your bot lacks scope on a channel, `chat.postMessage` returns `not_in_channel` — invite the bot or use a different channel.

## References

Read `references/api-reference.md` when you need scope requirements, error code meanings, Block Kit notes, or how to mint a bot token.
