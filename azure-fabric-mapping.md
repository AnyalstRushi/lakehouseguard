# Mapping to Azure, Databricks and Microsoft Fabric

The core is engine-agnostic on purpose. Each concept maps one-to-one onto the managed services used in production.

| LakehouseGuard | Azure Databricks | Microsoft Fabric | Azure services |
|---|---|---|---|
| Bronze / Silver / Gold partitions | Delta tables (Unity Catalog) | Lakehouse tables in OneLake | ADLS Gen2 |
| Contract gate | Lakeflow expectations (`EXPECT ... ON VIOLATION`) or this gate as a job | Notebook step, Dataflow Gen2 validation | Azure Data Factory data flow assert |
| Quarantine | Delta quarantine table | Lakehouse quarantine table | ADLS container |
| Idempotent batch partitions | `replaceWhere` / `MERGE` on `_batch_id` | Same on Delta | |
| Observability baseline | Delta table of run profiles + Lakehouse Monitoring | Delta table + Real-Time Intelligence | Azure Monitor / Log Analytics |
| Incidents + alerts | Workflows alerts | Fabric Activator | Logic Apps / Teams webhook |
| Lineage events | Unity Catalog lineage | Purview lineage | Microsoft Purview |
| Orchestration | Databricks Workflows | Data Factory pipeline in Fabric | ADF / Airflow |
| Dashboard | Databricks SQL dashboard | Power BI over `gold` | Power BI |
| Cost model | System tables billing | Capacity Metrics app | Cost Management |

`examples/spark_engine.py` shows the contract gate as PySpark + Delta (idempotent per-batch write via delete + append).

## Migration path for a real team

1. Keep the YAML contracts; generate Spark expressions from them (as `spark_engine.gate` does).
2. Run the gate as a Databricks Workflows task or a Fabric notebook between Bronze and Silver.
3. Persist run profiles and incidents to Delta tables instead of JSONL.
4. Point the dashboard at those tables (Power BI), keep the agent runbooks as a versioned folder.
