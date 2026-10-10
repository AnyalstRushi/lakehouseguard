"""Reference implementation of the contract gate on PySpark + Delta Lake.

The pure-Python core in ``src/`` is what the tests and benchmarks exercise. This module shows the
same Bronze -> contract gate -> Silver / Quarantine flow on a Spark engine (Databricks, Fabric
notebooks, or local Spark). It is exercised by the optional ``spark-smoke`` CI job, not by the
main test suite.

    pip install -e ".[spark]" && python examples/spark_smoke.py
"""
from __future__ import annotations

from pyspark.sql import DataFrame, SparkSession, Window
from pyspark.sql import functions as F

from lakehouseguard.contracts import Contract


def build_spark(app: str = "lakehouseguard") -> SparkSession:
    from delta import configure_spark_with_delta_pip

    builder = (
        SparkSession.builder.appName(app).master("local[2]")
        .config("spark.sql.extensions", "io.delta.sql.DeltaSparkSessionExtension")
        .config("spark.sql.catalog.spark_catalog", "org.apache.spark.sql.delta.catalog.DeltaCatalog")
        .config("spark.sql.shuffle.partitions", "4")
    )
    return configure_spark_with_delta_pip(builder).getOrCreate()


def gate(df: DataFrame, contract: Contract) -> DataFrame:
    """Add an ``_violations`` array column listing every rule a row breaks (empty array = clean)."""
    checks = []
    for col in contract.columns:
        c = F.col(col.name)
        if not col.nullable:
            checks.append(F.when(c.isNull(), F.lit(f"not_null:{col.name}")))
        if col.type in ("int", "float"):
            num = c.cast("double")
            checks.append(F.when(c.isNotNull() & num.isNull(), F.lit(f"type:{col.name}")))
            if col.min is not None:
                checks.append(F.when(num < F.lit(float(col.min)), F.lit(f"range:{col.name}")))
            if col.max is not None:
                checks.append(F.when(num > F.lit(float(col.max)), F.lit(f"range:{col.name}")))
        if col.pattern:
            checks.append(F.when(c.isNotNull() & ~c.cast("string").rlike(col.pattern), F.lit(f"pattern:{col.name}")))
        if col.allowed_values:
            checks.append(F.when(c.isNotNull() & ~c.isin(*col.allowed_values), F.lit(f"allowed_values:{col.name}")))
        if col.unique:
            w = Window.partitionBy(c).orderBy(F.monotonically_increasing_id())
            checks.append(F.when(F.row_number().over(w) > 1, F.lit(f"unique:{col.name}")))
    return df.withColumn("_violations", F.filter(F.array(*checks), lambda x: x.isNotNull()))


def write_batch(spark: SparkSession, df: DataFrame, contract: Contract, root: str, batch_id: str) -> dict:
    """Idempotent per-batch write: delete the batch's partition, then append."""
    from delta.tables import DeltaTable

    gated = gate(df, contract).withColumn("_batch_id", F.lit(batch_id))
    clean = gated.filter(F.size("_violations") == 0).drop("_violations")
    quarantine = gated.filter(F.size("_violations") > 0)
    for name, frame in (("silver", clean), ("quarantine", quarantine)):
        path = f"{root}/{name}/{contract.name}"
        if DeltaTable.isDeltaTable(spark, path):
            DeltaTable.forPath(spark, path).delete(F.col("_batch_id") == batch_id)
        frame.write.format("delta").mode("append").partitionBy("_batch_id").save(path)
    return {"clean": clean.count(), "quarantined": quarantine.count()}
