"""Deterministic, auditable self-healing.

Only *unambiguous* repairs are applied: whitespace trimming, case/alias normalisation to a
contract's allowed values, and parsing currency-formatted numbers. Every repair is counted.
A high heal rate is itself an incident (see observability): healing must never hide a
degrading upstream.
"""
from __future__ import annotations

import re

import pandas as pd

from .contracts import Contract

_CURRENCY = re.compile(r"(?i)(₹|rs\.?|inr|\$|usd|,|\s)")


def heal(df: pd.DataFrame, contract: Contract) -> tuple[pd.DataFrame, dict[str, int], int]:
    """Return (healed_df, actions, rows_healed). ``actions`` maps ``"<repair>:<column>"`` -> cell count."""
    out = df.copy()
    actions: dict[str, int] = {}
    touched = pd.Series(False, index=out.index)

    def note(label: str, col: str, changed: pd.Series) -> None:
        cnt = int(changed.sum())
        if cnt:
            actions[f"{label}:{col}"] = cnt
            touched.loc[changed[changed].index] = True

    for col in contract.columns:
        if col.name not in out.columns:
            continue
        s = out[col.name]
        if col.type == "string":
            if s.isna().all():
                continue
            as_str = s.astype("string")
            trimmed = as_str.str.strip()
            note("trim", col.name, (as_str != trimmed).fillna(False))
            cur = trimmed
            if col.allowed_values:
                lookup = {v.lower(): v for v in col.allowed_values}
                for alias, target in col.aliases.items():
                    lookup[alias.lower()] = target
                mapped = cur.str.lower().map(lookup)
                fixed = mapped.where(mapped.notna(), cur)
                note("normalize", col.name, (cur != fixed).fillna(False))
                cur = fixed
            out[col.name] = cur.astype(object).where(cur.notna(), None)
        elif col.type in ("int", "float"):
            if pd.api.types.is_numeric_dtype(s):
                continue
            is_text = s.map(lambda v: isinstance(v, str))
            if not is_text.any():
                continue
            txt = s.where(is_text).astype("string")
            parsed = pd.to_numeric(txt.str.replace(_CURRENCY, "", regex=True), errors="coerce")
            fixed = is_text & parsed.notna()
            note("numeric_parse", col.name, fixed)
            result = s.astype(object).copy()
            result[fixed] = parsed[fixed]
            # unparseable garbage keeps its original text so validation can quarantine it
            out[col.name] = pd.to_numeric(result, errors="coerce").where(~(is_text & ~fixed), result)
    return out, actions, int(touched.sum())
