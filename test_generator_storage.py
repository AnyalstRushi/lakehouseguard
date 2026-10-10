import tempfile
import unittest

import pandas as pd

from lakehouseguard.generator import CHAOS_MODES, EXPECTED_INCIDENT, generate_orders, inject_chaos, utc
from lakehouseguard.storage import Lakehouse, safe_name
from tests.helpers import contract
from lakehouseguard.contracts import validate


class Generator(unittest.TestCase):
    def test_deterministic_and_contract_clean(self):
        a = generate_orders(200, utc(2026, 10, 10, 3), seed=9)
        b = generate_orders(200, utc(2026, 10, 10, 3), seed=9)
        pd.testing.assert_frame_equal(a, b)
        self.assertEqual(validate(a, contract()).quality_score, 100.0)
        self.assertEqual(a["order_id"].is_unique, True)

    def test_id_offset(self):
        a = generate_orders(5, utc(2026, 10, 10, 3), seed=1, id_offset=100)
        self.assertEqual(a["order_id"].iloc[0], "ORD-00000101")

    def test_every_mode_changes_data(self):
        base = generate_orders(400, utc(2026, 10, 10, 3), seed=2)
        for m in CHAOS_MODES:
            bad = inject_chaos(base, m, seed=1)
            self.assertFalse(bad.equals(base), m)
            self.assertIn(m, EXPECTED_INCIDENT)

    def test_unknown_mode(self):
        with self.assertRaises(ValueError):
            inject_chaos(generate_orders(5, utc(2026, 10, 10, 3)), "nope")

    def test_mode_shapes(self):
        base = generate_orders(400, utc(2026, 10, 10, 3), seed=2)
        self.assertIn("order_amount", inject_chaos(base, "schema_drift").columns)
        self.assertLess(len(inject_chaos(base, "volume_drop")), 60)
        self.assertGreater(len(inject_chaos(base, "duplicate_flood")), 480)
        self.assertGreater(inject_chaos(base, "null_burst")["customer_id"].isna().sum(), 20)


class Storage(unittest.TestCase):
    def test_partition_roundtrip_and_overwrite(self):
        with tempfile.TemporaryDirectory() as d:
            lake = Lakehouse(d, fmt="csv")
            lake.write_partition("bronze", "t", "b1", pd.DataFrame({"a": [1, 2]}))
            lake.write_partition("bronze", "t", "b2", pd.DataFrame({"a": [3]}))
            lake.write_partition("bronze", "t", "b1", pd.DataFrame({"a": [9]}))   # idempotent overwrite
            self.assertEqual(sorted(lake.read_table("bronze", "t")["a"]), [3, 9])
            self.assertEqual(lake.partitions("bronze", "t"), ["b1", "b2"])
            self.assertTrue(lake.delete_partition("bronze", "t", "b1"))
            self.assertFalse(lake.delete_partition("bronze", "t", "b1"))

    def test_missing_and_empty_tables(self):
        with tempfile.TemporaryDirectory() as d:
            lake = Lakehouse(d)
            self.assertTrue(lake.read_table("x", "y").empty)
            self.assertEqual(lake.partitions("x", "y"), [])
            lake.write_partition("x", "y", "b", pd.DataFrame({"a": []}))
            self.assertTrue(lake.read_table("x", "y").empty)

    def test_write_table_replaces(self):
        with tempfile.TemporaryDirectory() as d:
            lake = Lakehouse(d, fmt="csv")
            lake.write_table("gold", "k", pd.DataFrame({"a": [1]}))
            lake.write_table("gold", "k", pd.DataFrame({"a": [2, 3]}))
            self.assertEqual(len(lake.read_table("gold", "k")), 2)

    def test_jsonl_and_rewrite(self):
        with tempfile.TemporaryDirectory() as d:
            lake = Lakehouse(d)
            self.assertEqual(lake.read_jsonl("n.jsonl"), [])
            lake.append_jsonl("n.jsonl", {"a": 1})
            lake.append_jsonl("n.jsonl", {"a": 2})
            self.assertEqual(len(lake.read_jsonl("n.jsonl")), 2)
            lake.rewrite_jsonl("n.jsonl", [{"a": 5}])
            self.assertEqual(lake.read_jsonl("n.jsonl"), [{"a": 5}])

    def test_bad_format_and_safe_name(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                Lakehouse(d, fmt="avro")
        self.assertEqual(safe_name("a/b c"), "a_b_c")


if __name__ == "__main__":
    unittest.main()
