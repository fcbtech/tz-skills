# Deploying the SDLC Actions companion

The `sdlc` skill helper (`scripts/sdlc_helper.py`) provides event handlers (`on-pr`, `on-merge`,
`on-deploy`, `check-pr-link`) that keep `fcbtech/pm` issues and Project #86 in sync automatically.
The two YAML files here wire them into GitHub Actions. **Both are validated end-to-end against the
sandbox; only deployment is left, because it requires permissions this skill's automation lacks.**

## Prerequisites (require an admin + `workflow`-scoped token)

1. **Org Actions secret `SDLC_PROJECT_TOKEN`** — a fine-grained PAT with:
   - Organization → **Projects: Read and write**
   - Repository (pm + impl repos + tz-skills) → **Issues: R/W**, **Contents: Read**, **Pull requests: Read**

   The default `GITHUB_TOKEN` cannot write org Projects v2 or cross-repo issues, so this secret is required.

2. **A token with the `workflow` scope** to commit files under `.github/workflows/`
   (`gh auth refresh -s workflow`, or commit via the web UI). The skill's automation token lacks it.

## Steps

1. Copy `sdlc-reusable.yml` → `fcbtech/pm/.github/workflows/sdlc-reusable.yml`.
2. Copy `sdlc-caller.yml` → `<impl-repo>/.github/workflows/sdlc.yml` for each implementation repo
   (start with one, e.g. `tz-core`, then roll out).
3. Verify each repo's deploy mechanism: `sdlc-caller.yml` assumes a **`push` to `canary`/`main`**
   marks a deploy. If a repo deploys via a separate workflow or environment, change the `deploy` job
   trigger to `on: deployment_status` and pass the deployed SHAs to `on-deploy`.
4. (Optional) Add `check-pr-link` as a required status check on impl repos to enforce that every PR
   references a valid `fcbtech/pm#N`.

## Validated behaviour (sandbox, 2026-06-19)

| Event | Result on the linked `fcbtech/pm` issue |
|-------|------------------------------------------|
| PR opened | Status=WIP, Review State=Waiting for Review |
| PR review approved / changes requested | Review State=Approved / Changes Requested |
| PR merged (task/dev) | Review State=Merged, Status=Done |
| PR merged (bug) | Review State=Merged, QA State=Ready for QA (Status stays WIP) |
| Push to canary / main | Release Stage=In Canary / In Prod for every linked issue in the range |
| PR missing a pm ref | `check-pr-link` fails the check |
