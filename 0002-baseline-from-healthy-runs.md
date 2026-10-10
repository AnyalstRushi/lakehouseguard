# ADR 0002: The observability baseline learns only from healthy runs

Status: accepted

## Context
A baseline that learns from every batch slowly accepts a degrading source as normal ("baseline poisoning").

## Decision
Only batches that raise no incident and are not blocked update the baseline (profiles and the capped reference sample).

## Consequences
+ A persistent regression keeps alerting until someone resolves it. - After a *legitimate* shift (new price list) the baseline must re-learn from healthy runs; the distribution-drift runbook documents that step.
