"""Synthetic marketplace orders + a chaos engine that breaks them in realistic ways.

Used by the demo, the tests and the benchmark. Everything is seeded and deterministic.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

CITIES = ["Pune", "Mumbai", "Bengaluru", "Hyderabad", "Delhi", "Chennai", "Nagpur", "Nashik"]
CITY_P = [0.18, 0.2, 0.17, 0.12, 0.14, 0.09, 0.05, 0.05]
CATEGORIES = ["Electronics", "Grocery", "Fashion", "Home", "Beauty", "Books"]
CAT_P = [0.14, 0.3, 0.22, 0.12, 0.12, 0.1]
CAT_BASE = {"Electronics": 9000, "Grocery": 450, "Fashion": 1400, "Home": 2200, "Beauty": 700, "Books": 380}
PAY = ["UPI", "CARD", "COD", "WALLET", "NETBANKING"]
PAY_P = [0.52, 0.2, 0.14, 0.09, 0.05]

CHAOS_MODES = [
    "null_burst", "schema_drift", "type_drift", "duplicate_flood", "volume_drop",
    "outlier_amount", "stale_data", "format_drift", "silent_drift",
]
# Which incident type we expect the platform to raise for each failure mode.
EXPECTED_INCIDENT = {
    "null_burst": "contract_violation",
    "schema_drift": "schema_break",
    "type_drift": "contract_violation",
    "duplicate_flood": "contract_violation",
    "volume_drop": "volume_anomaly",
    "outlier_amount": "contract_violation",
    "stale_data": "freshness_breach",
    "format_drift": "high_heal_rate",
    "silent_drift": "distribution_drift",
}


def generate_orders(n: int, run_ts: datetime, seed: int = 0, id_offset: int = 0, window_minutes: int = 60) -> pd.DataFrame:
    """Orders whose event_ts fall in the hour *before* ``run_ts``."""
    rng = np.random.default_rng(seed)
    cat = rng.choice(CATEGORIES, size=n, p=CAT_P)
    base = np.array([CAT_BASE[c] for c in cat], dtype=float)
    amount = np.round(base * rng.lognormal(mean=0.0, sigma=0.45, size=n), 2).clip(1.0, 150000.0)
    qty = rng.choice([1, 1, 1, 2, 2, 3, 4], size=n)
    offsets = rng.uniform(1, window_minutes, size=n)
    ts = [run_ts - timedelta(minutes=float(m)) for m in offsets]
    return pd.DataFrame(
        {
            "order_id": [f"ORD-{id_offset + i + 1:08d}" for i in range(n)],
            "customer_id": [f"C{x:05d}" for x in rng.integers(1, 20000, size=n)],
            "city": rng.choice(CITIES, size=n, p=CITY_P),
            "category": cat,
            "amount": amount,
            "quantity": qty.astype(int),
            "payment_mode": rng.choice(PAY, size=n, p=PAY_P),
            "event_ts": pd.to_datetime(ts, utc=True),
        }
    )


def inject_chaos(df: pd.DataFrame, mode: str, seed: int = 0) -> pd.DataFrame:
    """Return a broken copy of ``df``. ``mode`` must be one of ``CHAOS_MODES``."""
    if mode not in CHAOS_MODES:
        raise ValueError(f"unknown chaos mode '{mode}', choose from {CHAOS_MODES}")
    rng = np.random.default_rng(seed + 7919)
    out = df.copy()
    n = len(out)
    pick = lambda frac: rng.random(n) < frac  # noqa: E731

    if mode == "null_burst":
        out.loc[pick(0.15), "customer_id"] = None
    elif mode == "schema_drift":
        out = out.rename(columns={"amount": "order_amount"})
    elif mode == "type_drift":
        out["amount"] = out["amount"].astype(object)
        garbage = pick(0.2)
        out.loc[garbage, "amount"] = rng.choice(["N/A", "--", "abc"], size=int(garbage.sum()))
    elif mode == "duplicate_flood":
        dup = out.sample(frac=0.25, random_state=seed)
        out = pd.concat([out, dup], ignore_index=True)
    elif mode == "volume_drop":
        out = out.sample(frac=0.08, random_state=seed).reset_index(drop=True)
    elif mode == "outlier_amount":
        m = pick(0.05)
        out.loc[m, "amount"] = out.loc[m, "amount"] * 400.0
    elif mode == "stale_data":
        out["event_ts"] = out["event_ts"] - pd.Timedelta(hours=6)
    elif mode == "format_drift":
        m = pick(0.3)
        out["amount"] = out["amount"].astype(object)
        out.loc[m, "amount"] = ["₹" + f"{a:,.2f}" for a in df.loc[m, "amount"]]
        m2 = pick(0.3)
        lowered = out["city"].astype(object)
        lowered[m2] = [c.lower() if i % 2 else (" " + c) for i, c in enumerate(lowered[m2])]
        out["city"] = lowered
        m3 = pick(0.1)
        out.loc[m3, "city"] = out.loc[m3, "city"].replace({"Bengaluru": "Bangalore", "Mumbai": "Bombay"})
    elif mode == "silent_drift":
        out["amount"] = (out["amount"] * 1.8).round(2).clip(upper=190000.0)
    return out


def batch_size(rng: np.random.Generator, base: int = 1200) -> int:
    return int(base * rng.uniform(0.92, 1.08))


def utc(y: int, mo: int, d: int, h: int = 0, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=timezone.utc)
