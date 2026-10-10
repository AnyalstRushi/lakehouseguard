import sys
import tempfile
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouseguard.contracts import Contract  # noqa: E402
from lakehouseguard.generator import batch_size, generate_orders, utc  # noqa: E402
from lakehouseguard.pipeline import Pipeline  # noqa: E402

CONTRACT_PATH = ROOT / "contracts" / "orders.yaml"


def contract() -> Contract:
    return Contract.from_yaml(CONTRACT_PATH)


class Env:
    """A warmed-up pipeline plus a clock and a batch factory."""

    def __init__(self, warmup: int = 8, seed: int = 1):
        self.tmp = tempfile.TemporaryDirectory()
        self.pipe = Pipeline(self.tmp.name, contract())
        self.rng = np.random.default_rng(seed)
        self.t = utc(2026, 10, 10, 0)
        self.offset = 0
        self.results = [self.healthy() for _ in range(warmup)]

    def batch(self, seed=None):
        self.t += timedelta(hours=1)
        n = batch_size(self.rng)
        df = generate_orders(n, self.t, seed=seed if seed is not None else int(self.rng.integers(1, 10**6)), id_offset=self.offset)
        self.offset += n
        return df

    def healthy(self):
        df = self.batch()
        return self.pipe.run_batch(df, self.t)

    def run(self, df):
        return self.pipe.run_batch(df, self.t)

    def close(self):
        self.tmp.cleanup()
