# SDLC Automation: `sdlc` dev skill + thin Actions companion — Design

- **Date:** 2026-06-19
- **Status:** Draft for review
- **Author:** drafted with Claude (EM-driven brainstorming)
- **Canonical home (proposed):** a `fcbtech/pm` `epic` issue brief (per SOP §1) + this doc mirrored into `fcbtech/tz-skills`
- **Source of truth:** [SDLC SOP](https://outline.letstranzact.com/doc/sdlc-sop-GWLmvtHEK1) and [Branch Management](https://outline.letstranzact.com/doc/branch-management-9lGQhLsXXP)

---

## 1. Problem & goals

The team wants GitHub Project **#86 ("Engineering", org `fcbtech`)** to reflect real SDLC truth with minimal manual upkeep. Three pains drove this, all of which the SOP now formalizes:

1. **Done ≠ Deployed** — `Done` was used for merged code that wasn't shipped. → solved by a `Release Stage` field driven off deploys.
2. **Multi-repo issues** — one work item spans 2–5 repos; status must reflect all of them. → solved by centralizing issues in `fcbtech/pm` and updating Project fields explicitly.
3. **Bug "whose court"** — Dev vs QA ownership. → solved by `Review State` + `QA State`.

**Primary deliverable (decided):** a **dev-facing skill** (`sdlc`) in `fcbtech/tz-skills` that performs the correct GitHub operations so devs don't have to memorize the SOP — create work items, open PRs, drive lifecycle fields. **Secondary:** a thin GitHub Actions companion for the event-driven transitions a dev can't invoke by hand.

Non-goals for v1: QA-state flow, Done/DoD enforcement, issue/PR templates, evidence enforcement, production-bug template (all v2 — see §11).

## 2. Load-bearing finding (validated empirically 2026-06-18)

We ran a sandbox experiment (repos `zz-sandbox-pm`, `zz-sandbox-code-a/-b`, plus one cross-repo test against `tz-core`). A 2×2 matrix (same/cross-repo × default/non-default base) plus merge tests proved a single rule:

> **GitHub closing keywords (`closes`/`fixes`/`resolves`) — for both `closingIssuesReferences` linking AND auto-close — only fire when the PR targets the repository's *default* branch.** Cross-repo vs same-repo is irrelevant.

| | base = default (`main`) | base = non-default (`develop`) |
|---|---|---|
| same-repo | link ✓, auto-close ✓ | link ✗, auto-close ✗ |
| cross-repo | link ✓, auto-close ✓ | link ✗, auto-close ✗ |

The TranZact gitflow merges feature PRs into **epic branches / `develop`**, never the default branch. **Therefore native PR↔issue linking and auto-close are unusable for this workflow.** This is now codified in SOP §3.5.

**Design consequence:** automation/skill **must not** rely on `closingIssuesReferences`. It parses the `fcbtech/pm#N` reference itself and **updates the pm Project fields explicitly**. Also validated: cross-repo **sub-issues** work fully (with progress rollup), and the org PAT can perform all Project v2 GraphQL mutations against #86 (add item, set single-select, delete item).

## 3. Architecture

```
Dev (terminal) ──invokes──> sdlc skill (tz-skills/sdlc)
                               │  gh + GraphQL
                               ▼
                         fcbtech/pm issue  ───item───>  Project #86 fields
                               ▲                              ▲
   impl-repo PR (Fixes fcbtech/pm#N) ─┘                       │
                                                              │
   GitHub Actions companion (impl repos) ──deploy/merge events──┘
```

- **`fcbtech/pm`** is the single home for all SDLC issues (SOP §1, §5). Implementation repos hold only code + PRs.
- **`sdlc` skill** is the dev's entry point for everything dev-initiated; it writes Project fields explicitly (immune to the default-branch gate).
- **Thin Actions companion** handles only event-driven transitions (deploy, merge) + the PR-link enforcement check.
- **Board #86** is the lifecycle surface; `Status` is the simple source of truth, with `Review State` / `QA State` / `Release Stage` as secondary markers.

## 4. Field / schema model

### 4.1 Current live state of board #86 (already applied by the team)
- ✅ `Status`: `Backlog, Todo, WIP, Paused, Done, Cancelled`
- ✅ `Review State`: `Not Required, Waiting for Review, In Review, Changes Requested, Approved, Merged`
- ✅ `QA State`: `Not Required, Ready for QA, QA In Progress, QA Passed, QA Failed`
- ✅ Date fields: `Dev Start, Dev End - Expected, Dev End - Actual, QA Start, QA End - Expected, QA End - Actual, UAT Start, UAT End, Release Expected, Release Actual, Due Date`
- ✅ `Size`/`Estimate` removed in favor of a `Story Points` single-select

### 4.2 Org-level issue fields (DONE — set at the organization, not the project)
These four are **org-level issue fields** (`organization.issueFields` → `IssueFieldSingleSelect`), reusable across projects, with options already configured:
| Field (org-level) | Options (live) |
|---|---|
| `Priority` | `Urgent, High, Medium, Low` |
| `Subtype` | `dev, qa, bug, production-bug, suggestion` |
| `Release Stage` | `Canary Candidate, In Canary, In Prod` |
| `Story Points` | `1, 2, 4, 8` |

**Mixed field scopes — VALIDATED mechanics (two paths).** The board uses two field scopes; the skill/Actions must handle both:

| Field group | Set via | Read via | Needs |
|---|---|---|---|
| Project-scoped: `Status`, `Review State`, `QA State` | `updateProjectV2ItemFieldValue` (proven T5) | project item `fieldValueByName` | project-item id + project field id + option id |
| Org issue fields: `Priority`, `Subtype`, `Release Stage`, `Story Points` | `setIssueFieldValue` (proven 2026-06-19) | `issue.issueFieldValues` | issue node-id + org field id + option id |

`setIssueFieldValue(input:{issueId, issueFields:[{fieldId, singleSelectOptionId}]})` was validated end-to-end (set Subtype=dev + Priority=High, read back via `issue.issueFieldValues`). Org fields are **issue-scoped** — settable at issue creation, no board membership required, and reflected across all projects.

**Confirmed limitation:** org issue-field values do **not** surface on the Projects v2 *item* API (`ProjectV2Item.fieldValues`/`fieldValueByName` returns empty for them — only project-scoped fields appear). The board UI joins them from the issue for display, but automation **cannot** query/filter project items by these org fields — it must enumerate issues and read `issueFieldValues`. Our design only ever sets org fields on *specific* issues resolved from PR/deploy events, so it is unaffected; flagged for any future project-wide reporting (§8).

**Naming drift to reconcile:** SOP §5.1 lists the QA bug subtype as `qa-bug`, but the live org `Subtype` field uses `bug`. Pick one (rename the org option to `qa-bug`, or update the SOP) so the skill encodes a single canonical value.

### 4.3 Issue Types (org-level, `fcbtech/pm`)
Configure exactly three native GitHub Issue Types: **`epic`, `task`, `bug`** (SOP §5.1). The `Subtype` Project field disambiguates within `task` (`dev`/`qa`) and `bug` (`qa-bug`/`production-bug`/`suggestion`).

### 4.4 Existing-item migration (52 items)
`Status` options were already replaced. Items previously on `In progress`/`In Review`/`In Testing` need remap:
- `In progress → WIP` (team confirmed WIP ≡ In progress)
- `In Review → WIP` + `Review State = In Review`
- `In Testing → WIP` + `QA State = QA In Progress`

**Action:** a one-off `sdlc_helper.py migrate-status --dry-run` that reads all 52 items, prints the old→new diff, and applies only on `--apply`. Verify no item is left with a null `Status` after the options were swapped.

## 5. The `sdlc` skill

### 5.1 Home & layout
`fcbtech/tz-skills/sdlc/` following the org convention (cf. `freshdesk`, `keka`, `outline`):
```
sdlc/
├── SKILL.md                 # name: sdlc; description: when/how to run SDLC ops
├── scripts/
│   └── sdlc_helper.py       # gh + Project v2 GraphQL; one subcommand per op
└── references/
    └── field-ids.md         # cached Project/field/option node-ids for #86
```
`SKILL.md` encodes the SOP rules (type/subtype, status, review/QA states, branch naming, investment prefixes) so the agent applies them; `sdlc_helper.py` does the deterministic GitHub mutations.

### 5.2 v1 operations (confirmed scope)
**Create ops** (write to `fcbtech/pm`, add to Project #86, set fields):
- `create-epic --title --owner [--priority]` → Issue Type `epic`; `Status=Backlog` (or `Todo`); owner; priority.
- `create-task --title --subtype dev|qa --assignee [--epic <pm#>] [--priority --points]` → Issue Type `task`; `Subtype` (auto-resolved from the role map when `--assignee` is given and `--subtype` omitted); link as sub-issue of `--epic`.
- `create-bug --title --subtype qa-bug|production-bug|suggestion [--epic --priority]` → Issue Type `bug`; `Subtype` required (assignee can't disambiguate).

**Dev flow:**
- `start <pm#>` → `Status=WIP`, set `Dev Start` (today), assignee = caller; create the impl-repo **feature branch off the epic branch** per naming (`epic-<x>/<slug>`).
- `open-pr <pm#> --repo <impl-repo> [--base <epic-branch>]` → create a **draft** PR whose body references `fcbtech/pm#N` and carries the investment prefix (`feat:`/`fix:`/…); record the PR URL on the pm issue; set `Review State=Waiting for Review`, ensure `Status=WIP`. **Does not rely on `closes`** — the explicit field write is the signal (SOP §3.5).
- `ready <impl-repo#pr>` → flip PR ready-for-review; set `Review State=In Review`.

- `status <pm#>` (read-only helper) → print the issue's current Project field values.

### 5.3 Mechanics
- Resolve PR `<repo>#<n>` → `fcbtech/pm#N` by parsing the PR body for `fcbtech/pm#\d+` (base-branch-agnostic).
- Field writes use the two validated paths (§4.2): `setIssueFieldValue` for org fields (`Subtype`/`Priority`/`Release Stage`/`Story Points`, issue-scoped) and `addProjectV2ItemById` + `updateProjectV2ItemFieldValue` for project-scoped fields (`Status`/`Review State`/`QA State`). All project/field/option node-ids cached in `references/field-ids.md`, refreshable via a `sync-fields` subcommand.
- **Role map** for subtype auto-fill: `fcbtech/pm/.github/sdlc-roles.yml` (`dev: [...]`, `qa: [...]`). Ambiguous/missing → the skill prompts the dev to pass `--subtype` rather than guessing.

## 6. Thin Actions companion

Lives as **reusable workflows** in a central repo (`fcbtech/.github` or `fcbtech/pm`), called by each impl repo. Three jobs:

1. **PR lifecycle → pm fields** (`on: pull_request` + `pull_request_review`): parse `fcbtech/pm#N` from the PR; set `Review State` (`Waiting for Review`→`In Review`→`Changes Requested`→`Approved`→`Merged`) per SOP §5.4. On **merge into an epic branch**: `Review State=Merged`; for `task/dev` → `Status=Done`; for `bug/qa-bug|production-bug` → `QA State=Ready for QA`, keep `Status=WIP` (SOP §3.5, §5.3). Runs reliably even when merges happen in the GitHub UI.
2. **Deploy → Release Stage** (`on: deployment_status` / branch deploy of `canary` and `main`): `develop→canary` deploy success → `Release Stage=In Canary`; `canary→main` deploy success → `In Prod`; merge onto release branch pre-deploy → `Canary Candidate` (SOP §5.8). Resolves affected pm issues from the merged PRs' `fcbtech/pm#N` references in the deployed range.
3. **PR-link enforcement check** (`on: pull_request`): fail/warn when a non-trivial PR has no valid `fcbtech/pm#N` reference; record the PR URL on the pm issue when present (SOP §6.6).

**Token:** an org **fine-grained PAT** (Projects: write, Issues: write, Contents: read) stored as an org Actions secret; the skill uses the dev's own `gh` auth. Documented migration path to a GitHub App later.

## 7. Testing strategy

- Sandbox repos already exist and are **kept live**: `fcbtech/zz-sandbox-pm`, `-code-a`, `-code-b` (no CI → safe for end-to-end skill/Action runs).
- Pure logic (PR→issue reference parsing, status/role mapping, deploy-range resolution) extracted into unit-tested functions.
- All mutating subcommands support `--dry-run` (log intended GraphQL writes) before `--apply`.
- Validate Action transitions against the sandbox board before pointing real repos at the reusable workflow.

## 8. Risks & open questions

- **Org fields invisible to project-item queries** (validated §4.2) — project-wide reporting/filtering by `Priority`/`Subtype`/`Release Stage`/`Story Points` must iterate issues, not query project items. Fine for our event-driven writes; constrains future dashboards.
- **Subtype naming** — SOP §5.1 says `qa-bug`; live org field uses `bug`. Reconcile to one canonical value before encoding in the skill.
- **Role-map drift** — `sdlc-roles.yml` must be maintained or subtype auto-fill degrades to prompts. Acceptable fallback.
- **Deploy-range → pm issue resolution** for Release Stage relies on PRs carrying `fcbtech/pm#N`; the §6 PR-link check is the enforcement that makes this reliable. Order matters: ship the link check early.
- **Epic-branch naming** must be parseable by both skill and Actions; SOP §6 flags standardizing branch prefixes — confirm `epic-<x>/<slug>`.
- **Spec home** — to be confirmed: `fcbtech/pm` epic issue (canonical per SOP) vs `tz-skills` repo docs.

## 9. Sequencing (for the implementation plan)

1. **Board option config + Issue Types + item migration** (§4.2–4.4) — small, unblocks everything.
2. **`sdlc` skill v1** — create ops, then dev flow (`start`/`open-pr`/`ready`); dogfood on sandbox.
3. **Actions companion** — PR-link check first, then PR-lifecycle, then deploy→Release Stage.
4. Roll out to one real impl repo (e.g. `tz-core`) behind the sandbox-validated workflow, then the rest.

## 10. Traceability to SOP §6 (Future Work)

| SOP §6 item | Covered by |
|---|---|
| 1 Issue Types | §4.3 |
| 2 Subtype field | §4.2, skill create ops |
| 3 Status field | §4.1 (done) + §4.4 migration |
| 4 Review/QA State | §4.1 (done) |
| 5 Role-based subtype automation | §5.3 role map |
| 6 Cross-repo PR-link enforcement | §6.3 |
| 7 Review State automation | §6.1 |
| 8 QA State automation | §6.1 (merge part); QA pickup = v2 |
| 9 Release Stage field + automation | §4.2 + §6.2 |
| 10–13 Templates, evidence, DoD, prod-bug | **v2 (out of scope)** |

## 11. Out of scope (v2)
QA flow ops (`qa-start/pass/fail` + evidence scaffold), `done` + DoD checklist enforcement, issue/PR templates, QA/UAT evidence enforcement, production-bug template & response checklist (SOP §6 items 10–13).
