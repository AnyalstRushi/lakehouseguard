"""Smoke test for examples/spark_engine.py (used by the optional CI job)."""
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "examples"))

from spark_engine import build_spark, write_batch  # noqa: E402

from lakehouseguard.contracts import Contract  # noqa: E402
from lakehouseguard.generator import generate_orders, utc  # noqa: E402


def main() -> None:
    contract = Contract.from_yaml(ROOT / "contracts" / "orders.yaml")
    spark = build_spark()
    pdf = generate_orders(500, utc(2026, 10, 10, 5), seed=1)
    pdf["event_ts"] = pdf["event_ts"].astype(str)
    pdf.loc[:24, "customer_id"] = None
    with tempfile.TemporaryDirectory() as root:
        first = write_batch(spark, spark.createDataFrame(pdf), contract, root, "b1")
        again = write_batch(spark, spark.createDataFrame(pdf), contract, root, "b1")   # idempotent replay
        assert first == again == {"clean": 475, "quarantined": 25}, (first, again)
    print("spark gate OK", first)
    spark.stop()


if __name__ == "__main__":
    main()
