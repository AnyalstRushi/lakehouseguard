# Runbook: schema_break

## Symptoms
A required contract column is missing or renamed (for example `amount` arrives as `order_amount`). The whole batch is blocked, nothing reaches Silver, Gold stops updating, and downstream freshness degrades.

## Likely causes
- Upstream producer shipped a breaking change (rename, drop) without bumping the contract version.
- Source API version changed or an extraction query was edited.
- Column mapping in the ingestion job was reverted.

## Remediation
1. Compare the unexpected columns in the incident evidence with the missing ones to find the rename.
2. Ask the producer team to roll back, or agree a contract version bump (`version: 1.3.0`) with an explicit mapping.
3. Replay the blocked batch with the same batch id after the fix; partitions are idempotent so Gold recomputes cleanly.
4. Confirm quarantine holds every row of the batch and nothing was lost.

## Prevention
Require contract tests in the producer's CI. Treat a contract as an API: breaking changes need a major version bump and a deprecation window.
