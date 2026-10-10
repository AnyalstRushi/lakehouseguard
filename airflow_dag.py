"""Reference Airflow DAG: hourly extract -> contract-gated load -> dashboard refresh.

Not executed in CI. Shows how the library drops into an orchestrator: each task is idempotent
because partitions are keyed by ``batch_id``, so retries and backfills are safe.
"""
from datetime import datetime, timezone

from airflow.decorators import dag, task

from lakehouseguard.contracts import Contract
from lakehouseguard.generator import generate_orders
from lakehouseguard.pipeline import Pipeline
from lakehouseguard.report import build_report


@dag(schedule="@hourly", start_date=datetime(2026, 10, 1), catchup=True, max_active_runs=1, tags=["lakehouseguard"])
def orders_lakehouse():
    @task(retries=2)
    def load(logical_date=None):
        run_ts = logical_date.astimezone(timezone.utc)
        contract = Contract.from_yaml("contracts/orders.yaml")
        pipe = Pipeline("/data/lakehouse", contract)
        df = generate_orders(1200, run_ts, seed=int(run_ts.timestamp()))   # replace with your extract
        result = pipe.run_batch(df, run_ts)
        if result.status == "blocked":
            raise RuntimeError(f"batch blocked by contract gate: {result.incident_types}")
        return result.batch_id

    @task
    def refresh_dashboard(_batch_id: str):
        contract = Contract.from_yaml("contracts/orders.yaml")
        build_report(Pipeline("/data/lakehouse", contract).lake, contract, "/data/site")

    refresh_dashboard(load())


orders_lakehouse()
