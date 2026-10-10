"""The Medallion pipeline: Bronze -> (heal, contract gate, observe) -> Silver / Quarantine -> Gold."""
from __future__ import annotations

import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from . import lineage
from .contracts import Contract, ValidationResult, coerce_timestamp, validate
from .cost import CostModel
from .healing import heal
from .incidents import Incident, IncidentStore
from .observability import Baseline, Detector, Finding, Thresholds, profile
from .storage import Lakehouse

DATASET = "orders"


@dataclass
class RunResult:
    run_id: str
    batch_id: str
    ts: str
    status: str                      # success | degraded | blocked
    rows_in: int
    rows_clean: int
    rows_quarantined: int
    rows_healed: int
    quality_score: float
    duration_s: float
    cost_inr: float
    wasted_inr: float
    revenue_clean: float
    heal_actions: dict[str, int] = field(default_factory=dict)
    incidents: list[Incident] = field(default_factory=list)

    @property
    def incident_types(self) -> list[str]:
        return sorted({i.type for i in self.incidents})

    def to_record(self) -> dict[str, Any]:
        d = asdict(self)
        d["incident_ids"] = [i.id for i in self.incidents]
        d["incident_types"] = self.incident_types
        d.pop("incidents")
        return d


def contract_findings(res: ValidationResult) -> list[Finding]:
    out: list[Finding] = []
    if res.missing_columns:
        out.append(Finding("schema_break", "critical",
                           f"Required column(s) missing: {', '.join(res.missing_columns)}",
                           {"missing": res.missing_columns, "unexpected": res.unexpected_columns, "rows": res.total_rows,
                            "rule": "missing_column"}))
        return out
    if res.block_reason == "circuit_breaker":
        out.append(Finding("contract_violation", "critical",
                           f"Circuit breaker: {res.quarantine_fraction:.0%} of rows failed the contract",
                           {"rule": "circuit_breaker", "quarantine_fraction": round(res.quarantine_fraction, 4)}))
    for v in res.violations:
        out.append(Finding("contract_violation", "high" if v.pct > 0.2 else "medium",
                           f"{v.rule} violated on {v.column}: {v.count} rows ({v.pct:.1%})",
                           {"rule": v.rule, **v.to_dict()}, v.column))
    return out


class Pipeline:
    def __init__(self, root: str | Path, contract: Contract, cost_model: CostModel | None = None,
                 thresholds: Thresholds | None = None, fmt: str | None = None, max_quarantine_fraction: float = 0.5):
        self.lake = Lakehouse(root, fmt)
        self.contract = contract
        self.cost_model = cost_model or CostModel()
        self.baseline = Baseline(self.lake)
        self.detector = Detector(contract, self.baseline, thresholds)
        self.store = IncidentStore(self.lake)
        self.max_q = max_quarantine_fraction

    # ------------------------------------------------------------------
    def run_batch(self, df: pd.DataFrame, run_ts: datetime, batch_id: str | None = None, source: str = "orders_api") -> RunResult:
        t0 = time.perf_counter()
        batch_id = batch_id or f"b{run_ts:%Y%m%dT%H%M}"
        run_id = f"run-{batch_id}"
        ts_iso = run_ts.isoformat()
        rows_in = len(df)

        bronze = df.copy()
        bronze["_batch_id"] = batch_id
        bronze["_ingested_at"] = ts_iso
        self.lake.write_partition("bronze", DATASET, batch_id, bronze)

        healed, actions, rows_healed = heal(df, self.contract)
        heal_rate = rows_healed / max(rows_in, 1)
        res = validate(healed, self.contract, self.max_q)
        prof = profile(healed, self.contract)

        findings = self.detector.detect(healed, prof, run_ts, heal_rate) + contract_findings(res)
        incidents = self._persist(findings, run_id, ts_iso)

        # Silver / quarantine
        if res.blocked:
            self.lake.delete_partition("silver", DATASET, batch_id)
        else:
            silver = res.clean.copy()
            silver["event_ts"] = coerce_timestamp(silver["event_ts"])
            silver["_batch_id"] = batch_id
            self.lake.write_partition("silver", DATASET, batch_id, silver)
        q = res.quarantine.copy()
        if len(q):
            q["_batch_id"] = batch_id
            self.lake.write_partition("quarantine", DATASET, batch_id, q)
        else:
            self.lake.delete_partition("quarantine", DATASET, batch_id)

        self.build_gold()

        healthy = not findings and not res.blocked
        if healthy:
            self.baseline.update(prof, res.clean)

        duration = time.perf_counter() - t0
        cost = self.cost_model.run_cost(duration, rows_in)
        wasted = self.cost_model.wasted(cost["total_inr"], rows_in, len(res.quarantine), res.blocked)
        revenue = float(pd.to_numeric(res.clean["amount"], errors="coerce").sum()) if "amount" in res.clean.columns else 0.0
        status = "blocked" if res.blocked else ("degraded" if findings else "success")

        result = RunResult(
            run_id=run_id, batch_id=batch_id, ts=ts_iso, status=status, rows_in=rows_in,
            rows_clean=len(res.clean), rows_quarantined=len(res.quarantine), rows_healed=rows_healed,
            quality_score=res.quality_score if rows_in else 0.0, duration_s=round(duration, 4),
            cost_inr=cost["total_inr"], wasted_inr=wasted, revenue_clean=round(revenue, 2),
            heal_actions=actions, incidents=incidents,
        )
        self.lake.append_jsonl("runs.jsonl", result.to_record())
        self._lineage(run_id, ts_iso, batch_id, result, source)
        return result

    # ------------------------------------------------------------------
    def _persist(self, findings: list[Finding], run_id: str, ts: str) -> list[Incident]:
        out = []
        for f in findings:
            inc = Incident(run_id=run_id, ts=ts, type=f.type, severity=f.severity, dataset=DATASET,
                           title=f.title, evidence=f.evidence, column=f.column)
            self.store.add(inc)
            out.append(inc)
        return out

    def build_gold(self) -> None:
        silver = self.lake.read_table("silver", DATASET)
        if silver.empty:
            self.lake.write_table("gold", "fact_sales_daily", pd.DataFrame(columns=["date", "city", "category", "orders", "units", "revenue", "avg_order_value"]))
            self.lake.write_table("gold", "kpi_daily", pd.DataFrame(columns=["date", "orders", "revenue", "avg_order_value", "unique_customers"]))
            return
        silver["event_ts"] = coerce_timestamp(silver["event_ts"])
        silver["date"] = silver["event_ts"].dt.strftime("%Y-%m-%d")
        silver["amount"] = pd.to_numeric(silver["amount"], errors="coerce")
        fact = (silver.groupby(["date", "city", "category"], as_index=False)
                .agg(orders=("order_id", "nunique"), units=("quantity", "sum"), revenue=("amount", "sum")))
        fact["revenue"] = fact["revenue"].round(2)
        fact["avg_order_value"] = (fact["revenue"] / fact["orders"]).round(2)
        kpi = (silver.groupby("date", as_index=False)
               .agg(orders=("order_id", "nunique"), revenue=("amount", "sum"), unique_customers=("customer_id", "nunique")))
        kpi["revenue"] = kpi["revenue"].round(2)
        kpi["avg_order_value"] = (kpi["revenue"] / kpi["orders"]).round(2)
        kpi = kpi[["date", "orders", "revenue", "avg_order_value", "unique_customers"]]
        self.lake.write_table("gold", "fact_sales_daily", fact)
        self.lake.write_table("gold", "kpi_daily", kpi)

    def _lineage(self, run_id: str, ts: str, batch_id: str, r: RunResult, source: str) -> None:
        jobs = [
            ("bronze_ingest", [f"source.{source}"], ["bronze.orders"], {"rows": r.rows_in}),
            ("contract_gate", ["bronze.orders"], ["silver.orders", "quarantine.orders"],
             {"clean": r.rows_clean, "quarantined": r.rows_quarantined, "healed": r.rows_healed, "contract": self.contract.version}),
            ("gold_build", ["silver.orders"], ["gold.fact_sales_daily", "gold.kpi_daily"], {"status": r.status}),
        ]
        for job, ins, outs, facets in jobs:
            self.lake.append_jsonl("lineage.jsonl", lineage.event(job, run_id, ts, ins, outs, facets))

    def runs(self) -> list[dict[str, Any]]:
        return self.lake.read_jsonl("runs.jsonl")
