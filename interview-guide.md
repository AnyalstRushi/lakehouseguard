# Interview guide

## 30-second pitch
"Most pipeline projects stop at ETL. I built the layer that makes a pipeline trustworthy: YAML data contracts that gate Bronze to Silver,
a quarantine instead of silent drops, observability that catches drift contracts cannot see, and an offline AI agent that triages alerts,
proposes a root cause and links the runbook. Nine injected failure modes are all detected with zero false positives across 100 healthy batches in my benchmark."

## 2-minute walkthrough
1. Problem: bad data reaches dashboards silently; alerts are noisy; nobody knows the cause.
2. Show the dashboard: health, quality trend, incidents.
3. Chaos demo: `lakehouseguard demo`; open the schema-break RCA ("renamed amount to order_amount", blast radius, runbook, patch).
4. The clever case: silent drift (1.8x amounts, every row valid). Only observability sees it.
5. Close with the Azure mapping: same ideas on Databricks / Fabric.

## Questions you should expect (and honest answers)

**Why contracts at the Bronze to Silver boundary?** Bronze must stay raw and replayable. Silver is the first layer others trust, so that is where promises are enforced. See ADR 0001.

**What is the difference between a contract failure and an observability alert?** A contract checks a row against a rule you wrote. Observability checks a batch against what normal looked like. Contracts block; observability alerts.

**Why not just use Great Expectations / Soda / dbt tests?** They are excellent for rule checks. This project's point is the system around them: quarantine, circuit breaker, baseline hygiene, triage and RCA. I would happily plug GE or Soda into the gate in production.

**How does the circuit breaker work and why 50%?** If more than half of a batch fails, publishing the survivors would bias KPIs, so the whole batch is held. 50% is configurable; pick it from how much bias your KPIs tolerate.

**How do you avoid alert fatigue?** Causal triage (schema break explains nulls, drift and volume), de-duplicated incident ids, recurrence counts. In the benchmark it removed 29% of raw alerts.

**Is self-healing dangerous?** Only unambiguous repairs (trim, case and alias mapping to the contract's allowed values, parsing currency formatting). Every repair is counted, and a repair rate above 10% raises its own incident so healing cannot mask a degrading source. Anything ambiguous is quarantined, never guessed.

**Why TF-IDF instead of embeddings or an LLM?** Small corpus, exact vocabulary, zero dependencies, deterministic and testable. The LLM hook is optional and only sees retrieved facts.

**How would it scale?** Pandas core handles ~33k rows/s in the benchmark. For volume, the same gate runs as Spark expressions (`examples/spark_engine.py`), profiles go to Delta, and Gold becomes incremental instead of a full rebuild.

**What does 100% detection mean?** Honest answer: it detects the nine failures I designed it against. It is not a claim about unseen failures. The caveats are in `benchmarks/RESULTS.md`.

**What would you do next?** Per-segment range rules (400x outliers on cheap categories slip under a global max, 73% row-level recall), embeddings retrieval, incremental Gold, dbt-core as a Gold engine, OpenLineage to Marquez, auto-opened tickets.

## Things to say you learned
Baseline poisoning is a real failure mode. Contracts and drift detection are complements. Idempotent partitions make retries and backfills boring. Treat alert volume as a product metric.
