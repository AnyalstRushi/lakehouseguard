# Runbook: contract_violation

## Symptoms
Rows fail a rule (not_null, type, range, unique, pattern, allowed_values). Failing rows are quarantined with the exact rule names; clean rows continue to Silver. If more than half the batch fails, the circuit breaker blocks the whole batch.

## Likely causes
- not_null spike: an upstream join or lookup started failing, or an optional field became unpopulated.
- type violation: a new upstream format such as free text in a numeric field, or "N/A" placeholders.
- range violation: unit or currency scaling error, test data leaking into production, a missing decimal point.
- unique violation: producer retry storm, at-least-once delivery without an idempotency key, or a replayed file.
- pattern or allowed_values: new code values, typos, or a new category launched without telling the data team.

## Remediation
1. Open the quarantine partition for the batch and read the `_violations` column; group by rule.
2. Identify the upstream system and field from the incident evidence samples.
3. Fix at the source. Do not loosen the contract to make the alert go away unless the business rule truly changed.
4. If the business rule changed, bump the contract version with the new allowed values or range, and replay the quarantined rows.
5. For duplicates, deduplicate on the primary key and add an idempotency key at the producer.

## Prevention
Alert on quarantine rate per rule, not only totals. Review the quarantine table weekly for slow-moving drift.
