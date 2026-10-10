"""End-to-end demo: warm up on healthy data, then break the pipeline nine different ways."""
from __future__ import annotations

import shutil
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np

from .contracts import Contract
from .generator import CHAOS_MODES, batch_size, generate_orders, inject_chaos, utc
from .pipeline import Pipeline, RunResult


def run_demo(root: str | Path, contract: Contract, seed: int = 42, warmup: int = 10,
             start: datetime | None = None, reset: bool = True, modes: list[str] | None = None) -> tuple[Pipeline, list[RunResult]]:
    root = Path(root)
    if reset and root.exists():
        shutil.rmtree(root)
    pipe = Pipeline(root, contract)
    rng = np.random.default_rng(seed)
    t = start or utc(2026, 10, 10, 0)
    offset, results = 0, []

    def healthy() -> RunResult:
        nonlocal t, offset
        t += timedelta(hours=1)
        n = batch_size(rng)
        df = generate_orders(n, t, seed=int(rng.integers(1, 10**6)), id_offset=offset)
        offset += n
        return pipe.run_batch(df, t)

    for _ in range(warmup):
        results.append(healthy())
    for mode in (modes or CHAOS_MODES):
        t += timedelta(hours=1)
        n = batch_size(rng)
        df = generate_orders(n, t, seed=int(rng.integers(1, 10**6)), id_offset=offset)
        offset += n
        results.append(pipe.run_batch(inject_chaos(df, mode, seed=int(rng.integers(1, 10**6))), t, batch_id=f"{t:%Y%m%dT%H%M}-{mode}"))
        results.append(healthy())
    return pipe, results
