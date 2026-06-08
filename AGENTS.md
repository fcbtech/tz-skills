# AGENTS.md

This repository maintains TranZact organization skills. Treat each skill as a compact, executable operating guide for agents, not as user-facing documentation.

## Source Of Truth

- Follow the official Agent Skills specification: https://agentskills.io/specification.
- If specification details are uncertain or may have changed, verify the live specification before editing.
- Keep `README.md` aligned with this file when repository workflow expectations change.

## Skill Structure

Every skill must live in its own directory and include a required `SKILL.md`.

```text
skill-name/
|-- SKILL.md
|-- scripts/
|-- references/
`-- assets/
```

- `SKILL.md` must begin with YAML frontmatter followed by Markdown instructions.
- Required frontmatter:
  - `name`: lowercase letters, numbers, and hyphens only; 1-64 characters; no leading, trailing, or consecutive hyphens; must match the parent directory name.
  - `description`: 1-1024 characters; clearly state what the skill does and when to use it.
- Optional frontmatter may include `license`, `compatibility`, `metadata`, and `allowed-tools` when genuinely useful.
- Use `scripts/` for deterministic executable workflows.
- Use `references/` for detailed documentation that should be loaded only when needed.
- Use `assets/` for templates, images, data files, and other output resources.

## Authoring Rules

- Design for progressive disclosure: keep metadata highly specific, keep `SKILL.md` concise, and move detailed material into focused reference files.
- Keep `SKILL.md` under 500 lines unless there is a strong reason.
- Do not add extra documentation files inside skill directories unless the specification or an actual workflow needs them.
- Reference bundled files from `SKILL.md` with paths relative to the skill root, for example `references/API.md` or `scripts/validate.py`.
- Keep reference chains shallow; files referenced from `SKILL.md` should be directly useful.
- Prefer scripts for fragile, repetitive, or validation-sensitive operations.
- Scripts should be self-contained, document dependencies, produce helpful errors, and handle edge cases.
- Do not duplicate the same instructions across `SKILL.md` and `references/`; put the operational summary in `SKILL.md` and the detailed material in `references/`.

## Validation

- After changing a skill, validate its structure against the Agent Skills rules.
- If `skills-ref` is available, run:

```sh
skills-ref validate ./skill-name
```

- For repo-wide checks, validate every skill directory that contains a `SKILL.md`.
- After code changes, run the repo linter or type checker and fix type errors before finishing. If no linter/type checker exists, state that explicitly.

## Repository Workflow

- Use `rg` for searching through files.
- Use `apply_patch` for manual edits.
- Preserve unrelated local changes.
- If interacting with GitHub, always use the GitHub CLI (`gh`).
- Keep edits scoped to the requested skill or repository governance file.
- Before adding a new convention, check whether it belongs in `AGENTS.md`, `CLAUDE.md`, `README.md`, or the individual skill.
