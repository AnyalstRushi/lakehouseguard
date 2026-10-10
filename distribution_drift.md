# Runbook: distribution_drift

## Symptoms
Every row is valid, yet a column's distribution moved (population stability index above 0.2). Contracts cannot see this: for example order amounts all rose 80% while staying within the allowed range.

## Likely causes
- Currency or unit change (paise to rupees, tax now included), a price list update, a new product mix.
- Genuine business change such as a sale or a new city launch.
- A producer applying a multiplier or a conversion bug.

## Remediation
1. Compare baseline and current means in the incident evidence; a clean multiplier (for example 1.8x) points to a unit or conversion bug.
2. Check with the business whether a promotion, price change or launch explains it.
3. If it is a bug, fix upstream and replay. If it is real, accept the new normal: resolve the incident and let the baseline re-learn from the next healthy runs.

## Prevention
Keep drift monitoring on key numeric and categorical columns. Pair data observability with a business calendar of launches and price changes.
