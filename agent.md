# The incident agent

Offline-first: **no API key, no network, deterministic.** An LLM is an optional presentation layer.

1. **Triage** collapses a run's alerts into one primary incident plus symptoms using a causal rule table.
   A schema break explains the nulls, drift and volume alerts it causes; duplicates explain a volume spike; a type violation explains the retype.
2. **Hypotheses**: evidence-driven candidates with normalised confidence. Example: a missing column plus a new column in the same batch makes
   "renamed upstream" the top hypothesis; an event age that is a whole number of hours points to a timezone bug; a mean that moved 1.73x with all values in range
   points to a unit or conversion multiplier.
3. **Retrieval (RAG)**: TF-IDF over the runbooks and the data contract finds remediation steps and related guidance.
4. **Blast radius**: lineage gives affected downstream assets; run metrics give rows affected and estimated revenue at risk.
5. **Q&A**: `lakehouseguard ask "why did revenue drop?"` routes to revenue, cost, status, fix, incident or search intents.

## Optional LLM

```bash
ollama pull llama3.1
lakehouseguard ask "why did revenue drop?" --ollama llama3.1
```

The model only receives the retrieved facts and is told not to invent numbers. If the server is unreachable the deterministic answer is returned.
Why TF-IDF and not embeddings: the corpus is a few dozen short runbook sections, exact vocabulary matters, and it runs anywhere with zero downloads.
Swapping in embeddings is a one-class change (`KnowledgeBase`).

## Limits

- Hypotheses come from a playbook, not from learning; unusual failure types fall back to "Unclassified incident".
- Confidence is a normalised weight, not a calibrated probability.
- It explains and proposes; it never edits data or contracts on its own.
