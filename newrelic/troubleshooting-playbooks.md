# Troubleshooting Playbooks

Step-by-step diagnostic guides for common production issues.

---

## Playbook 1: High Latency Investigation

**Symptoms**: Response times are elevated, users reporting slow application

### Step 1: Confirm the Issue
```bash
# Check current latency vs baseline
newrelic nrql query --query 'SELECT average(duration), percentile(duration, 95, 99) FROM Transaction SINCE 1 hour ago COMPARE WITH 1 day ago TIMESERIES'
```

### Step 2: Identify Affected Endpoints
```bash
# Find slowest transactions
newrelic nrql query --query 'SELECT average(duration), percentile(duration, 95), count(*) FROM Transaction SINCE 1 hour ago FACET name LIMIT 20'
```

### Step 3: Check Component Breakdown
```bash
# Where is time being spent?
newrelic nrql query --query 'SELECT average(databaseDuration), average(externalDuration), average(duration - databaseDuration - externalDuration) AS appTime FROM Transaction SINCE 1 hour ago TIMESERIES'
```

### Step 4: Check Database
```bash
# Database performance
newrelic nrql query --query 'SELECT average(databaseDuration), max(databaseDuration) FROM Transaction WHERE databaseDuration > 0 SINCE 1 hour ago FACET name LIMIT 10'
```

### Step 5: Check External Services
```bash
# External call latency
newrelic nrql query --query 'SELECT average(duration) FROM Span WHERE category = "http" SINCE 1 hour ago FACET name LIMIT 10'
```

### Step 6: Check Infrastructure
```bash
# CPU and Memory
newrelic nrql query --query 'SELECT average(cpuPercent), average(memoryUsedPercent) FROM SystemSample SINCE 1 hour ago FACET hostname TIMESERIES'
```

### Step 7: Check for Recent Changes
```bash
# Recent deployments
newrelic nrql query --query 'SELECT * FROM Deployment SINCE 1 day ago'
```

---

## Playbook 2: Error Rate Spike

**Symptoms**: Increased error rates, user complaints about failures

### Step 1: Quantify the Problem
```bash
# Error rate trend
newrelic nrql query --query 'SELECT percentage(count(*), WHERE error IS true) FROM Transaction SINCE 2 hours ago COMPARE WITH 1 day ago TIMESERIES'
```

### Step 2: Identify Error Types
```bash
# Error breakdown by class
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET error.class'

# Error breakdown by message
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET error.message LIMIT 20'
```

### Step 3: Find Affected Endpoints
```bash
# Errors by transaction
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET transactionName LIMIT 20'
```

### Step 4: Check HTTP Status Codes
```bash
# Status code distribution
newrelic nrql query --query 'SELECT count(*) FROM Transaction WHERE httpResponseCode >= 400 SINCE 1 hour ago FACET httpResponseCode TIMESERIES'
```

### Step 5: Review Error Details
```bash
# Recent error messages
newrelic nrql query --query 'SELECT error.message, error.class, transactionName, host FROM TransactionError SINCE 30 minutes ago LIMIT 50'
```

### Step 6: Check Logs
```bash
# Error logs
newrelic nrql query --query 'SELECT message FROM Log WHERE level = "ERROR" SINCE 30 minutes ago LIMIT 50'
```

### Step 7: Correlate with Deployments
```bash
newrelic nrql query --query 'SELECT * FROM Deployment SINCE 1 day ago'
```

---

## Playbook 3: Throughput Drop

**Symptoms**: Lower than expected request volume, potential availability issue

### Step 1: Confirm Throughput Drop
```bash
# Current vs baseline throughput
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) FROM Transaction SINCE 1 hour ago COMPARE WITH 1 day ago TIMESERIES'
```

### Step 2: Check by Endpoint
```bash
# Throughput by transaction
newrelic nrql query --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago FACET name COMPARE WITH 1 day ago'
```

### Step 3: Check Entity Health
```bash
# Find unhealthy entities
newrelic entity search --alert-severity CRITICAL
newrelic entity search --alert-severity WARNING
```

### Step 4: Check Host Availability
```bash
# Host reporting status
newrelic entity search --type HOST --reporting false
```

### Step 5: Check Synthetics
```bash
# Monitor status
newrelic synthetics monitor list --statusFilter "DISABLED"
```

### Step 6: Check for Infrastructure Issues
```bash
# CPU/Memory issues that might cause throttling
newrelic nrql query --query 'SELECT max(cpuPercent), max(memoryUsedPercent) FROM SystemSample SINCE 1 hour ago FACET hostname'
```

---

## Playbook 4: Infrastructure Resource Exhaustion

**Symptoms**: High CPU, memory, or disk usage

### Step 1: Identify Affected Hosts
```bash
# Find high CPU hosts
newrelic nrql query --query 'SELECT average(cpuPercent) FROM SystemSample SINCE 30 minutes ago FACET hostname WHERE cpuPercent > 80'

# Find high memory hosts
newrelic nrql query --query 'SELECT average(memoryUsedPercent) FROM SystemSample SINCE 30 minutes ago FACET hostname WHERE memoryUsedPercent > 80'
```

### Step 2: Trend Analysis
```bash
# CPU trend
newrelic nrql query --query 'SELECT average(cpuPercent) FROM SystemSample SINCE 6 hours ago FACET hostname TIMESERIES'

# Memory trend
newrelic nrql query --query 'SELECT average(memoryUsedPercent) FROM SystemSample SINCE 6 hours ago FACET hostname TIMESERIES'
```

### Step 3: Check Disk
```bash
# Disk usage
newrelic nrql query --query 'SELECT average(diskUsedPercent) FROM StorageSample SINCE 1 hour ago FACET hostname, mountPoint'
```

### Step 4: Correlate with Application Load
```bash
# Check if application load increased
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) FROM Transaction SINCE 6 hours ago TIMESERIES'
```

### Step 5: Check Process Details (if available)
```bash
# Process CPU usage
newrelic nrql query --query 'SELECT average(cpuPercent) FROM ProcessSample SINCE 30 minutes ago FACET processDisplayName, hostname LIMIT 20'
```

---

## Playbook 5: Database Performance Issues

**Symptoms**: Slow queries, database timeouts, high database latency

### Step 1: Identify Slow Database Operations
```bash
# Transactions with high database time
newrelic nrql query --query 'SELECT average(databaseDuration), max(databaseDuration), count(*) FROM Transaction WHERE databaseDuration > 0.5 SINCE 1 hour ago FACET name LIMIT 20'
```

### Step 2: Database Time Trend
```bash
# Database latency over time
newrelic nrql query --query 'SELECT average(databaseDuration), percentile(databaseDuration, 95) FROM Transaction SINCE 2 hours ago TIMESERIES'
```

### Step 3: Check Database Call Volume
```bash
# Call count distribution
newrelic nrql query --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago FACET databaseCallCount'
```

### Step 4: Check Specific Database Operations
```bash
# Breakdown by operation type
newrelic nrql query --query 'SELECT average(duration), count(*) FROM Span WHERE category = "datastore" SINCE 1 hour ago FACET name LIMIT 20'
```

### Step 5: Compare with Baseline
```bash
newrelic nrql query --query 'SELECT average(databaseDuration) FROM Transaction SINCE 1 hour ago COMPARE WITH 1 day ago TIMESERIES'
```

---

## Playbook 6: External Service Dependency Issues

**Symptoms**: Failures or slowness due to third-party services

### Step 1: Identify External Call Issues
```bash
# External call latency
newrelic nrql query --query 'SELECT average(externalDuration), percentile(externalDuration, 95) FROM Transaction WHERE externalDuration > 0 SINCE 1 hour ago FACET name LIMIT 20'
```

### Step 2: Check External Service Health
```bash
# External calls breakdown
newrelic nrql query --query 'SELECT average(duration), count(*) FROM Span WHERE category = "http" SINCE 1 hour ago FACET peer.hostname LIMIT 20'
```

### Step 3: Check for External Errors
```bash
# External call errors
newrelic nrql query --query 'SELECT count(*) FROM Span WHERE category = "http" AND error.message IS NOT NULL SINCE 1 hour ago FACET peer.hostname, error.message'
```

### Step 4: Trend Analysis
```bash
# External latency trend
newrelic nrql query --query 'SELECT average(externalDuration) FROM Transaction SINCE 6 hours ago COMPARE WITH 1 day ago TIMESERIES'
```

---

## Playbook 7: Deployment Verification

**Symptoms**: Post-deployment monitoring, canary analysis

### Step 1: Find Recent Deployment
```bash
newrelic nrql query --query 'SELECT * FROM Deployment SINCE 1 day ago'
```

### Step 2: Compare Error Rate
```bash
# Error rate before/after
newrelic nrql query --query 'SELECT percentage(count(*), WHERE error IS true) FROM Transaction SINCE 2 hours ago COMPARE WITH 4 hours ago TIMESERIES'
```

### Step 3: Compare Latency
```bash
# Latency before/after
newrelic nrql query --query 'SELECT average(duration), percentile(duration, 95) FROM Transaction SINCE 2 hours ago COMPARE WITH 4 hours ago TIMESERIES'
```

### Step 4: Compare Throughput
```bash
# Throughput before/after
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) FROM Transaction SINCE 2 hours ago COMPARE WITH 4 hours ago TIMESERIES'
```

### Step 5: Check for New Error Types
```bash
# New errors since deployment
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 2 hours ago FACET error.message'
```

---

## General Tips

### When to Escalate
- Error rate > 5% sustained
- P95 latency > 3x baseline
- Throughput drop > 50%
- Infrastructure resources > 90% utilization
- Critical alert triggered

### Information to Gather for Escalation
1. Time range of incident
2. Affected services/endpoints
3. Error messages and stack traces
4. Recent deployments or changes
5. Infrastructure metrics
6. User impact assessment

### Quick Health Check Command Sequence
```bash
# Run these in sequence for quick assessment
newrelic entity search --alert-severity CRITICAL
newrelic nrql query --query 'SELECT percentage(count(*), WHERE error IS true) FROM Transaction SINCE 1 hour ago'
newrelic nrql query --query 'SELECT average(duration), percentile(duration, 95) FROM Transaction SINCE 1 hour ago'
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) FROM Transaction SINCE 1 hour ago'
```
