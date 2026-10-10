# Observability

Contracts catch **known** failure shapes. Observability catches the **unknown**: data that is valid but wrong.

| Detector | Method | Default threshold |
|---|---|---|
| Freshness | age of newest `event_ts` vs contract SLA | `freshness_minutes` |
| Volume | robust z-score on row count: (x - median) / (1.4826 x MAD), MAD floored at 5% of median | abs(z) > 3.5 |
| Null-rate drift | per-column rate vs baseline mean + max(4 sigma, 2 points) | see left |
| Schema drift | added / removed / retyped columns vs last healthy run | any change |
| Distribution drift | Population Stability Index vs a reference sample (deciles for numeric, shares for categorical) | PSI > 0.2 |
| Heal rate | share of rows needing auto-repair | > 10% |

PSI = sum((a_i - e_i) x ln(a_i / e_i)) over bins, with a tiny floor to avoid log(0). Rule of thumb: <0.1 stable, 0.1-0.25 moderate shift, >0.25 major.

## The baseline only learns from healthy runs

If any incident fires, the batch does not update the baseline. Otherwise a slow-motion failure teaches the platform that bad is normal.
The baseline needs 5 healthy runs before volume, null, schema and drift checks activate (freshness works from run one).

## Silent drift

The demo includes a 1.8x scaling of `amount` that stays inside the contract's allowed range. Every row passes the contract; only PSI
notices that the data stopped looking like yesterday's data. This is the case that justifies having both layers.
