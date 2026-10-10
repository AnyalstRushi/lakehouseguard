"""Data observability: learn what "healthy" looks like, then flag anything that deviates.

Contracts catch *known* failure shapes. Observability catches the unknown ones:
volume anomalies, freshness, null-rate drift, schema drift and silent distribution drift
(values still valid, but the data is no longer the data you had yesterday).

The baseline is only ever updated from *healthy* runs, so bad data cannot teach the
platform that bad is normal.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import numpy as np
import pandas as pd

from .contracts import Contract, coerce_numeric, coerce_timestamp
from .storage import Lakehouse

MIN_HISTORY = 5
REFERENCE_CAP = 6000


@dataclass(frozen=True)
class Thresholds:
    volume_robust_z: float = 3.5
    null_abs: float = 0.02
    null_sigma: float = 4.0
    psi: float = 0.2
    heal_rate: float = 0.10


@dataclass
class Finding:
    type: str
    severity: str
    title: str
    evidence: dict[str, Any]
    column: str | None = None


def psi_numeric(reference: pd.Series, current: pd.Series, bins: int = 10) -> float:
    ref = pd.to_numeric(reference, errors="coerce").dropna()
    cur = pd.to_numeric(current, errors="coerce").dropna()
    if len(ref) < 20 or len(cur) < 20:
        return 0.0
    edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
    if len(edges) < 3:
        return 0.0
    edges[0], edges[-1] = -np.inf, np.inf
    e = np.histogram(ref, bins=edges)[0] / len(ref)
    a = np.histogram(cur, bins=edges)[0] / len(cur)
    e, a = np.clip(e, 1e-4, None), np.clip(a, 1e-4, None)
    return float(np.sum((a - e) * np.log(a / e)))


def psi_categorical(reference: pd.Series, current: pd.Series) -> float:
    ref = reference.dropna().astype(str).value_counts(normalize=True)
    cur = current.dropna().astype(str).value_counts(normalize=True)
    if len(ref) == 0 or len(cur) == 0:
        return 0.0
    cats = ref.index.union(cur.index)
    e = ref.reindex(cats).fillna(0).clip(lower=1e-4).to_numpy()
    a = cur.reindex(cats).fillna(0).clip(lower=1e-4).to_numpy()
    return float(np.sum((a - e) * np.log(a / e)))


def profile(df: pd.DataFrame, contract: Contract) -> dict[str, Any]:
    cols = [c for c in df.columns if not str(c).startswith("_")]
    nulls = {c: float(df[c].isna().mean()) if len(df) else 0.0 for c in cols}
    max_ts = None
    if "event_ts" in df.columns and len(df):
        ts = coerce_timestamp(df["event_ts"]).dropna()
        max_ts = ts.max().isoformat() if len(ts) else None
    return {
        "rows": int(len(df)),
        "columns": sorted(cols),
        "dtypes": {c: _kind(df[c]) for c in cols},
        "null_rates": nulls,
        "max_event_ts": max_ts,
    }


def _kind(s: pd.Series) -> str:
    if pd.api.types.is_bool_dtype(s):
        return "bool"
    if pd.api.types.is_numeric_dtype(s):
        return "number"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "timestamp"
    return "text"


class Baseline:
    """Healthy-run history: per-run profiles + a capped reference sample."""

    PROFILES = "baseline_profiles.jsonl"
    REFERENCE = "baseline_reference.csv"

    def __init__(self, lake: Lakehouse):
        self.lake = lake

    @property
    def profiles(self) -> list[dict[str, Any]]:
        return self.lake.read_jsonl(self.PROFILES)

    @property
    def ready(self) -> bool:
        return len(self.profiles) >= MIN_HISTORY

    def reference(self) -> pd.DataFrame | None:
        p = self.lake.ops_dir() / self.REFERENCE
        return pd.read_csv(p) if p.exists() else None

    def update(self, prof: dict[str, Any], clean_df: pd.DataFrame) -> None:
        self.lake.append_jsonl(self.PROFILES, prof)
        ref = self.reference()
        sample = clean_df.drop(columns=[c for c in clean_df.columns if str(c).startswith("_")], errors="ignore")
        merged = pd.concat([ref, sample], ignore_index=True) if ref is not None else sample.copy()
        if len(merged) > REFERENCE_CAP:
            merged = merged.sample(REFERENCE_CAP, random_state=7).reset_index(drop=True)
        merged.to_csv(self.lake.ops_dir() / self.REFERENCE, index=False)


class Detector:
    def __init__(self, contract: Contract, baseline: Baseline, thresholds: Thresholds | None = None):
        self.contract = contract
        self.baseline = baseline
        self.t = thresholds or Thresholds()

    def detect(self, df: pd.DataFrame, prof: dict[str, Any], run_ts: datetime, heal_rate: float = 0.0) -> list[Finding]:
        out: list[Finding] = []
        out += self._freshness(prof, run_ts)
        out += self._heal(heal_rate)
        if self.baseline.ready:
            out += self._volume(prof)
            out += self._schema(prof)
            out += self._nulls(prof)
            out += self._drift(df)
        return out

    # -- detectors -------------------------------------------------------
    def _freshness(self, prof: dict[str, Any], run_ts: datetime) -> list[Finding]:
        limit = self.contract.sla.get("freshness_minutes")
        if limit is None or prof["max_event_ts"] is None:
            return []
        age = (pd.Timestamp(run_ts) - pd.Timestamp(prof["max_event_ts"])).total_seconds() / 60.0
        if age <= limit:
            return []
        return [Finding("freshness_breach", "high",
                        f"Newest event is {age:.0f} min old (SLA {limit} min)",
                        {"age_minutes": round(age, 1), "sla_minutes": limit, "max_event_ts": prof["max_event_ts"]}, "event_ts")]

    def _heal(self, rate: float) -> list[Finding]:
        if rate <= self.t.heal_rate:
            return []
        return [Finding("high_heal_rate", "medium",
                        f"{rate:.0%} of rows needed auto-repair (limit {self.t.heal_rate:.0%})",
                        {"heal_rate": round(rate, 4), "limit": self.t.heal_rate})]

    def _volume(self, prof: dict[str, Any]) -> list[Finding]:
        hist = np.array([p["rows"] for p in self.baseline.profiles], dtype=float)
        med = float(np.median(hist))
        mad = float(np.median(np.abs(hist - med)) * 1.4826)
        mad = max(mad, 0.05 * med, 1.0)
        z = (prof["rows"] - med) / mad
        if abs(z) <= self.t.volume_robust_z:
            return []
        direction = "drop" if z < 0 else "spike"
        return [Finding("volume_anomaly", "high" if direction == "drop" else "medium",
                        f"Row volume {direction}: {prof['rows']} rows vs typical {med:.0f}",
                        {"rows": prof["rows"], "baseline_median": med, "robust_z": round(float(z), 2),
                         "ratio": round(prof["rows"] / med, 3), "direction": direction})]

    def _schema(self, prof: dict[str, Any]) -> list[Finding]:
        last = self.baseline.profiles[-1]
        added = sorted(set(prof["columns"]) - set(last["columns"]))
        removed = sorted(set(last["columns"]) - set(prof["columns"]))
        changed = sorted(c for c in set(prof["columns"]) & set(last["columns"]) if prof["dtypes"][c] != last["dtypes"][c])
        if not (added or removed or changed):
            return []
        return [Finding("schema_drift", "medium", "Schema differs from healthy baseline",
                        {"added": added, "removed": removed, "retyped": changed})]

    def _nulls(self, prof: dict[str, Any]) -> list[Finding]:
        out = []
        hist = self.baseline.profiles
        for col, rate in prof["null_rates"].items():
            past = np.array([h["null_rates"].get(col, 0.0) for h in hist])
            limit = float(past.mean() + max(self.t.null_sigma * past.std(), self.t.null_abs))
            if rate > limit:
                out.append(Finding("null_rate_drift", "medium",
                                   f"Null rate on {col} is {rate:.1%} (baseline {past.mean():.1%})",
                                   {"null_rate": round(rate, 4), "baseline_mean": round(float(past.mean()), 4), "limit": round(limit, 4)}, col))
        return out

    def _drift(self, df: pd.DataFrame) -> list[Finding]:
        ref = self.baseline.reference()
        if ref is None or len(df) == 0:
            return []
        out = []
        for col in self.contract.columns:
            if col.name not in df.columns or col.name not in ref.columns:
                continue
            if col.type in ("int", "float"):
                score = psi_numeric(ref[col.name], coerce_numeric(df[col.name]))
                kind = "numeric"
            elif col.type == "string" and col.allowed_values:
                score = psi_categorical(ref[col.name], df[col.name])
                kind = "categorical"
            else:
                continue
            if score > self.t.psi:
                cur = coerce_numeric(df[col.name]) if kind == "numeric" else None
                ev = {"psi": round(score, 3), "threshold": self.t.psi, "kind": kind}
                if cur is not None:
                    ev["baseline_mean"] = round(float(pd.to_numeric(ref[col.name], errors="coerce").mean()), 2)
                    ev["current_mean"] = round(float(cur.mean()), 2)
                out.append(Finding("distribution_drift", "high" if score > 0.5 else "medium",
                                   f"{col.name} distribution drifted (PSI {score:.2f})", ev, col.name))
        return out
