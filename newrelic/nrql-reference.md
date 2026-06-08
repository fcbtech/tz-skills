# NRQL Query Reference

Comprehensive NRQL query templates for common observability scenarios.

## Performance Analysis

### Response Time / Latency

```bash
# Average response time over time
newrelic nrql query --query 'SELECT average(duration) FROM Transaction SINCE 1 hour ago TIMESERIES'

# Percentile latencies (P50, P90, P95, P99)
newrelic nrql query --query 'SELECT percentile(duration, 50, 90, 95, 99) FROM Transaction SINCE 1 hour ago TIMESERIES'

# Latency by transaction name
newrelic nrql query --query 'SELECT average(duration), percentile(duration, 95) FROM Transaction SINCE 1 hour ago FACET name LIMIT 20'

# Slow transactions (> 2 seconds)
newrelic nrql query --query 'SELECT count(*) FROM Transaction WHERE duration > 2 SINCE 1 hour ago FACET name'

# Compare latency with yesterday
newrelic nrql query --query 'SELECT average(duration) FROM Transaction SINCE 1 hour ago COMPARE WITH 1 day ago TIMESERIES'
```

### Throughput

```bash
# Requests per minute
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) AS "RPM" FROM Transaction SINCE 1 hour ago TIMESERIES'

# Throughput by endpoint
newrelic nrql query --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago FACET name LIMIT 20'

# Throughput by host
newrelic nrql query --query 'SELECT count(*) FROM Transaction SINCE 1 hour ago FACET host LIMIT 20'

# Compare throughput with baseline
newrelic nrql query --query 'SELECT rate(count(*), 1 minute) FROM Transaction SINCE 1 hour ago COMPARE WITH 1 week ago TIMESERIES'
```

## Error Analysis

### Error Rates

```bash
# Overall error rate percentage
newrelic nrql query --query 'SELECT percentage(count(*), WHERE error IS true) AS "Error Rate %" FROM Transaction SINCE 1 hour ago TIMESERIES'

# Error count by type
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET error.class'

# Error count by message
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET error.message LIMIT 20'

# Errors by transaction/endpoint
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET transactionName LIMIT 20'

# HTTP error status codes
newrelic nrql query --query 'SELECT count(*) FROM Transaction WHERE httpResponseCode >= 400 SINCE 1 hour ago FACET httpResponseCode'

# 5xx errors specifically
newrelic nrql query --query 'SELECT count(*) FROM Transaction WHERE httpResponseCode >= 500 SINCE 1 hour ago FACET name TIMESERIES'
```

### Error Details

```bash
# Recent errors with stack traces
newrelic nrql query --query 'SELECT error.message, error.class, transactionName FROM TransactionError SINCE 30 minutes ago LIMIT 50'

# Errors by user (if user tracking enabled)
newrelic nrql query --query 'SELECT count(*) FROM TransactionError SINCE 1 hour ago FACET user'
```

## Infrastructure Metrics

### CPU and Memory

```bash
# CPU usage by host
newrelic nrql query --query 'SELECT average(cpuPercent) FROM SystemSample SINCE 1 hour ago FACET hostname TIMESERIES'

# Memory usage by host
newrelic nrql query --query 'SELECT average(memoryUsedPercent) FROM SystemSample SINCE 1 hour ago FACET hostname TIMESERIES'

# Hosts with high CPU (> 80%)
newrelic nrql query --query 'SELECT average(cpuPercent) FROM SystemSample WHERE cpuPercent > 80 SINCE 1 hour ago FACET hostname'

# Memory available
newrelic nrql query --query 'SELECT average(memoryFreeBytes/1e9) AS "Free GB" FROM SystemSample SINCE 1 hour ago FACET hostname'
```

### Disk

```bash
# Disk usage by host
newrelic nrql query --query 'SELECT average(diskUsedPercent) FROM StorageSample SINCE 1 hour ago FACET hostname, mountPoint'

# Disk I/O
newrelic nrql query --query 'SELECT average(readBytesPerSecond/1e6), average(writeBytesPerSecond/1e6) FROM StorageSample SINCE 1 hour ago FACET hostname TIMESERIES'
```

### Network

```bash
# Network throughput
newrelic nrql query --query 'SELECT average(receiveBytesPerSecond/1e6) AS "Receive MB/s", average(transmitBytesPerSecond/1e6) AS "Transmit MB/s" FROM NetworkSample SINCE 1 hour ago FACET hostname TIMESERIES'

# Network errors
newrelic nrql query --query 'SELECT sum(receiveErrorsPerSecond), sum(transmitErrorsPerSecond) FROM NetworkSample SINCE 1 hour ago FACET hostname'
```

## Database Performance

### Database Queries

```bash
# Slow database calls
newrelic nrql query --query 'SELECT average(databaseDuration) FROM Transaction WHERE databaseDuration > 0.5 SINCE 1 hour ago FACET name'

# Database call count and duration
newrelic nrql query --query 'SELECT count(*), average(databaseDuration) FROM Transaction SINCE 1 hour ago FACET databaseCallCount'

# Database operations breakdown
newrelic nrql query --query 'SELECT average(duration) FROM Span WHERE category = "datastore" SINCE 1 hour ago FACET name LIMIT 20'
```

## External Services

### External Calls

```bash
# External service response times
newrelic nrql query --query 'SELECT average(externalDuration) FROM Transaction WHERE externalDuration > 0 SINCE 1 hour ago FACET name'

# External call breakdown
newrelic nrql query --query 'SELECT average(duration), count(*) FROM Span WHERE category = "http" SINCE 1 hour ago FACET name LIMIT 20'

# Slow external calls
newrelic nrql query --query 'SELECT count(*) FROM Span WHERE category = "http" AND duration > 1 SINCE 1 hour ago FACET name'
```

## Logs Analysis

### Log Queries

```bash
# Error logs count
newrelic nrql query --query 'SELECT count(*) FROM Log WHERE level = "ERROR" SINCE 1 hour ago TIMESERIES'

# Logs by level
newrelic nrql query --query 'SELECT count(*) FROM Log SINCE 1 hour ago FACET level'

# Recent error logs
newrelic nrql query --query 'SELECT message FROM Log WHERE level = "ERROR" SINCE 30 minutes ago LIMIT 50'

# Logs by service
newrelic nrql query --query 'SELECT count(*) FROM Log SINCE 1 hour ago FACET service.name'

# Search logs for specific pattern
newrelic nrql query --query "SELECT count(*) FROM Log WHERE message LIKE '%exception%' SINCE 1 hour ago FACET message LIMIT 20"
```

## Distributed Tracing

### Trace Analysis

```bash
# Trace duration distribution
newrelic nrql query --query 'SELECT histogram(duration.ms, 10, 20) FROM Span SINCE 1 hour ago'

# Slow traces
newrelic nrql query --query 'SELECT traceId, duration FROM Span WHERE duration > 5 SINCE 1 hour ago LIMIT 20'

# Trace errors
newrelic nrql query --query 'SELECT count(*) FROM Span WHERE error.message IS NOT NULL SINCE 1 hour ago FACET error.message'

# Service dependencies
newrelic nrql query --query 'SELECT count(*) FROM Span SINCE 1 hour ago FACET service.name, peer.service'
```

## Custom Events & Metrics

### Custom Data

```bash
# Custom event counts
newrelic nrql query --query 'SELECT count(*) FROM MyCustomEvent SINCE 1 hour ago'

# Custom metrics
newrelic nrql query --query 'SELECT average(myCustomMetric) FROM Metric SINCE 1 hour ago TIMESERIES'
```

## Apdex Score

```bash
# Apdex score over time
newrelic nrql query --query 'SELECT apdex(duration, t: 0.5) FROM Transaction SINCE 1 hour ago TIMESERIES'

# Apdex by transaction
newrelic nrql query --query 'SELECT apdex(duration, t: 0.5) FROM Transaction SINCE 1 hour ago FACET name'
```

## Deployment Correlation

```bash
# Find recent deployments
newrelic nrql query --query 'SELECT * FROM Deployment SINCE 1 week ago'

# Compare metrics before/after deployment
newrelic nrql query --query 'SELECT average(duration) FROM Transaction SINCE 2 hours ago COMPARE WITH 1 day ago TIMESERIES'
```

## Advanced Patterns

### Time Window Patterns

```bash
# Last N minutes/hours/days
SINCE 30 minutes ago
SINCE 1 hour ago
SINCE 24 hours ago
SINCE 7 days ago

# Specific time range
SINCE '2024-01-01 00:00:00' UNTIL '2024-01-02 00:00:00'

# Compare with previous period
COMPARE WITH 1 hour ago
COMPARE WITH 1 day ago
COMPARE WITH 1 week ago
```

### Aggregation Functions

```bash
# Common aggregations
count(*)           # Total count
average(field)     # Mean value
sum(field)         # Total sum
min(field)         # Minimum
max(field)         # Maximum
percentile(field, N)  # Nth percentile
rate(count(*), 1 minute)  # Rate per time unit
percentage(count(*), WHERE condition)  # Percentage matching condition
```

### Filtering

```bash
# WHERE clause examples
WHERE appName = 'my-app'
WHERE duration > 1
WHERE error IS true
WHERE httpResponseCode >= 500
WHERE name LIKE '%api%'
WHERE host IN ('host1', 'host2')
```
