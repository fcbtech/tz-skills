# Troubleshooting playbook

Logs for every service of every stack are in CloudWatch group `/tz/stacks`, streams `<stack>/<service>/<task id>`.
`tz logs` reads them; CloudWatch Logs Insights is the tool for anything older than a day.

## Stack is up but signup returns 400
`RootNodeNotFound: Root Node Not Found in Permission Tree` in the v2 log means `iam_permission` is empty: the
Postgres task was replaced (a deploy before the EFS disk, or a `cleanup-stack`) and no seed ran.
Fix: `tz deploy-stack -n <stack> -br <branch> --seed --backend-only` (about 10 minutes).

## Free trial: subhub 200, v2 never activates the plan
Trace: v2 `POST /subscription/subscription/free-trial/` -> subhub `POST /subscription/new/` -> daak
`subscription.created` -> v2 `POST /api/external-webhook/handler/` (signed with the daak secret, `123456` on stacks).
- daak log says `Created Messages []` -> no rows in `daak_subscriptions`/`daak_destinations`. Re-run the seed, or insert
  the subscriber `acc_tranzact`, one destination at the handler URL with secret `123456`, and one subscription per event
  type (subscription.*, usagemeter.updated, user.*, company.onboarded, workflow.triggered, einvoice.*, eway_bill.synced).
- daak log says `Non 2xx response` -> the destination URL is wrong; it must end in `/api/external-webhook/handler/`.
- To replay for a company whose event was lost: push the event onto `<stack>-daak-consumer-q` (SQS) with
  `{"event_type":"subscription.created","entities":[{"entity_type":"subscription","entity_id":"<subhub_subscription.id>","entity_payload":null}],"metadata":{"account_id":"acc_<base64 company uuid>","actor_id":"actor_MA==","producer":"subhub"}}`.

## Master data missing (pincodes, GST state codes, reports)
Tables `gst_pincodedata`, `settings_statecode`, `reporting_report`, `reporting_reportinput` are filled by
`qa/tools/{gst_master_seed,reporting_catalog_seed}` in tranzact-v2 (moved there from `qa/generated/automations/` by
PR #6586). `qa/tools/setup_profile_final.py`, which the stack seed runs, seeds every `qa/tools/*_seed` first; a branch
that predates them has to merge develop. The seeds are standalone psycopg2 scripts and can be run from a develop
checkout against the stack DB with `DB_HOST/DB_USER/DB_PASSWORD/DB_NAME` set; `--check` reports without writing.

## Seed task fails at once with `qa/tools/setup_profile_final.py is missing`
The tranzact-v2 branch predates PR #6586 (2026-09-25), which replaced the `demo_account_setup` automation with
`qa/tools/setup_profile_final.py`. Merge develop into the branch and redeploy; there is no fallback to the old scripts.

## Seed log says `an account with this email already exists, but QA_PASSWORD in .env doesn't match it`
The stack database already has the seed's `QA_EMAIL` with another password (a database kept across a config change).
Either restore the password in the stack config or `tz cleanup-stack` for an empty database.

## E-way bill: "state_of_supply: Enter the correct state of consignee"
Masters India (project-gsp-mig) rejects the export payload the frontend builds (to-state 99 with the port's pincode).
The adapter must send `state_of_supply OTHER COUNTRY`, `actual_to_state_name OTHER TERRITORY`, `pincode_of_consignee 999999`,
`place_of_consignee Outside Country`, `sub_supply_type Export`, and a non-zero distance (tranzact-v2 PR #6426).
Test against the sandbox with the stack account's cached JWT from `integration_externalintegrationprovider`.

## Deploy stuck at "Waiting for services"
`tz logs -n <stack> -s postgres --since 15m --no-follow` first (Postgres must be healthy before app services start),
then the ECS console, cluster `tz-stacks`, service `<stack>-<service>`, Events tab. Exit code 137 is out of memory:
raise that service in `sizes` in the stack Terraform.

## Fresh stack: free trial or billing page returns 500, v2 log says `Failed to resolve 'subhub'`
ECS Service Connect fixes the names a deployment can resolve when the deployment is created, and a new stack creates
every app service in the same second, so v2's first deployment can miss subhub or zipper (and theirs can miss v2).
`tz deploy-stack` rolls v2, subhub and zipper a second time on a new stack since 1.11.16. For a stack deployed before
that, or if it ever recurs, force a new deployment of the three services (about 3 minutes, database untouched):
`aws ecs update-service --cluster tz-stacks --service <stack>-v2 --force-new-deployment`, same for `-subhub` and
`-zipper`. Stop/start does not help: scaling keeps the deployment.

## Cost looks wrong
Everything the stacks cost is ECS (Fargate), a share of the `tz-stacks` ALB and EFS. Large RDS and ELB lines are the
old EC2 staging estate, not the stacks. Daily numbers: Cost Explorer, group by service.

## Deployed a new frontend branch but the site still shows develop
The FE build checks out the branch in the `~/.tz/config` FE checkout **before** fetching, so a branch that doesn't
exist locally there fails with "pathspec did not match" inside the background job log (`~/.tz/logs/`) and the
build ships whatever was checked out. Fix: `git -C <dir> fetch origin <br> && git -C <dir> branch --track <br> origin/<br>`,
then `tz deploy-frontend -n <stack> -br <vue2> -brv3 <vue3> -be <stack> -bu yes`. Verify by grepping the served
`/v3/assets/index-*.js` for a chunk only your branch has.

## Redeploy didn't pick up my backend change
Images are keyed by commit. Uncommitted or unpushed changes are invisible; push, then redeploy.
`--rebuild` only helps if the build itself was bad.

## A manual env override vanished overnight
`start-stack` re-applies Terraform every morning after the 21:00 IST stop, overwriting task-definition edits
(including `TZ_ENV_OVERRIDES` hacks). Durable config changes go into the stack Terraform via a tranzact-cli PR.

## Email "sent" but never arrives
Staging publishers only deliver to recipients in the `WhiteListedRecipients` table (`active=1`) unless
`HOST_TYPE == "production"`. Check the whitelist before debugging the worker.

## Analytics / dashboard cards read 0 on staging
BigQuery isn't wired up for staging, so analytics cards are empty by design. Check whether the card has a
"Refresh Analytics" path before treating it as a bug.

## `tz db-info` host stopped working
The Postgres task got a new public IP (stop/start, nightly stop, redeploy). Re-run `tz db-info`.

## RDS create fails with `Invalid master user name`
`~/.tz/config [auth]` has quoted values; configparser keeps the quotes. Write `DATABASE_USERNAME=scar3crow`, not `"scar3crow"`.
