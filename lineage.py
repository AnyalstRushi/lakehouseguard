"""Minimal OpenLineage-style lineage: events per job run + a static dataset graph."""
from __future__ import annotations

from typing import Any

# dataset -> upstream datasets
GRAPH: dict[str, list[str]] = {
    "source.orders_api": [],
    "bronze.orders": ["source.orders_api"],
    "silver.orders": ["bronze.orders"],
    "quarantine.orders": ["bronze.orders"],
    "gold.fact_sales_daily": ["silver.orders"],
    "gold.kpi_daily": ["gold.fact_sales_daily"],
}


def upstream(dataset: str) -> list[str]:
    seen: list[str] = []
    stack = list(GRAPH.get(dataset, []))
    while stack:
        d = stack.pop(0)
        if d not in seen:
            seen.append(d)
            stack.extend(GRAPH.get(d, []))
    return seen


def downstream(dataset: str) -> list[str]:
    out: list[str] = []
    frontier = [dataset]
    while frontier:
        cur = frontier.pop(0)
        for child, parents in GRAPH.items():
            if cur in parents and child not in out:
                out.append(child)
                frontier.append(child)
    return out


def event(job: str, run_id: str, ts: str, inputs: list[str], outputs: list[str], facets: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "eventType": "COMPLETE",
        "eventTime": ts,
        "run": {"runId": run_id, "facets": facets or {}},
        "job": {"namespace": "lakehouseguard", "name": job},
        "inputs": [{"namespace": "lakehouseguard", "name": n} for n in inputs],
        "outputs": [{"namespace": "lakehouseguard", "name": n} for n in outputs],
        "producer": "https://github.com/AnyalstRushi/lakehouseguard",
    }
