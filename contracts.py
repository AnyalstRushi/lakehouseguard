"""Data contracts: declarative expectations that gate the Bronze -> Silver boundary.

A contract is a YAML file (see ``contracts/orders.yaml``). ``validate`` splits a batch into
clean rows and quarantined rows (each tagged with every rule it broke) and reports
rule-level violations. Nothing is dropped silently: every rejected row is kept in quarantine.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import yaml

SUPPORTED_TYPES = {"string", "int", "float", "timestamp"}


class ContractError(ValueError):
    """Raised when a contract file is malformed."""


@dataclass(frozen=True)
class Column:
    name: str
    type: str
    nullable: bool = True
    unique: bool = False
    pattern: str | None = None
    allowed_values: tuple[str, ...] | None = None
    min: float | None = None
    max: float | None = None
    aliases: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class Contract:
    name: str
    version: str
    owner: str
    description: str
    sla: dict[str, Any]
    primary_key: tuple[str, ...]
    columns: tuple[Column, ...]

    @property
    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def column(self, name: str) -> Column:
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> "Contract":
        for key in ("name", "version", "columns"):
            if key not in raw:
                raise ContractError(f"contract missing required key '{key}'")
        cols = []
        for c in raw["columns"]:
            if "name" not in c or "type" not in c:
                raise ContractError("every column needs 'name' and 'type'")
            if c["type"] not in SUPPORTED_TYPES:
                raise ContractError(f"unsupported type '{c['type']}' for column '{c['name']}'")
            allowed = tuple(c["allowed_values"]) if c.get("allowed_values") else None
            cols.append(
                Column(
                    name=c["name"],
                    type=c["type"],
                    nullable=bool(c.get("nullable", True)),
                    unique=bool(c.get("unique", False)),
                    pattern=c.get("pattern"),
                    allowed_values=allowed,
                    min=c.get("min"),
                    max=c.get("max"),
                    aliases=dict(c.get("aliases", {})),
                )
            )
        names = [c.name for c in cols]
        if len(set(names)) != len(names):
            raise ContractError("duplicate column names in contract")
        pk = tuple(raw.get("primary_key", []))
        for k in pk:
            if k not in names:
                raise ContractError(f"primary key column '{k}' not in columns")
        return cls(
            name=raw["name"],
            version=str(raw["version"]),
            owner=raw.get("owner", "unknown"),
            description=raw.get("description", ""),
            sla=dict(raw.get("sla", {})),
            primary_key=pk,
            columns=tuple(cols),
        )

    @classmethod
    def from_yaml(cls, path: str | Path) -> "Contract":
        with open(path, "r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh)
        if not isinstance(raw, dict):
            raise ContractError("contract file must contain a YAML mapping")
        return cls.from_dict(raw)


@dataclass
class Violation:
    rule: str          # e.g. "not_null", "range", "type", "unique", "pattern", "allowed_values"
    column: str
    count: int
    pct: float
    sample: list[Any]

    @property
    def key(self) -> str:
        return f"{self.rule}:{self.column}"

    def to_dict(self) -> dict[str, Any]:
        return {"rule": self.rule, "column": self.column, "count": self.count,
                "pct": round(self.pct, 4), "sample": self.sample}


@dataclass
class ValidationResult:
    total_rows: int
    clean: pd.DataFrame
    quarantine: pd.DataFrame
    violations: list[Violation]
    missing_columns: list[str]
    unexpected_columns: list[str]
    blocked: bool = False
    block_reason: str | None = None

    @property
    def quarantine_fraction(self) -> float:
        return 0.0 if self.total_rows == 0 else len(self.quarantine) / self.total_rows

    @property
    def quality_score(self) -> float:
        """Share of rows that passed every rule, 0-100."""
        if self.total_rows == 0:
            return 0.0
        return round(100.0 * len(self.clean) / self.total_rows, 2)


def coerce_timestamp(s: pd.Series) -> pd.Series:
    if pd.api.types.is_datetime64_any_dtype(s):
        return s.dt.tz_localize("UTC") if s.dt.tz is None else s.dt.tz_convert("UTC")
    return pd.to_datetime(s, utc=True, errors="coerce", format="ISO8601")


def coerce_numeric(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _sample(series: pd.Series, mask: np.ndarray, n: int = 3) -> list[Any]:
    vals = series[mask].head(n).tolist()
    return [None if (isinstance(v, float) and np.isnan(v)) else (v if isinstance(v, (int, float, str)) or v is None else str(v)) for v in vals]


def validate(df: pd.DataFrame, contract: Contract, max_quarantine_fraction: float = 0.5) -> ValidationResult:
    """Validate ``df`` against ``contract``.

    Policy:
    * missing contract column -> schema break, the whole batch is blocked;
    * every other rule is evaluated per row; failing rows go to quarantine;
    * circuit breaker: if more than ``max_quarantine_fraction`` of rows fail, the whole batch
      is blocked, because publishing a heavily-filtered partial batch would bias Gold KPIs.
    """
    n = len(df)
    names = contract.column_names
    missing = [c for c in names if c not in df.columns]
    unexpected = [c for c in df.columns if c not in names and not str(c).startswith("_")]

    if missing:
        q = df.copy()
        q["_violations"] = "missing_column:" + ",".join(missing)
        viol = [Violation("missing_column", m, n, 1.0, []) for m in missing]
        return ValidationResult(n, df.iloc[0:0].copy(), q, viol, missing, unexpected, True, "schema_break")

    fail: dict[str, np.ndarray] = {}
    violations: list[Violation] = []

    def record(rule: str, col: str, mask: np.ndarray, source: pd.Series) -> None:
        cnt = int(mask.sum())
        if cnt == 0:
            return
        fail[f"{rule}:{col}"] = mask
        violations.append(Violation(rule, col, cnt, cnt / max(n, 1), _sample(source, mask)))

    for col in contract.columns:
        raw = df[col.name]
        is_null = raw.isna().to_numpy()
        if not col.nullable:
            record("not_null", col.name, is_null, raw)

        # --- type ---
        if col.type in ("int", "float"):
            num = coerce_numeric(raw)
            bad_type = (num.isna() & ~raw.isna()).to_numpy()
            if col.type == "int":
                frac = (num.notna() & (num != np.floor(num.fillna(0)))).to_numpy()
                bad_type = bad_type | frac
            record("type", col.name, bad_type, raw)
            oor = np.zeros(n, dtype=bool)
            if col.min is not None:
                oor |= ((num < col.min) & num.notna()).to_numpy()
            if col.max is not None:
                oor |= ((num > col.max) & num.notna()).to_numpy()
            record("range", col.name, oor, raw)
        elif col.type == "timestamp":
            ts = coerce_timestamp(raw)
            record("type", col.name, (ts.isna() & ~raw.isna()).to_numpy(), raw)
        else:  # string
            s = raw.astype("string")
            if col.pattern:
                ok = s.str.fullmatch(col.pattern).fillna(True).astype(bool).to_numpy()
                record("pattern", col.name, ~ok, raw)
            if col.allowed_values:
                ok = s.isin(list(col.allowed_values)).to_numpy() | is_null
                record("allowed_values", col.name, ~ok, raw)

        if col.unique:
            dup = raw.duplicated(keep="first").to_numpy() & ~is_null
            record("unique", col.name, dup, raw)

    if not fail:
        return ValidationResult(n, df.copy(), df.iloc[0:0].copy(), [], [], unexpected)

    any_fail = np.zeros(n, dtype=bool)
    for m in fail.values():
        any_fail |= m
    keys = list(fail.keys())
    matrix = np.vstack([fail[k] for k in keys]).T
    reasons = pd.Series(
        [";".join(k for k, hit in zip(keys, row) if hit) if row.any() else "" for row in matrix],
        index=df.index,
    )
    quarantine = df[any_fail].copy()
    quarantine["_violations"] = reasons[any_fail]
    clean = df[~any_fail].copy()
    frac = quarantine.shape[0] / max(n, 1)
    if frac > max_quarantine_fraction:
        q_all = df.copy()
        q_all["_violations"] = reasons.where(reasons != "", "circuit_breaker")
        return ValidationResult(n, df.iloc[0:0].copy(), q_all, violations, [], unexpected, True, "circuit_breaker")
    return ValidationResult(n, clean, quarantine, violations, [], unexpected)
