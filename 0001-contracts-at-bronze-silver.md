# ADR 0001: Enforce contracts at the Bronze to Silver boundary

Status: accepted

## Context
Bad data must be stopped before consumers see it, but dropping it destroys evidence needed for root cause analysis.

## Decision
Bronze stores batches exactly as received. The contract gate runs between Bronze and Silver. Failing rows go to a Quarantine table with every rule they broke.

## Consequences
+ Bronze is replayable; nothing is lost. + Silver is trustworthy by construction. + Quarantine is the evidence base for RCA.
- Extra storage for rejected rows (cheap compared with an unexplained KPI error).
