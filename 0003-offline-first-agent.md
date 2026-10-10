# ADR 0003: Offline-first, deterministic incident agent with an optional LLM

Status: accepted

## Context
A portfolio project that needs a paid API key cannot be cloned and run in one command, and LLM answers are hard to test.

## Decision
Triage, hypotheses and retrieval are deterministic (rules + TF-IDF). An `LLMClient` protocol (Ollama client included) can rephrase the answer from retrieved facts only.

## Consequences
+ Runs anywhere, fully testable, no hallucinated numbers. - Hypotheses are as good as the playbook; unknown failures fall back to "Unclassified".
