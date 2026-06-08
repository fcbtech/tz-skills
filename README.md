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

Replace `freshdesk` with another skill directory name, such as `outline`, `newrelic`, or `keka`, to install a different skill.

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

## Agent Instructions

- `AGENTS.md` contains repo instructions for Codex-style agents.
- `CLAUDE.md` contains repo instructions for Claude-style agents.
- Keep both files aligned when changing repository-wide skill maintenance rules.
