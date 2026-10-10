# ADR 0005: Self-healing only for unambiguous repairs, and heal rate is monitored

Status: accepted

## Decision
Repairs allowed: trim whitespace, case and alias mapping onto the contract's allowed values, parsing currency-formatted numbers. Everything else is quarantined.
Each repair is counted; more than 10% of rows repaired raises `high_heal_rate`.

## Consequences
+ Cosmetic upstream noise does not cause outages. + Healing cannot hide a degrading producer. - Aliases need an owner (they live in the contract and are reviewed in PRs).
