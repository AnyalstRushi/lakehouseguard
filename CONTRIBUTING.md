# Contributing

```bash
pip install -e ".[dev]"
pytest -q                       # ~40 s
coverage run -m pytest -q && coverage report
ruff check .
```

Add a failure mode: extend `CHAOS_MODES` and `EXPECTED_INCIDENT` in `generator.py`, add a runbook section in `src/lakehouseguard/knowledge/`, and a test.
Add an incident type: raise it from `observability.py` or `pipeline.contract_findings`, add hypotheses in `agent.hypothesize`, a runbook, and a test.
