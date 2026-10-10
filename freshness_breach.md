# Runbook: freshness_breach

## Symptoms
The newest event timestamp is older than the contract SLA. Dashboards look healthy but show old numbers; revenue appears flat or lower than reality for the current hour.

## Likely causes
- Upstream extract delayed or paused, late-arriving data, a stuck queue or consumer.
- Clock skew or timezone bug in the producer (timestamps shifted by hours).
- Orchestrator run executed against yesterday's file or an old watermark.

## Remediation
1. Compare the newest event timestamp with the run time; a round number of hours suggests a timezone shift.
2. Check the upstream extract job and its watermark; re-trigger the extract.
3. If data is simply late, wait for the next run and confirm the gap closes. Backfill the missing window using the same batch ids.
4. Notify consumers that figures for the stale window are provisional.

## Prevention
Monitor watermark lag per source. Publish freshness on the dashboard so consumers see staleness instead of trusting silently.
