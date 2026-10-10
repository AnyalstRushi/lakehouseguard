"""Reproducible benchmark. Every number in the README and the deck comes from this script.

  python benchmarks/run_benchmark.py --seeds 20

Measures, over N independent seeds (each seed = fresh lakehouse, 8 healthy warm-up batches):
  * detection: was *any* incident raised for each failure mode, and was the *expected* type raised
  * false positives: incidents raised on healthy batches
  * alert noise reduction from causal triage (raw incidents -> primary incident per bad batch)
  * agent top-1 root cause on the synthetic failure library (self-consistency, not external validity)
  * row-level gate recall/precision for the row-level failure modes
  * throughput of the pure-Python engine
"""
import argparse
import json
import statistics
import sys
import tempfile
import time
from datetime import timedelta
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lakehouseguard.agent import IncidentAgent, triage  # noqa: E402
from lakehouseguard.contracts import Contract  # noqa: E402
from lakehouseguard.generator import CHAOS_MODES, EXPECTED_INCIDENT, batch_size, generate_orders, inject_chaos, utc  # noqa: E402
from lakehouseguard.pipeline import Pipeline  # noqa: E402

TRUE_CAUSE = {
    "null_burst": "join or lookup", "schema_drift": "renamed", "type_drift": "placeholders", "duplicate_flood": "idempotency",
    "volume_drop": "Partial extract", "outlier_amount": "scaling", "stale_data": "Timezone", "format_drift": "formatting",
    "silent_drift": "multiplier",
}


def one_seed(seed: int, contract: Contract) -> dict:
    tmp = tempfile.TemporaryDirectory()
    pipe = Pipeline(tmp.name, contract)
    agent = IncidentAgent(pipe.lake, contract)
    rng = np.random.default_rng(seed)
    t, off = utc(2026, 10, 10, 0), 0
    out = {"modes": {}, "healthy_runs": 0, "healthy_false_positives": 0}

    def nxt():
        nonlocal t, off
        t += timedelta(hours=1)
        n = batch_size(rng)
        df = generate_orders(n, t, seed=int(rng.integers(1, 10**6)), id_offset=off)
        off += n
        return df

    for _ in range(8):
        pipe.run_batch(nxt(), t)
    for mode in CHAOS_MODES:
        df = nxt()
        bad = inject_chaos(df, mode, seed=int(rng.integers(1, 10**6)))
        r = pipe.run_batch(bad, t, batch_id=f"{mode}-{seed}")
        primary, symptoms = triage(r.incidents)
        top = agent.analyze(primary, symptoms).hypotheses[0]["cause"] if primary else ""
        out["modes"][mode] = {
            "detected": bool(r.incidents), "expected_type": EXPECTED_INCIDENT[mode] in r.incident_types,
            "raw_incidents": len(r.incidents), "primary_incidents": 1 if primary else 0,
            "top1_correct": TRUE_CAUSE[mode].lower() in top.lower(), "rows_quarantined": r.rows_quarantined, "rows_in": r.rows_in,
            "primary_type": primary.type if primary else None,
        }
    for _ in range(5):
        r = pipe.run_batch(nxt(), t)
        out["healthy_runs"] += 1
        out["healthy_false_positives"] += int(bool(r.incidents))
    tmp.cleanup()
    return out


def row_level(contract: Contract, seeds: int) -> dict:
    from lakehouseguard.contracts import validate
    res = {}
    for mode, truth in {
        "null_burst": lambda b, g: b["customer_id"].isna().to_numpy(),
        "outlier_amount": lambda b, g: (b["amount"] != g["amount"]).to_numpy(),
    }.items():
        tp = fp = fn = 0
        for s in range(seeds):
            good = generate_orders(1200, utc(2026, 10, 10, 5), seed=s)
            bad = inject_chaos(good, mode, seed=s)
            v = validate(bad, contract)
            flagged = bad.index.isin(v.quarantine.index)
            truth_mask = truth(bad, good)
            tp += int((flagged & truth_mask).sum())
            fp += int((flagged & ~truth_mask).sum())
            fn += int((~flagged & truth_mask).sum())
        res[mode] = {"recall": tp / (tp + fn) if tp + fn else 1.0, "precision": tp / (tp + fp) if tp + fp else 1.0}
    return res


def throughput(contract: Contract, rows: int = 20000, reps: int = 3) -> dict:
    times = []
    for _ in range(reps):
        with tempfile.TemporaryDirectory() as d:
            pipe = Pipeline(d, contract)
            df = generate_orders(rows, utc(2026, 10, 10, 5), seed=1)
            t0 = time.perf_counter()
            pipe.run_batch(df, utc(2026, 10, 10, 5))
            times.append(time.perf_counter() - t0)
    med = statistics.median(times)
    return {"rows": rows, "seconds": round(med, 3), "rows_per_sec": int(rows / med)}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--out", default=str(ROOT / "benchmarks" / "results.json"))
    a = ap.parse_args()
    contract = Contract.from_yaml(ROOT / "contracts" / "orders.yaml")
    runs = [one_seed(s, contract) for s in range(a.seeds)]
    modes = {}
    for m in CHAOS_MODES:
        rows = [r["modes"][m] for r in runs]
        modes[m] = {
            "detection_rate": sum(x["detected"] for x in rows) / len(rows),
            "expected_type_rate": sum(x["expected_type"] for x in rows) / len(rows),
            "top1_root_cause": sum(x["top1_correct"] for x in rows) / len(rows),
            "raw_incidents_avg": round(statistics.mean(x["raw_incidents"] for x in rows), 2),
            "primary_type_mode": max(set(x["primary_type"] for x in rows), key=[x["primary_type"] for x in rows].count),
        }
    healthy = sum(r["healthy_runs"] for r in runs)
    fps = sum(r["healthy_false_positives"] for r in runs)
    raw = sum(x["raw_incidents"] for r in runs for x in r["modes"].values())
    prim = sum(x["primary_incidents"] for r in runs for x in r["modes"].values())
    n = len(runs) * len(CHAOS_MODES)
    result = {
        "seeds": a.seeds, "failure_batches": n, "healthy_batches": healthy,
        "overall_detection_rate": sum(m["detection_rate"] for m in modes.values()) / len(modes),
        "overall_expected_type_rate": sum(m["expected_type_rate"] for m in modes.values()) / len(modes),
        "overall_top1_root_cause": sum(m["top1_root_cause"] for m in modes.values()) / len(modes),
        "false_positive_rate": fps / healthy, "false_positive_batches": fps,
        "raw_incidents": raw, "after_triage": prim, "alert_reduction": 1 - prim / raw if raw else 0,
        "modes": modes, "row_level": row_level(contract, a.seeds), "throughput": throughput(contract),
    }
    Path(a.out).write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "modes"}, indent=2))
    print(f"\n{'failure mode':<18}{'detected':>9}{'right type':>11}{'top-1 cause':>12}{'raw alerts':>11}")
    for m, v in modes.items():
        print(f"{m:<18}{v['detection_rate']:>9.0%}{v['expected_type_rate']:>11.0%}{v['top1_root_cause']:>12.0%}{v['raw_incidents_avg']:>11}")


if __name__ == "__main__":
    main()
