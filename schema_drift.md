# Runbook: schema_drift

## Symptoms
Columns were added, removed, or changed type compared with the last healthy run, even if the contract still passes (for example an extra column).

## Likely causes
- Producer added a field, or changed a type from number to text.
- A rename (also raises schema_break when a contract column is affected).

## Remediation
1. Review added, removed and retyped columns in the evidence.
2. If intentional, update the contract and baseline by running a healthy batch.
3. If accidental, ask the producer to revert.

## Prevention
Version contracts and review schema changes in pull requests.
