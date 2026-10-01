# tz command reference (1.12.2)

All stack-related commands use AWS profile `staging` (assumed role from `~/.aws/credentials`), region ap-south-1,
and Terraform state in an S3 bucket in the staging account (one workspace per stack). DNS changes go through the
`route53` profile.

## Fargate stacks

### `tz deploy-stack -n <name>`
| Flag | Repo | Default |
|---|---|---|
| `-br, --branch` | tranzact-v2 | develop |
| `-report-br, --reporting-branch` | tz-reporting | develop |
| `-pub-br, --publisher-branch` | tz-comms-publisher | develop |
| `-sub-br, --subhub-branch` | subhub | develop |
| `-zip-br, --zipper-branch` | zipper (API + worker) | develop |
| `-daak-br, --daak-branch` | daak | develop |
| `-fe-br, --frontend-branch` | tranzact-frontend (Vue 2) | develop |
| `-fe-v3-br, --frontend-v3-branch` | tz-vue-3 | develop |
| `-ops-br, --ops-branch` | tranzact-ops-dashboard | develop |
| `--seed` | re-run the demo seed on an existing stack | off (auto on new stacks) |
| `--rebuild` | rebuild every image even if the commit is in ECR | off |
| `--backend-only` | skip Vue 2/Vue 3 frontend and ops dashboard | off |
| `--db <rds name or host[:port]>` | run against that RDS instead of the stack's own Postgres (>= 1.12.4); sticky in state; no seed unless `--seed`; migrations run on it | own Postgres |

Order of operations:
1. Select/create the Terraform workspace. A workspace with no outputs (failed first apply) counts as **new**.
2. Requires shared infra (`tz infra`) or fails with "Shared infrastructure is not set up yet".
3. Resolve each backend branch to its commit; build arm64 images locally (Docker) for commits missing in ECR and push.
4. `terraform apply` the stack with those image tags.
5. Unless `--backend-only`: start two background jobs (logs in `~/.tz/logs/`): `deploy-frontend` and
   `deploy-ops-dashboard` (the `-ops` subdomain). They only need the backend hostname so they run in parallel.
6. Wait for ECS services to be stable. On a new stack, force a second deployment of v2, subhub and zipper
   (Service Connect name race). Wait for `https://<name>-be.letstranzact.com` to answer.
7. Seed (new stack or `--seed`) via a one-off ECS task running `docker/seed.sh` from the v2 image.
8. Print a summary (backend / frontend / ops-dashboard ok|FAILED with log paths and tails). Non-zero exit if any failed.

Re-running it on an existing stack only rolls new images forward and keeps the DB.

### `tz stop-stack -n <name>`
Terraform apply with `running=false` and the current image tags. ~$0 while stopped (plus ~$2/month of EFS).

### `tz start-stack -n <name>`
Terraform apply with `running=true` and the image tags from state, then waits for services and backend. No seed.
Suggests `deploy-stack` if the stack doesn't exist.

### `tz cleanup-stack -n <name>`
`terraform destroy` (services, ALB rules, DNS, **EFS database**), deletes the workspace, then runs
`cleanup-frontend` for `<name>` and `<name>-ops`. An RDS instance attached with `--db` is left alone.

### `tz check-deployments`
Table: STACK, STATE (`running` / `stopped` / `starting` / `no services`), SERVICES running/total, backend URL.
`no services` = workspace left by a failed first deploy; `tz cleanup-stack -n <it>` removes it.

### `tz logs -n <name>`
- `-s/--service` (repeatable): `v2 reporting subhub publisher zipper zipper-worker daak postgres redis`; omit for all.
- `--since` (default `30m`; `2h`, `1d`), `--grep REGEX`, `--follow/--no-follow` (default follow).
- Reads CloudWatch group `/tz/stacks`, streams `<stack>/<service>/<task id>`; Django file logs are tailed into the
  same stream. Lines are prefixed with the service name and interleaved by time. For >1 day use Logs Insights.

### `tz db-info -n <name>`
Finds the running `<name>-postgres` task's ENI public IP and prints host/port/db/user/password plus a psql line.
On an `--db` stack it prints the RDS host and port instead. Errors if the stack's Postgres task is stopped. Note: the printed psql line falls back to `user=tranzact` if the state lacks a user;
the real user is `scar3crow`.

### `tz infra`
One-time, idempotent: ECS cluster `tz-stacks`, shared ALB with `*.letstranzact.com` ACM cert, ECR repos, task
roles, GitHub Actions push role, the 21:00 IST stop schedule. Prints cluster, ALB DNS and ECR URLs.

## Hidden commands (not in `tz --help`, still callable)

### `tz deploy-frontend -n <name> -br <vue2 branch> [-brv3 <vue3 branch>] [-be <backend stack>] [-bu v2|v3|yes|no]`
Builds and uploads the Vue 2 (and Vue 3 if `-brv3` is non-empty) frontend to S3 + CloudFront at
`<name>.letstranzact.com`; creates bucket/distribution/DNS on first run. `-be` sets which `<be>-be` backend the
build points at. `-bu no` re-uploads the existing `dist/` without building. Stashes local changes in both FE
checkouts first. Use it for a frontend-only redeploy.

### `tz deploy-ops-dashboard -n <name>-ops -br <branch> [-be <backend stack>] [-bu yes|no]`
Same for tranzact-ops-dashboard. Needs `OPS_DASHBOARD_DIRECTORY` and `OPS_DASHBOARD_DIST_DIRECTORY` in `[main]`.

## Legacy EC2 / RDS (the EC2 deploy commands `deploy-backend`, `start-backend`, `deploy-storybook` were removed in 1.12.0)

- `tz deploy-database -n mstag-x`: create RDS instance `mstag-x` restored from the `cli-db` snapshot (mstag-dmz's
  scrambled prod copy, refreshed by `prod-db-snapshot`) with mstag-dmz's instance settings (1.12.3+). Pair with
  `deploy-stack --db mstag-x`.
- `tz cleanup-database -n mstag-x`: delete that RDS instance.
- `tz cleanup-backend -n mstag-x [-e staging|prod]`: delete an old EC2 environment: RDS instance (skips the shared
  `mstag-dmz`), load balancers, target groups, EC2 instances, Route53 records. Each step continues on error.
  `-e prod` targets the production account: never use without explicit instruction.
- `tz cleanup-frontend -n mstag-x`: delete an S3 + CloudFront frontend and its DNS record.
- `tz remote-ssh -n mstag-x`: SSH into the EC2 backend `mstag-x-be` using `PEM_KEY_PATH`.
- `tz add-remote -n mstag-x [-a alias]`: write the EC2 instance's IP into `SSH_CONFIG_PATH` as host `alias`
  (defaults to the name), so `ssh <alias>` works.
- `tz prod-db-snapshot [-dbn tz-pg-prod-read-replica] [-sdbn mstag-dmz]`: snapshot the prod DB, copy/re-encrypt it,
  share to staging, restore as `<sdbn>-new`, scramble private details, refresh the `cli-db` snapshot, **delete the
  existing `<sdbn>` instance and rename `-new` into its place**. Long, uses production credentials, replaces a
  shared database. Only on explicit request.

## Setup / maintenance

- `tz setup`: interactive; writes `~/.aws/config`, `~/.aws/credentials`, `~/.tz/config` only if absent.
- `tz update`: compares with the private PyPI and runs `pip install --upgrade`. For pipx installs use
  `pipx upgrade tranzact-cli` instead.
- `tz --version`.

## Other entry points

- MCP server `tz-cli` (install `tranzact-cli[mcp]`): tools `deploy_stack` (incl. `db`), `stop_stack`, `start_stack`,
  `cleanup_stack`, `db_info`, `logs`, each shelling out to the installed `tz`.
- `tz-tui` (in the tranzact-cli checkout, untracked in some clones): Textual UI over `tz`, with dashboard,
  deploy form, presets and history. `TZ_BIN` overrides the binary.
