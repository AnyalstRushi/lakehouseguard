# Runbook: high_heal_rate

## Symptoms
Many rows needed automatic repair (whitespace trimming, case normalisation, aliases such as Bangalore to Bengaluru, currency-formatted numbers). The data still loaded, but the upstream format is degrading.

## Likely causes
- Producer changed formatting (currency symbols and thousand separators in numbers, new casing).
- A new client or app version sending free-text values.

## Remediation
1. Review the heal actions on the run: they show which column and repair fired and how often.
2. Ask the producer to emit canonical values. Auto-heal is a safety net, not a contract.
3. If a new alias is legitimate, add it to the contract `aliases` map.

## Prevention
Keep heal rate under 10%. Self-healing must never hide a degrading upstream, so a high rate raises its own incident.
