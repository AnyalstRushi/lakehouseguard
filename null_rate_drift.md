# Runbook: null_rate_drift

## Symptoms
The share of null values in a column rose well above its healthy baseline, even if the column is nullable.

## Likely causes
- A join or lookup upstream stopped matching; a field was deprecated; a new app version no longer sends the value.
- A partial outage of an enrichment service.

## Remediation
1. Identify the column from the incident and the first affected batch.
2. Trace upstream via lineage to the system that populates it.
3. Fix the enrichment; replay affected batches.

## Prevention
Track null rate per column as a first-class metric and review it in weekly data quality reviews.
