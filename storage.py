"""Tiny table store for the Medallion layers.

Tables are directories of per-batch partitions: ``<root>/<layer>/<table>/batch_id=<id>.<ext>``.
Writing the same ``batch_id`` twice overwrites that partition, which makes every run
idempotent. Parquet is used when ``pyarrow`` is installed, CSV otherwise.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

import pandas as pd

try:  # pragma: no cover - exercised only when pyarrow is installed
    import pyarrow  # noqa: F401
    HAS_PARQUET = True
except Exception:  # pragma: no cover
    HAS_PARQUET = False

_SAFE = re.compile(r"[^A-Za-z0-9_.=-]")


def safe_name(value: str) -> str:
    return _SAFE.sub("_", str(value))


class Lakehouse:
    def __init__(self, root: str | Path, fmt: str | None = None):
        self.root = Path(root)
        self.fmt = fmt or ("parquet" if HAS_PARQUET else "csv")
        if self.fmt not in ("csv", "parquet"):
            raise ValueError("fmt must be 'csv' or 'parquet'")
        self.root.mkdir(parents=True, exist_ok=True)

    # --- helpers -------------------------------------------------------
    def table_dir(self, layer: str, table: str) -> Path:
        return self.root / layer / table

    def ops_dir(self) -> Path:
        d = self.root / "ops"
        d.mkdir(parents=True, exist_ok=True)
        return d

    # --- partitioned tables -------------------------------------------
    def write_partition(self, layer: str, table: str, batch_id: str, df: pd.DataFrame) -> Path:
        d = self.table_dir(layer, table)
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"batch_id={safe_name(batch_id)}.{'parquet' if self.fmt == 'parquet' else 'csv'}"
        if self.fmt == "parquet":  # pragma: no cover
            df.to_parquet(path, index=False)
        else:
            df.to_csv(path, index=False)
        return path

    def read_table(self, layer: str, table: str) -> pd.DataFrame:
        d = self.table_dir(layer, table)
        if not d.exists():
            return pd.DataFrame()
        files = sorted(d.glob("batch_id=*"))
        frames = []
        for f in files:
            if f.suffix == ".parquet":  # pragma: no cover
                frames.append(pd.read_parquet(f))
            else:
                frames.append(pd.read_csv(f))
        frames = [f for f in frames if len(f)]
        return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()

    def partitions(self, layer: str, table: str) -> list[str]:
        d = self.table_dir(layer, table)
        return sorted(p.stem.split("=", 1)[1] for p in d.glob("batch_id=*")) if d.exists() else []

    def delete_partition(self, layer: str, table: str, batch_id: str) -> bool:
        d = self.table_dir(layer, table)
        hit = False
        for ext in ("csv", "parquet"):
            p = d / f"batch_id={safe_name(batch_id)}.{ext}"
            if p.exists():
                p.unlink()
                hit = True
        return hit

    def write_table(self, layer: str, table: str, df: pd.DataFrame) -> Path:
        """Overwrite a whole (non-partitioned) table such as a Gold mart."""
        d = self.table_dir(layer, table)
        d.mkdir(parents=True, exist_ok=True)
        for old in d.glob("batch_id=*"):
            old.unlink()
        return self.write_partition(layer, table, "full", df)

    # --- jsonl logs ----------------------------------------------------
    def append_jsonl(self, name: str, record: dict[str, Any]) -> None:
        with open(self.ops_dir() / name, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(record, default=str) + "\n")

    def read_jsonl(self, name: str) -> list[dict[str, Any]]:
        p = self.ops_dir() / name
        if not p.exists():
            return []
        with open(p, "r", encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def rewrite_jsonl(self, name: str, records: Iterable[dict[str, Any]]) -> None:
        with open(self.ops_dir() / name, "w", encoding="utf-8") as fh:
            for r in records:
                fh.write(json.dumps(r, default=str) + "\n")
