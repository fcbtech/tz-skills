---
name: tz-cli
description: Operate TranZact environments with the `tz` CLI (tranzact-cli) - deploy/stop/start/destroy Fargate staging stacks (mstag-*), tail their logs, connect to their Postgres, run the backend locally with `tz local`, and handle the legacy EC2/RDS leftovers. Use whenever a task mentions tz, tranzact-cli, a staging stack, mstag-<anything>, deploying a branch to staging, stack logs/DB, `tz local`, or debugging a staging environment - and before reaching for raw aws/terraform/docker to do any of that.
---

# tz CLI (tranzact-cli)

`tz` is the only sanctioned way to change TranZact staging infrastructure. Never reimplement a command
with raw `aws`/`terraform`/`docker compose` calls; use those only for read-only inspection or for the
documented escape hatches in `references/`.

Verified against **tranzact-cli 1.12.4** (origin/main, 2026-10-01; `deploy-stack --db` needs >= 1.12.4). If `tz --version` is newer, trust `tz <cmd> --help`
over this file where they differ. Source: `fcbtech/tranzact-cli`; the repo also ships its own skill at `.claude/skills/tranzact-cli/`.

## Before running anything

- **Safety tiers.** Read-only, run freely: `check-deployments`, `logs`, `db-info`, `local ps`, `local logs`, `--help`, `--version`.
  Changes things, fine when the user asked for it: `deploy-stack`, `start-stack`, `stop-stack`, `local up/down/restart`.
  **Destructive, always confirm first, naming exactly what goes away:** `cleanup-stack` (wipes the stack DB),
  `cleanup-backend`, `cleanup-database`, `cleanup-frontend`, `local down --reset`, `prod-db-snapshot`
  (touches the **production** AWS account and replaces the shared `mstag-dmz` RDS instance), `deploy-stack --rebuild`
  (slow, not destructive), and anything on a stack the user didn't name (stacks are shared between people).
- Never run state-changing SQL against a stack/RDS/local database unless the user explicitly asks; hand them the SQL.
- **Names must start with `mstag`.** Otherwise most commands print a message and **exit 0** without doing
  anything; check the output, not the exit code.
- stop/start/cleanup/db-info on a name with no stack print "No stack named X" and do nothing.
- `deploy-stack` and `logs` (default `--follow`) are long-running. Run deploys in the background and poll;
  always pass `--no-follow` to `tz logs` from an agent.
- Prereqs: Docker Desktop running (image builds, `tz local`), Terraform installed, `~/.tz/config` and `~/.aws/*`
  present (`tz setup` creates them). Every command but `setup` warns if `~/.tz/config` is missing.

## Command map

| Goal | Command | Typical time |
|---|---|---|
| Create or redeploy a stack | `tz deploy-stack -n mstag-x [-br v2] [-report-br] [-pub-br] [-sub-br] [-zip-br] [-daak-br] [-fe-br vue2] [-fe-v3-br vue3] [-ops-br] [--seed] [--rebuild] [--backend-only] [--db <rds>]` | new 18-20 min, redeploy ~8, backend-only ~5 |
| Stop (scale to 0, DB kept) | `tz stop-stack -n mstag-x` | 1 min |
| Start again (same images + DB) | `tz start-stack -n mstag-x` | 3 min |
| Destroy everything incl. DB | `tz cleanup-stack -n mstag-x` | 3 min |
| List stacks + state | `tz check-deployments` | |
| Logs | `tz logs -n mstag-x [-s v2 -s subhub ...] [--since 2h] [--grep REGEX] --no-follow` | |
| DB host/creds | `tz db-info -n mstag-x` | |
| Shared infra (once, idempotent) | `tz infra` | |
| Local backend (Compose) | `tz local up [-s v2 ...] [--db mstag-dmz [--migrate]]`, `down [--reset]`, `logs`, `ps`, `restart [-s]` | first up ~10 min |
| Frontend-only redeploy (hidden) | `tz deploy-frontend -n mstag-x -br <vue2> -brv3 <vue3> -be mstag-x -bu yes` | |
| Ops dashboard only (hidden) | `tz deploy-ops-dashboard -n mstag-x-ops -br <br> -be mstag-x -bu yes` | |
| Upgrade the CLI | `tz update` (pip install) or `pipx upgrade tranzact-cli` (pipx install); see note | |

Full flags, defaults and per-command behaviour: `references/commands.md`.
Local stack details and container gotchas: `references/local.md`.
How it works under the hood + escape hatches: `references/internals.md`.
Symptom → fix playbook: `references/troubleshooting.md` (read it before debugging any stack).

## Facts every agent gets wrong without this

- A stack `mstag-x` = `https://mstag-x.letstranzact.com` (Vue 2 + Vue 3 FE), `https://mstag-x-be.letstranzact.com`
  (v2 API; reporting on `:81`), `https://mstag-x-ops.letstranzact.com` (ops dashboard).
- Stack services: tranzact-v2, tz-reporting, tz-comms-publisher, subhub, zipper (+ celery worker), daak, plus their
  own Postgres + Redis containers. **tz-print is not in the stack**; every stack uses the shared staging print server.
- **Every branch defaults to `develop`**, per service. Deploying `-br feat/x` alone gives develop everywhere else.
- **All stacks auto-stop at 21:00 IST daily.** Morning: `tz start-stack -n <stack>`.
- Stack DB lives on per-stack EFS: survives stop/start, the nightly stop and redeploys (redeploy migrates forward).
  Only `cleanup-stack` wipes it. Fresh DB = `cleanup-stack` then `deploy-stack`.
- A **new** stack is seeded automatically (also a workspace left by a failed first deploy). `--seed` re-runs it on an
  existing stack, idempotently. The seed is tranzact-v2 `qa/tools/setup_profile_final.py` and needs a v2 branch
  containing PR #6586 (develop/main since 2026-09-25) - older branches fail with `setup_profile_final.py is missing`.
- Seeded login: the stack config's `QA_EMAIL`/`QA_PASSWORD`, else `qae2etesting@letstranzact.com` / `123456`;
  tenant `TranZact QA`.
- Stack DB credentials are fixed: user `scar3crow`, password `apgh2bpb`, db `tranzact`, port 5432 open to the
  internet. The **host is the Postgres task's public IP and changes on every restart**: re-run `tz db-info`.
- **Stack on real data (>= 1.12.4):** `tz deploy-database -n mstag-x` (restores the `cli-db` snapshot = a copy of
  mstag-dmz with scrambled prod data) then `tz deploy-stack -n mstag-x --db mstag-x`. No Postgres task, no seed
  (`--seed` still works), and **the branch's migrations run on that RDS**, so only point it at an instance the stack owns,
  never shared `mstag-dmz`. The choice sticks in state across redeploys/stop/start until a deploy names another `--db`;
  `cleanup-stack` leaves the instance (delete with `tz cleanup-database`). Before 1.12.4 stacks could not use RDS.
- **Frontend branch trap:** the FE builds run `git stash; git checkout <br>; git pull` in the checkouts named by
  `~/.tz/config` - checkout happens **before** any fetch. A branch that doesn't exist locally there silently
  ships whatever was checked out (usually develop). Before deploying a new FE branch:
  `git -C <FRONT_END_DIRECTORY|V3_FRONT_END_DIRECTORY> fetch origin <br> && git -C <dir> branch --track <br> origin/<br>`.
  Backend images are unaffected (built from fresh clones under `~/.tz/build/`).
- The FE/ops builds **`git stash` uncommitted work** in those checkouts and switch their branch. If
  `OPS_DASHBOARD_DIRECTORY` points at a checkout someone is working in, `deploy-stack` (without `--backend-only`)
  will stash their changes and move it to develop. Check `~/.tz/config [main]` before deploying.
- Images are tagged by commit; a commit already in ECR is never rebuilt (unless `--rebuild`), so pushing a new commit
  to the branch is what makes a redeploy pick up code.
- `tz start-stack` and `deploy-stack` re-apply Terraform, overwriting any manual ECS task-definition edits.
- `tz update` (built-in) runs plain `pip install` from the private index: fine for a pip install, wrong for a pipx
  install (`pipx list` shows which); there use `pipx upgrade tranzact-cli`. (Some users alias `tz update` to that in their shell rc; agent shells may not load it.)
- `~/.tz/config` is read raw by configparser: **quotes are kept literally** (`DATABASE_USERNAME=""` means the
  two-character string `""`). Write bare values.
