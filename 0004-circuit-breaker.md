# ADR 0004: Circuit breaker on quarantine rate

Status: accepted

## Context
If 70% of a batch is invalid, publishing the remaining 30% yields biased KPIs that look plausible.

## Decision
When more than `max_quarantine_fraction` (default 0.5) of a batch fails, block the whole batch and quarantine everything.

## Consequences
+ Gold never shows a heavily filtered partial picture. - A single systemic bug delays an entire batch; the incident and runbook make the replay path explicit.
