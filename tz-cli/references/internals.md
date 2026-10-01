# How tz works under the hood

## Config files
- `~/.tz/config` (configparser, values read **raw**: quotes are kept literally, `$HOME`/`~` handling is unreliable,
  so use absolute paths):
  - `[aws]` `AWS_CONFIG_FILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_LAMBDA_EDGE_ARN` (CloudFront router for FE).
  - `[main]` FE checkouts used for deploy builds: `FRONT_END_DIRECTORY` / `FRONT_END_DIST_DIRECTORY` (Vue 2),
    `V3_FRONT_END_DIRECTORY` / `V3_FRONT_END_DIST_DIRECTORY`, `OPS_DASHBOARD_DIRECTORY` / `_DIST_DIRECTORY`;
    `PEM_KEY_PATH`, `SSH_CONFIG_PATH`; backend checkouts for `tz local` (`TRANZACT_V2_DIRECTORY` etc.).
  - `[auth]` `PYPI_USERNAME/PASSWORD` (private index, image builds), `LINEAR_HEADER_AUTH`,
    `DATABASE_USERNAME/PASSWORD` (RDS paths only: `deploy-database`, `deploy-stack --db`, `local up --db`, `prod-db-snapshot`).
- `~/.aws/credentials`: a personal profile plus assumed-role profiles `staging` and `route53` (and `production`
  for `prod-db-snapshot`). Working from a source checkout: `AWS_CONFIG_FILE=~/.aws/config AWS_SHARED_CREDENTIALS_FILE=~/.aws/credentials uv run tz ...`.
- Best practice for FE deploy checkouts: dedicated clones (e.g. `.../deploy/tz-vue-3`), never your working checkout,
  because the build stashes and switches branches there.

## Terraform
- Modules bundled in the package: `tranzactCLI/terraform/aws/shared` (applied by `tz infra`) and
  `.../stack` (one workspace per stack). State in an S3 bucket in the staging account.
- Stack variables: `name`, `shared_state_bucket`, `running`, `image_tags`, and (1.12.4+) the external `db`.
  Everything else (sizes, env) is in `stack/locals.tf`. Changing it means a tranzact-cli PR.
- OOM (exit 137) → raise that service in `sizes` in the stack Terraform (a CLI change).

## Images
- Each service repo owns its `Dockerfile`; the CLI's entrypoint/helpers (`tranzactCLI/docker/common`) are staged into
  a private clone under `~/.tz/build/<repo>` at build time. Repos without a stack Dockerfile fall back to
  `tranzactCLI/docker/<repo>/Dockerfile`.
- Tag = commit (plus CLI docker files). Built arm64 natively on Apple Silicon, pushed to ECR `tz/<repo>:<tag>`.
  Built once per commit regardless of who deploys.

## Runtime
- ECS cluster `tz-stacks`, services `<stack>-<service>` (`-v2`, `-reporting`, `-subhub`, `-publisher`, `-zipper`,
  `-zipper-worker`, `-daak`, `-postgres`, `-redis`). Service Connect for in-stack names (`postgres`, `subhub`, `v2`...).
- Container `.env` is rebuilt by `entrypoint.sh` at each start from the staging secret plus `TZ_ENV_OVERRIDES`.
  Optional files not in git (Google service-account JSONs): Secrets Manager `stack-files-<repo>`, a JSON map of
  `{"filename": "content"}`, written at start.
- Shell into a task (read-only debugging):
  `aws ecs execute-command --cluster tz-stacks --task <id> --container v2 --interactive --command sh`.
- Editing `/app/.env` over ECS Exec **does not stick**: v2 runs `gunicorn --preload`, and any restart rebuilds `.env`.
  The only no-PR override is editing `TZ_ENV_OVERRIDES` on the task definition; it survives restarts and the nightly
  stop but `tz start-stack`/`deploy-stack` re-apply Terraform over it.
- Force a fresh rollout without touching the DB:
  `aws ecs update-service --cluster tz-stacks --service <stack>-<svc> --force-new-deployment`.
  stop/start does **not** do this (scaling keeps the deployment).

## Frontend builds
Since 1.12.2 the builds run `npm install` (Vue 2) / `bun install` (Vue 3) after checkout, so new deps on a branch no longer break them.

## Verifying an FE deploy really shipped your branch
Grep the served bundle for something only your branch has, e.g. fetch `https://<stack>.letstranzact.com/v3/` and
look for an expected lazy-chunk name in `/v3/assets/index-*.js`.

## Cost
A stack is ~2.25 vCPU / 6.5 GB on Graviton, ~$23/month at 12h on weekdays, $0 stopped (+~$2 EFS).
Large RDS/ELB lines in the bill are the old EC2 estate, not stacks. Budget: `staging-daily-cost`.

## Contributing
`main` of tranzact-cli is protected; every change goes through a PR, and the version lives in `pyproject.toml`.
