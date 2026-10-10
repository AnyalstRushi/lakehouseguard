# Changelog

## 1.0.0 (2026-10-10)
- Contract-gated Medallion pipeline (Bronze, Silver, Quarantine, Gold), idempotent per-batch partitions
- Self-healing with guardrails and heal-rate monitoring
- Observability: freshness, volume (robust z), null drift, schema drift, distribution drift (PSI)
- Incident store, causal triage, RCA with hypotheses, blast radius and runbook retrieval
- Offline-first incident agent with optional Ollama hook
- Static HTML dashboard, CLI, chaos engine with 9 failure modes, benchmark, CI/CD, Docker
