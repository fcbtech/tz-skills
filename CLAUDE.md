# CLAUDE.md

This repository contains TranZact organization skills. When working here, follow the official Agent Skills specification and keep skills optimized for agent use.

## Required Reference

- Specification: https://agentskills.io/specification
- Verify the live specification when you are unsure about a field, limit, validation rule, or directory convention.

## Skill Requirements

- Each skill is a directory with a required `SKILL.md`.
- `SKILL.md` must contain YAML frontmatter and Markdown body content.
- Frontmatter requirements:
  - `name` is required, must match the parent directory, and must use only lowercase letters, numbers, and hyphens.
  - `description` is required and should explain both what the skill does and when to use it.
- Optional directories:
  - `scripts/` for executable code.
  - `references/` for detailed documentation loaded on demand.
  - `assets/` for templates, static resources, or output materials.

## Writing Guidance

- Keep the main `SKILL.md` concise and operational.
- Put detailed domain notes, examples, schemas, and API references in `references/`.
- Put deterministic or fragile repeatable procedures in `scripts/`.
- Use relative paths from the skill root when linking to bundled files.
- Avoid deeply nested reference chains.
- Do not create extra docs inside skill folders unless they are directly needed by the skill.
- Descriptions are activation metadata; write them with specific task keywords.

## Checks Before Finishing

- Validate changed skills with `skills-ref validate ./skill-name` when the tool is available.
- Run the available repo linter/type checker after code changes and fix type errors.
- Use `rg` for search.
- Use `gh` for GitHub issues, PRs, and other GitHub interactions.
- Do not overwrite unrelated local changes.

