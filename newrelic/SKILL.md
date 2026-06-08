---
name: newrelic
description: Analyze telemetry data from New Relic to diagnose production issues, investigate performance problems, and provide actionable recommendations. Use when the user asks about monitoring, observability, APM, errors, performance, latency, throughput, logs, traces, or production system health. Triggers on keywords like "New Relic", "APM", "telemetry", "monitoring", "production issue", "error rate", "latency", "throughput", "logs", "traces".
---

# New Relic Observability & Diagnostics Skill

Analyze telemetry data from New Relic to uncover insights, diagnose issues, and provide actionable recommendations.

## CRITICAL: CLI Usage Requirement

**ALWAYS use the `newrelic` command-line tool for ALL interactions with New Relic.**

The CLI must be installed and configured locally with a New Relic user API key, account ID, and region before any diagnostic command can run. Never hardcode or commit New Relic API keys, license keys, or account-specific secrets. Before executing any command, consult the documentation in [cli-docs/](cli-docs/) to ensure correct syntax.

## First Run Setup

If the `newrelic` command is not available, ask the user to install the New Relic CLI first.

If the CLI is installed but not configured, ask the user for:

- New Relic user API key
- New Relic account ID
- New Relic region (`US` or `EU`)

Then configure a local profile:

```bash
newrelic profile add --profile tranzact --apiKey <new-relic-user-key> --accountId <account-id> --region US
newrelic profile default --profile tranzact
```

The New Relic CLI stores credentials in the user's local CLI config, not in this repository. Do not echo API keys in chat, logs, shell history, issues, or PRs.

## Quick Reference: Essential Commands

### 1. NRQL Queries (Primary Tool for Analysis)

```bash
# Basic query
newrelic nrql query --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago'

# With specific time range
newrelic nrql query --query 'SELECT average(duration) FROM Transaction SINCE 24 hours ago TIMESERIES'
```

### 2. Entity Search

```bash
# Search by name
newrelic entity search --name "production-api"

# Search by type
newrelic entity search --type APPLICATION

# Search by alert severity
newrelic entity search --alert-severity CRITICAL
```

### 3. APM Application Search

```bash
newrelic apm application search --name "my-app"
```

### 4. NerdGraph (Advanced GraphQL Queries)

```bash
newrelic nerdgraph query 'query { actor { user { email } } }'
```

### 5. Workloads

```bash
newrelic workload list
```

### 6. Synthetics Monitors

```bash
newrelic synthetics monitor list
newrelic synthetics monitor list --statusFilter "DISABLED"
```

## Diagnostic Workflow

When investigating an issue, follow this systematic approach:

### Step 1: Identify Scope
- What services/applications are affected?
- What is the time window of the issue?
- What are the symptoms (errors, latency, throughput)?

### Step 2: Gather Initial Data
Run these queries to get baseline understanding:

```bash
# Error rate
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET error.message'

# Throughput and latency
newrelic nrql query --query 'SELECT rate(count(*), 1 minute), average(duration), percentile(duration, 95) FROM Transaction SINCE 1 hour ago TIMESERIES'

# Find affected entities
newrelic entity search --alert-severity CRITICAL
newrelic entity search --alert-severity WARNING
```

### Step 3: Deep Dive
Based on findings, investigate specific areas.

### Step 4: Correlate
Look for correlations between events, deployments, and issues.

### Step 5: Recommend
Provide specific, actionable recommendations.

## Common NRQL Queries by Scenario

See [nrql-reference.md](nrql-reference.md) for comprehensive query templates.

## Troubleshooting Playbooks

See [troubleshooting-playbooks.md](troubleshooting-playbooks.md) for step-by-step guides.

## Output Formatting

- Default output is JSON. Use `--format Text` for human-readable output
- Use `--format YAML` for structured data
- Use `--plain` for compact output

## Best Practices

1. **Start broad, then narrow down** - Begin with high-level queries, then drill into specifics
2. **Use TIMESERIES** - Visualize trends over time to identify patterns
3. **Use FACET** - Break down metrics by dimensions to find outliers
4. **Compare time periods** - Use COMPARE WITH to identify deviations from baseline
5. **Check for recent deployments** - Correlate issues with recent changes
6. **Look at the full picture** - Consider infrastructure, APM, logs, and synthetics together

## CLI Documentation Reference

For detailed command syntax, read the docs in [cli-docs/](cli-docs/).

Key files:
- [cli-docs/newrelic_nrql_query.md](cli-docs/newrelic_nrql_query.md) - NRQL query syntax
- [cli-docs/newrelic_entity_search.md](cli-docs/newrelic_entity_search.md) - Entity search options
- [cli-docs/newrelic_nerdgraph_query.md](cli-docs/newrelic_nerdgraph_query.md) - GraphQL queries
- [cli-docs/newrelic_apm_application.md](cli-docs/newrelic_apm_application.md) - APM commands
- [cli-docs/newrelic_synthetics_monitor.md](cli-docs/newrelic_synthetics_monitor.md) - Synthetics monitoring
- [cli-docs/newrelic_workload.md](cli-docs/newrelic_workload.md) - Workload management
- [cli-docs/newrelic_diagnose_run.md](cli-docs/newrelic_diagnose_run.md) - Diagnostics troubleshooting

Run `newrelic [command] --help` for inline help on any command.
