import unittest
import tempfile
from pathlib import Path

import pandas as pd

from tests.helpers import contract
from lakehouseguard.contracts import Contract, ContractError, validate, coerce_timestamp
from lakehouseguard.generator import generate_orders, utc


def good(n=50):
    return generate_orders(n, utc(2026, 10, 10, 5), seed=3)


class ContractLoading(unittest.TestCase):
    def test_loads_yaml(self):
        c = contract()
        self.assertEqual(c.name, "orders")
        self.assertIn("amount", c.column_names)
        self.assertEqual(c.primary_key, ("order_id",))
        self.assertEqual(c.column("city").aliases["Bangalore"], "Bengaluru")

    def test_unknown_column_raises(self):
        with self.assertRaises(KeyError):
            contract().column("nope")

    def test_rejects_bad_contracts(self):
        base = {"name": "x", "version": "1", "columns": [{"name": "a", "type": "string"}]}
        Contract.from_dict(base)
        with self.assertRaises(ContractError):
            Contract.from_dict({"name": "x"})
        with self.assertRaises(ContractError):
            Contract.from_dict({**base, "columns": [{"name": "a"}]})
        with self.assertRaises(ContractError):
            Contract.from_dict({**base, "columns": [{"name": "a", "type": "blob"}]})
        with self.assertRaises(ContractError):
            Contract.from_dict({**base, "columns": [{"name": "a", "type": "string"}, {"name": "a", "type": "int"}]})
        with self.assertRaises(ContractError):
            Contract.from_dict({**base, "primary_key": ["zzz"]})

    def test_yaml_must_be_mapping(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "bad.yaml"
            p.write_text("- just\n- a list\n")
            with self.assertRaises(ContractError):
                Contract.from_yaml(p)


class Validation(unittest.TestCase):
    def setUp(self):
        self.c = contract()

    def test_clean_batch_passes(self):
        r = validate(good(), self.c)
        self.assertEqual(len(r.clean), 50)
        self.assertEqual(r.violations, [])
        self.assertEqual(r.quality_score, 100.0)
        self.assertFalse(r.blocked)

    def test_empty_batch(self):
        r = validate(good().iloc[0:0], self.c)
        self.assertEqual(r.quality_score, 0.0)
        self.assertEqual(r.quarantine_fraction, 0.0)

    def test_not_null(self):
        df = good()
        df.loc[:2, "customer_id"] = None
        r = validate(df, self.c)
        self.assertEqual(len(r.quarantine), 3)
        self.assertEqual({v.key for v in r.violations}, {"not_null:customer_id"})
        self.assertIn("not_null:customer_id", r.quarantine["_violations"].iloc[0])

    def test_pattern_and_allowed_values(self):
        df = good()
        df.loc[0, "order_id"] = "bad-id"
        df.loc[1, "city"] = "Atlantis"
        r = validate(df, self.c)
        keys = {v.key for v in r.violations}
        self.assertEqual(keys, {"pattern:order_id", "allowed_values:city"})

    def test_range_min_and_max(self):
        df = good()
        df.loc[0, "amount"] = 0.5
        df.loc[1, "amount"] = 9e6
        df.loc[2, "quantity"] = 99
        r = validate(df, self.c)
        rng = {v.key: v.count for v in r.violations}
        self.assertEqual(rng["range:amount"], 2)
        self.assertEqual(rng["range:quantity"], 1)

    def test_type_violation_and_fractional_int(self):
        df = good()
        df["amount"] = df["amount"].astype(object)
        df.loc[0, "amount"] = "abc"
        df["quantity"] = df["quantity"].astype(float)
        df.loc[1, "quantity"] = 2.5
        r = validate(df, self.c)
        keys = {v.key for v in r.violations}
        self.assertIn("type:amount", keys)
        self.assertIn("type:quantity", keys)

    def test_timestamp_type(self):
        df = good()
        df["event_ts"] = df["event_ts"].astype(str)
        df.loc[0, "event_ts"] = "not a date"
        r = validate(df, self.c)
        self.assertEqual([v.key for v in r.violations], ["type:event_ts"])

    def test_unique_keeps_first(self):
        df = good()
        df = pd.concat([df, df.iloc[:5]], ignore_index=True)
        r = validate(df, self.c)
        self.assertEqual(len(r.quarantine), 5)
        self.assertEqual(len(r.clean), 50)
        self.assertEqual(r.violations[0].rule, "unique")

    def test_missing_column_blocks_batch(self):
        df = good().rename(columns={"amount": "order_amount"})
        r = validate(df, self.c)
        self.assertTrue(r.blocked)
        self.assertEqual(r.block_reason, "schema_break")
        self.assertEqual(r.missing_columns, ["amount"])
        self.assertEqual(r.unexpected_columns, ["order_amount"])
        self.assertEqual(len(r.clean), 0)
        self.assertEqual(len(r.quarantine), 50)

    def test_circuit_breaker(self):
        df = good(20)
        df.loc[:14, "city"] = "Atlantis"   # 75% bad
        r = validate(df, self.c)
        self.assertTrue(r.blocked)
        self.assertEqual(r.block_reason, "circuit_breaker")
        self.assertEqual(len(r.clean), 0)
        self.assertEqual(len(r.quarantine), 20)
        self.assertEqual(r.quarantine["_violations"].iloc[-1], "circuit_breaker")

    def test_timestamp_helper_handles_naive(self):
        s = pd.Series(pd.to_datetime(["2026-01-01"]))
        self.assertEqual(str(coerce_timestamp(s).dt.tz), "UTC")
        s2 = pd.Series(pd.to_datetime(["2026-01-01"], utc=True)).dt.tz_convert("Asia/Kolkata")
        self.assertEqual(str(coerce_timestamp(s2).dt.tz), "UTC")

    def test_violation_to_dict(self):
        df = good()
        df.loc[0, "customer_id"] = None
        d = validate(df, self.c).violations[0].to_dict()
        self.assertEqual(d["rule"], "not_null")
        self.assertEqual(d["count"], 1)


if __name__ == "__main__":
    unittest.main()
