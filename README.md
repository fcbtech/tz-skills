# tz-skills

TranZact organization skills live in this repository. A skill is a small, focused package of instructions, scripts, references, and assets that helps an AI agent perform a specific workflow reliably.

## Specification

This repo follows the official Agent Skills specification:

https://agentskills.io/specification

Before adding or changing skill structure, verify the current specification. The key rules are:

- Every skill is a directory with a required `SKILL.md`.
- `SKILL.md` starts with YAML frontmatter followed by Markdown instructions.
- `name` and `description` are required frontmatter fields.
- `name` must match the parent directory and use only lowercase letters, numbers, and hyphens.
- `description` should clearly state what the skill does and when an agent should use it.
- Optional directories are `scripts/`, `references/`, and `assets/`.

## Recommended Layout

```text
skill-name/
|-- SKILL.md          # Required: metadata and operating instructions
|-- scripts/          # Optional: executable workflows
|-- references/       # Optional: detailed docs loaded only when needed
`-- assets/           # Optional: templates, images, data, and other resources
```

## Authoring Principles

- Keep each skill focused on one job or closely related workflow.
- Optimize for progressive disclosure: metadata first, concise `SKILL.md`, then optional resources.
- Keep `SKILL.md` short and action-oriented; move long examples, schemas, and background material into `references/`.
- Use `scripts/` for repeatable workflows where deterministic behavior matters.
- Use `assets/` for files that support final outputs rather than instructions.
- Avoid extra files such as per-skill README files unless they are directly part of the agent workflow.

## Validation

When `skills-ref` is installed, validate changed skills with:

```sh
skills-ref validate ./skill-name
```

For a repo-wide check, validate every directory that contains a `SKILL.md`.

## Installing One Skill

Each top-level folder with a `SKILL.md` is an installable skill. For example, `freshdesk/`, `outline/`, `newrelic/`, and `keka/` can be installed independently.

First clone the repository:

```sh
git clone https://github.com/fcbtech/tz-skills.git
cd tz-skills
```

Install one skill for Claude Code:

```sh
mkdir -p ~/.claude/skills
cp -R freshdesk ~/.claude/skills/freshdesk
```

Install one skill for Codex:

```sh
mkdir -p ~/.codex/skills
cp -R freshdesk ~/.codex/skills/freshdesk
```

Replace `freshdesk` with another skill directory name, such as `outline`, `newrelic`, `keka`, `mysql`, `slack`, or `oncall-agent`, to install a different skill.

## Bulk Install (Recommended for Claude Code)

To install every skill in this repo into `~/.claude/skills/` as symlinks so `git pull` instantly updates them:

```sh
bin/install-symlinks.sh                 # link every skill
bin/install-symlinks.sh mysql freshdesk # link a subset
bin/install-symlinks.sh --dry-run       # preview without changes
```

The installer is idempotent and refuses to overwrite an existing real directory at the destination (you'll get a `warn:` line for that skill — back up or remove the existing directory yourself if you want to switch to the repo version).

After installing, restart Claude Code or Codex so the new skill is discovered. Then invoke the skill directly by name or ask naturally:

```text
/freshdesk
List Freshdesk tickets assigned to me.
```

If the skill uses credentials, set them in the environment or run that skill's setup helper after copying it. Do not commit `.env` files or API tokens.

## Outline Skill Setup

The `outline/` skill is configured for the TranZact Outline instance at:

```text
https://outline.letstranzact.com
```

To use it locally or from an agent runtime:

1. Create a personal Outline API token from Outline settings.
2. Export the token in the shell or agent environment:

```sh
export OUTLINE_API_TOKEN="<outline-api-token>"
```

3. If the token is not present, run the setup command and paste it when prompted:

```sh
python outline/scripts/outline_helper.py setup
```

The setup command saves credentials to `outline/scripts/.env` with local-only file permissions. That file is ignored by git and must not be committed.

4. Install or copy the `outline/` directory into the agent's skills directory.
5. Use the helper script for common operations:

```sh
python outline/scripts/outline_helper.py list-collections
python outline/scripts/outline_helper.py search "payments"
python outline/scripts/outline_helper.py read DOCUMENT_UUID
```

Do not commit personal API tokens. The repo may contain the shared Outline URL, but authentication must stay in environment variables or the user's local secret store.

## Freshdesk Skill Setup

The `freshdesk/` skill works with Freshdesk API v2 for `tranzact.freshdesk.com`. It needs a Freshdesk API key.

Configuration is read from environment variables first. The domain is optional and defaults to `tranzact.freshdesk.com`:

```sh
export FRESHDESK_API_KEY="<freshdesk-api-key>"
```

If the key is not present, run the setup command and paste it when prompted:

```sh
python freshdesk/scripts/freshdesk_helper.py setup
```

The setup command saves credentials to `freshdesk/scripts/.env` with local-only file permissions. That file is ignored by git and must not be committed.

Common helper commands:

```sh
python freshdesk/scripts/freshdesk_helper.py me
python freshdesk/scripts/freshdesk_helper.py list-tickets --page 1 --per-page 30
python freshdesk/scripts/freshdesk_helper.py get-ticket 123
python freshdesk/scripts/freshdesk_helper.py search-tickets "status:2 AND priority:1"
```

## New Relic Skill Setup

The `newrelic/` skill uses the New Relic CLI for diagnostics, NRQL queries, entity search, APM inspection, workloads, synthetics, and NerdGraph.

Install and configure the New Relic CLI before using the skill. The skill does not store New Relic credentials in this repository.

Create a local New Relic CLI profile:

```sh
newrelic profile add --profile tranzact --apiKey <new-relic-user-key> --accountId <account-id> --region US
newrelic profile default --profile tranzact
```

Use `--region EU` instead if the account is in the EU region. Do not commit New Relic API keys, license keys, or generated CLI config.

Common commands the skill will run:

```sh
newrelic nrql query --accountId <account-id> --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago'
newrelic entity search --name "production-api"
newrelic apm application search --name "my-app"
newrelic nerdgraph query 'query { actor { user { email } } }'
```

## Keka Skill Setup

The `keka/` skill works with Keka HRMS APIs for Core HR, attendance, payroll, hire, expense, leave, reporting, exports, and API troubleshooting.

Keka uses OAuth-style token authentication. Configure credentials in the local shell or agent environment:

```sh
export KEKA_SUBDOMAIN="<tenant-subdomain>"
export KEKA_ENV="keka"
export KEKA_CLIENT_ID="<client-id>"
export KEKA_CLIENT_SECRET="<client-secret>"
export KEKA_API_KEY="<api-key>"
```

Alternatively, run the setup helper and paste the values when prompted:

```sh
python keka/scripts/keka_helper.py setup
```

The setup command saves credentials to `keka/scripts/.env` with local-only file permissions. That file is ignored by git and must not be committed.

Use `KEKA_ENV="kekademo"` for sandbox/demo tenants. Do not commit Keka client secrets, API keys, access tokens, exports, or employee/payroll data.

The public Keka API docs are at:

```text
https://apidocs.keka.com/
https://developers.keka.com/docs/getting-started-with-keka-apis
```

## MySQL Skill Setup

The `mysql/` skill talks to MySQL via the `mysql` CLI using **named profiles** stored as `[client]` cnf files in `~/.tz-oncall/<profile>.cnf` (mode 0600). It auto-discovers any profile present in that directory.

The skill ships a **table-name guardrail**: before any query is sent, the helper extracts identifiers after `FROM`/`JOIN`/`UPDATE`/`INSERT INTO`/`DELETE FROM` and validates them against `information_schema.tables` (24h-cached per profile at `~/.tz-oncall/schema-cache/<profile>.json`). Unknown tables fail loud with a difflib suggestion (`unknown table 'auth_users'; did you mean: auth_user`).

Create or replace a profile interactively:

```sh
python mysql/scripts/mysql_helper.py setup --profile tz-prod-read-replica
```

Or write a cnf manually (typical for the TranZact on-call set):

```sh
mkdir -p ~/.tz-oncall && chmod 700 ~/.tz-oncall
cat <<'EOF' > ~/.tz-oncall/tz-prod-read-replica.cnf
[client]
host=<DB_READ_REPLICA_HOST>
user=<DB_READ_REPLICA_USER>
password=<DB_READ_REPLICA_PASSWORD>
database=<DB_READ_REPLICA_NAME>
EOF
chmod 600 ~/.tz-oncall/tz-prod-read-replica.cnf
```

Common helper commands:

```sh
python mysql/scripts/mysql_helper.py list-profiles
python mysql/scripts/mysql_helper.py run --profile tz-prod-read-replica --sql 'SELECT 1'
python mysql/scripts/mysql_helper.py run --profile mstag-dmz --file path/to/query.sql --var USER_ID=42

# Mandatory dry-run before any write; writes only on mstag-dmz.
python mysql/scripts/mysql_helper.py dry-run-write \
    --profile mstag-dmz \
    --pre-select  "SELECT id, is_active FROM auth_user WHERE id = 12345" \
    --post-select "SELECT id, is_active FROM auth_user WHERE id = 12345" \
    --sql         "UPDATE auth_user SET is_active = 1 WHERE id = 12345"
```

Force-refresh the schema cache after a table rename:

```sh
python mysql/scripts/mysql_helper.py schema-refresh --profile mstag-dmz
```

Unit tests for the guardrail:

```sh
python mysql/tests/test_guardrail.py -v
```

Never commit cnf files. They live in `~/.tz-oncall/` outside the repo and are written with mode 0600 by the helper.

## Slack Skill Setup

The `slack/` skill posts messages and runs lookups against Slack via either an incoming webhook OR the Web API. The helper auto-detects which mode to use:

| Variable | Mode | Capability |
|----------|------|------------|
| `SLACK_BOT_TOKEN` (`xoxb-...`) | Web API | Threading, mentions, channel/user lookup |
| `SLACK_WEBHOOK_URL` | Incoming webhook | Single channel, plain text + Block Kit only |
| `SLACK_DEFAULT_CHANNEL` | Either | Default channel for `post` when `--channel` is omitted |

Configure interactively (prompts for whichever values you want):

```sh
python slack/scripts/slack_helper.py setup
```

The setup command saves credentials to `slack/scripts/.env` with mode `0600`. That file is ignored by git and must not be committed.

Common helper commands:

```sh
# Auto mode — bot token if set, else webhook
python slack/scripts/slack_helper.py post --channel "#oncall" --text "found a stuck IAP"

# Threaded reply (Web API only)
python slack/scripts/slack_helper.py post-message --channel "#oncall" --text "follow-up" --thread-ts 1700000000.000100

# Lookups
python slack/scripts/slack_helper.py lookup-user dev@letstranzact.com
python slack/scripts/slack_helper.py lookup-channel oncall
```

How to mint credentials, scope requirements, and Block Kit reference live in `slack/references/api-reference.md`.

## On-Call Agent Skill Setup

The `oncall-agent/` skill is the TranZact on-call investigation **orchestrator**. It composes the `freshdesk`, `newrelic`, and `mysql` skills under a TZ-specific discipline (mandatory customer-id resolution, polyrepo routing, transactional dry-run protocol, constrained output shape).

Prerequisites — install and configure these sibling skills first:

1. `freshdesk` — for ticket fetch.
2. `newrelic` — for NRQL.
3. `mysql` — for replica + mstag-dmz access. **The dry-run protocol lives here.**
4. `slack` — *optional*. Required only if you want investigation results posted to Slack via `notify-investigation.sh --notify`.

After siblings are configured, install this skill:

```sh
bin/install-symlinks.sh oncall-agent
```

The skill auto-activates when a prompt contains the literal header `# Freshdesk Ticket Context`, or when invoked manually as `/oncall <description-or-ticket-ref>`.

Ready-to-run composite scripts:

```sh
oncall-agent/scripts/fetch-ticket-context.sh <ticket-id>
oncall-agent/scripts/user-impersonation-lookup.sh --user-id <id>    # or --email
oncall-agent/scripts/customer-state-snapshot.sh --company-id <id>
```

These delegate to `mysql/scripts/mysql_helper.py` and `freshdesk/scripts/freshdesk_helper.py`, so the table-name guardrail and the credential management stay in one place.

The skill's `references/sql/` ships four parameterized SQL templates (`auth-user-by-id.sql`, `auth-user-by-email.sql`, `user-profile-across-companies.sql`, `company-overview.sql`) — broadly-applicable lookups distilled from past investigations. TZ domain constants (document type codes, the `expired=1` orphan marker, `IAP######` approval-id format, BINARY case-sensitive matching) are documented in `references/domain-constants.md`.

## Sessions Digest (optional crons)

Two local launchd jobs that read your Claude Code transcripts, ask Claude to summarize what you've been working on, and DM the summary to Slack. **macOS only** — uses `launchd`.

| Job | When | What it sends |
|-----|------|---------------|
| `com.tranzact.sessions-digest` | every 2h, 08:00–20:00 local | **incremental** — tight one-sentence bullets (≤25 words) of what was *achieved* in the last 2h, each tagged `repo · branch · PR` |
| `com.tranzact.sessions-scrum` | weekdays 09:55 local | a standup (done / in progress / blockers) built by **aggregating that day's stored 2h updates** (yesterday + today so far; Mon reaches back to Fri) |

Both run `~/bin/claude-digest.py`, which: scans `~/.claude/projects/*/*.jsonl`, **redacts secret-looking strings** (Slack/GitHub/API tokens) before anything leaves the machine, asks `claude --print --model claude-sonnet-4-6` for **structured JSON**, and posts a **Slack Block Kit** message (header + per-item section + muted `📦 repo · 🌿 branch · 🔀 PR` context line) via the `slack` skill. If the JSON ever fails to parse, it degrades to plain text so delivery never breaks.

**Incremental windowing.** The 2h digest filters by each message's `timestamp`, so a session running across several windows only contributes its *newly-added* messages each time — no re-summarizing old work. The summarizer sees both your prompts **and** Claude's responses (insight blocks + meaningful conclusions), so bullets reflect what was actually *done*, not just what you asked. `repo`/`branch` come from the transcript's per-line `cwd`/`gitBranch`; PRs from `/pull/NNNN` and `PR #NNNN` mentions.

**History store.** Each 2h run appends its structured items to `~/.tz-oncall/digest-history/YYYY-MM-DD.jsonl`. The weekday scrum reads those entries for its window and synthesizes them — a summary-of-summaries that's tight and cheap. If the store is empty for the window (e.g. day one), the scrum falls back to re-reading the raw transcripts. Daily files older than 7 days are auto-pruned on each run.

### Requirements

- `python3` on PATH
- The `claude` CLI (Claude Code) installed and logged in — the crons invoke it to summarize
- The `slack/` skill configured with a **bot token** + `SLACK_DEFAULT_CHANNEL` (your Slack user id for a self-DM, or a channel). See "Slack Skill Setup" above. Delivery reads `~/.claude/skills/slack/scripts/.env`.

### Install

```sh
bin/install-sessions-digest.sh             # installs BOTH jobs
bin/install-sessions-digest.sh --dry-run   # preview without changes
```

Empty windows are silently skipped (Claude isn't even called), so you only get pinged when there was real activity — and you don't pay for empty summaries.

### Test manually

```sh
~/bin/claude-digest.py --mode recent --hours 2 --dry-run   # show the prompt, no Claude/Slack
~/bin/claude-digest.py --mode recent --hours 2 --notify    # incremental 2-hour digest → Slack
~/bin/claude-digest.py --mode scrum            --notify    # standup (computes its own window) → Slack
```

`recent-sessions-digest.py` is also installed as a zero-cost (no-LLM) raw fallback if you ever want the plain transcript digest without a Claude call.

### Logs

`~/Library/Logs/sessions-digest.{log,err.log}` and `~/Library/Logs/sessions-scrum.{log,err.log}`

### Uninstall

```sh
bin/uninstall-sessions-digest.sh   # removes both jobs
```

### Roadmap — autonomous polling agent (future)

The current orchestrator is **reactive**: it activates when a human pastes a `# Freshdesk Ticket Context` block or types `/oncall`. The long-term vision is an **autonomous polling agent** that watches Freshdesk for tech tickets, investigates each one via this skill, and notifies a developer in Slack — optionally raising a draft PR.

Architecture is captured in `oncall-agent/references/polling-loop.md`. It is **not implemented in this PR**. Planned follow-ups:

1. `oncall-poller/` skill — Python daemon (launchd / systemd / cron-driven) that polls Freshdesk, filters by group/tag, dedupes against state, invokes Claude headlessly, calls `notify-investigation.sh --notify`.
2. Autonomous draft-PR raising — opt-in per-repo, draft-only, never auto-merge. Riskier; lands last.

## Agent Instructions

- `AGENTS.md` contains repo instructions for Codex-style agents.
- `CLAUDE.md` contains repo instructions for Claude-style agents.
- Keep both files aligned when changing repository-wide skill maintenance rules.
