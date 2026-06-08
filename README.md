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

## Agent Instructions

- `AGENTS.md` contains repo instructions for Codex-style agents.
- `CLAUDE.md` contains repo instructions for Claude-style agents.
- Keep both files aligned when changing repository-wide skill maintenance rules.
