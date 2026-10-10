# Data contracts

A contract is a YAML file that is the single source of truth for a dataset. See `contracts/orders.yaml`.

```yaml
name: orders
version: 1.2.0
owner: data-platform@lakehouseguard.dev
sla:
  freshness_minutes: 120
  min_rows_per_batch: 200
primary_key: [order_id]
columns:
  - name: amount
    type: float            # string | int | float | timestamp
    nullable: false
    min: 1.0
    max: 200000.0
  - name: city
    type: string
    allowed_values: [Pune, Mumbai, ...]
    aliases: {Bangalore: Bengaluru}   # safe auto-heal mapping
```

## Rules

| Key | Meaning | Violation name |
|---|---|---|
| `nullable: false` | value required | `not_null` |
| `type` | value must parse as the type (ints must be whole numbers) | `type` |
| `min` / `max` | numeric range | `range` |
| `pattern` | full-match regex | `pattern` |
| `allowed_values` | closed set | `allowed_values` |
| `unique: true` | first occurrence kept, repeats rejected | `unique` |
| missing column | schema break, batch blocked | `missing_column` |

Extra columns are allowed (reported as `schema_drift`), because additive changes are usually safe.

## Using a contract in a producer's CI

```bash
lakehouseguard validate sample_extract.csv --contract contracts/orders.yaml   # exit code 2 on any violation
```

Treat a contract like an API: additive changes bump the minor version; renames, removals and tighter rules bump the major.
