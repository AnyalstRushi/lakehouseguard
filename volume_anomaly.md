# Runbook: volume_anomaly

## Symptoms
Row count is far from the healthy baseline (robust z-score beyond 3.5). A drop shrinks revenue; a spike often signals duplicates or a replay.

## Likely causes
- Drop: partial extract, upstream outage, filter or WHERE clause added by mistake, pagination bug, a source region offline.
- Spike: duplicate delivery, backfill overlapping live data, a bot or load test, a promotion.

## Remediation
1. Check whether the drop is uniform or concentrated (one city or category) using the Gold fact table.
2. Re-run the extract for the window and compare counts with the source system of record.
3. For a spike, check the unique-key violations in the same run; duplicates are the most common cause.
4. Replay the corrected batch with the same batch id.

## Prevention
Reconcile counts against the source system after each load. Alert on both drops and spikes.
