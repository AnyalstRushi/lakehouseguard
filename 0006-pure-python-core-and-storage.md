# ADR 0006: Pure-Python core; CSV by default, Parquet when pyarrow is present; Spark as a reference engine

Status: accepted

## Decision
The core uses pandas and PyYAML so it installs in seconds and the whole suite runs offline in about 40 seconds. Storage is CSV, or Parquet if `pyarrow` is installed (`pip install .[parquet]`).
The Spark + Delta version of the gate is provided in `examples/` and checked by an optional CI job.

## Consequences
+ Fast feedback, easy contributions, one-command demo. - Throughput is single-process (~33k rows/s measured); the Spark path is the scale story and is exercised only by the smoke test.
