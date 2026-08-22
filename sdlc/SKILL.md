---
name: sdlc
description: Run TranZact SDLC operations against fcbtech/pm and GitHub Project #86 — create epics/tasks/bugs with the correct Issue Type, Subtype, and fields, and open PRs that drive the board lifecycle (Status, Review State). Use whenever a dev is filing a task/bug/epic, starting work, or opening/marking-ready a PR for TranZact.
---

# sdlc

Performs SDLC GitHub operations per the [SDLC SOP](https://outline.letstranzact.com/doc/sdlc-sop-GWLmvtHEK1).

- All work items live in **`fcbtech/pm`**; implementation PRs live in their code repo and reference `fcbtech/pm#N`.
- TranZact PRs target epic/`develop` branches, **not** the default branch, so GitHub's `closes`/`fixes` keywords do **not** update the issue. This skill writes the Project fields **explicitly** instead.
- Two field scopes (handled automatically): org issue fields (`Subtype`, `Priority`, `Release Stage`, `Story Points`, `Dev Start`, …) via `setIssueFieldValue`; project-scoped fields (`Status`, `Review State`, `QA State`) via `updateProjectV2ItemFieldValue`.

## Setup

1. Authenticate `gh` (`gh auth status`) with `project` + `repo` scope.
2. Cache board node-ids: `python scripts/sdlc_helper.py sync-fields` (re-run after any board schema change).
3. For `create-task` subtype auto-fill, export the role map:
   `export SDLC_ROLES='{"dev":["login1","login2"],"qa":["login3"]}'` (or pass `--subtype` explicitly).

## Operations

```
sync-fields                                              # refresh references/field-ids.md
create-epic  --title T [--owner LOGIN --priority P]
create-task  --title T (--subtype dev|qa | --assignee LOGIN) [--epic N --priority P --points 1|2|4|8]
create-bug   --title T --subtype qa-bug|production-bug|suggestion [--epic N --priority P]
start  <pm#>                                             # Status=WIP, Dev Start=today, assign self
open-pr <pm#> --repo R --head H --base B --title "feat: ..."   # draft PR -> Review State=Waiting for Review
ready  <pm#> --repo R <pr#>                              # PR ready -> Review State=In Review
```

`P` ∈ `Urgent|High|Medium|Low`.

## Rules the agent must enforce

- When the user names a parent epic for a task or bug — in any phrasing (`for epic #N`, `sub-issue of #N`, `under #N`, `child of #N`, `belongs to epic #N`, etc.) — pass `--epic N` to `create-task` / `create-bug`. **Do not** put `Parent: #N` (or any parent reference) into `--body`: a body string only produces a cross-reference event, not a sub-issue link. `--epic` is what invokes the GitHub `sub_issues` API and creates the real parent/child relationship the board reads.
- PR titles start with an investment prefix (`feat:`/`fix:`/`chore:`/`dx:`/`perf:`/`refactor:`/`debt:`/`infra:`/`sec:`/`docs:`/`test:`/`ci:`/`automation:`/`maint:`).
- `task` Subtype is `dev` or `qa`; `bug` Subtype is `qa-bug`/`production-bug`/`suggestion`; `epic` has no subtype.
- Feature branches: `epic-<epic-slug>/<task-slug>`, branched off the epic branch.
- **Never** mark a `bug` Done on merge — QA owns that transition (handled by the Actions companion / v2).

## Not yet in v1

QA-state flow (`qa-start/pass/fail` + evidence), `done` + Definition-of-Done enforcement, and the event-driven automation (PR-merge → fields, deploy → Release Stage, PR-link check) which is the separate Actions companion. In v1 a dev drives those fields via the ops above.
