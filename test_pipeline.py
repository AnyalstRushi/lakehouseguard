import unittest

import pandas as pd

from tests.helpers import Env
from lakehouseguard.generator import CHAOS_MODES, EXPECTED_INCIDENT, generate_orders, inject_chaos


class HealthyOperation(unittest.TestCase):
    def test_warmup_has_no_false_positives(self):
        env = Env(warmup=14, seed=7)
        try:
            self.assertTrue(all(r.status == "success" for r in env.results), [r.incident_types for r in env.results])
            self.assertTrue(all(r.quality_score == 100.0 for r in env.results))
        finally:
            env.close()

    def test_layers_are_populated(self):
        env = Env(warmup=6)
        try:
            lake = env.pipe.lake
            self.assertEqual(len(lake.partitions("bronze", "orders")), 6)
            self.assertEqual(len(lake.partitions("silver", "orders")), 6)
            gold = lake.read_table("gold", "fact_sales_daily")
            kpi = lake.read_table("gold", "kpi_daily")
            self.assertGreater(len(gold), 0)
            silver = lake.read_table("silver", "orders")
            self.assertEqual(int(kpi["orders"].sum()), silver["order_id"].nunique())
            self.assertAlmostEqual(float(kpi["revenue"].sum()), float(silver["amount"].sum()), delta=1.0)
            self.assertEqual(set(kpi.columns), {"date", "orders", "revenue", "avg_order_value", "unique_customers"})
        finally:
            env.close()

    def test_idempotent_replay(self):
        env = Env(warmup=6)
        try:
            df = env.batch(seed=5)
            env.pipe.run_batch(df, env.t, batch_id="replay")
            first = env.pipe.lake.read_table("gold", "kpi_daily").to_dict("records")
            n_silver = len(env.pipe.lake.read_table("silver", "orders"))
            env.pipe.run_batch(df, env.t, batch_id="replay")      # same batch id again
            self.assertEqual(env.pipe.lake.read_table("gold", "kpi_daily").to_dict("records"), first)
            self.assertEqual(len(env.pipe.lake.read_table("silver", "orders")), n_silver)
        finally:
            env.close()


class FailureModes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(warmup=9, seed=3)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def run_mode(self, mode):
        df = self.env.batch(seed=21)
        r = self.env.pipe.run_batch(inject_chaos(df, mode, seed=21), self.env.t, batch_id=f"x-{mode}")
        self.env.healthy()   # the platform keeps working afterwards
        return r

    def test_every_failure_mode_is_detected_with_the_right_incident(self):
        for mode in CHAOS_MODES:
            with self.subTest(mode=mode):
                r = self.run_mode(mode)
                self.assertIn(EXPECTED_INCIDENT[mode], r.incident_types)
                self.assertNotEqual(r.status, "success")

    def test_schema_break_blocks_and_quarantines_everything(self):
        r = self.run_mode("schema_drift")
        self.assertEqual(r.status, "blocked")
        self.assertEqual(r.rows_clean, 0)
        self.assertEqual(r.rows_quarantined, r.rows_in)
        self.assertNotIn("x-schema_drift", self.env.pipe.lake.partitions("silver", "orders"))
        self.assertEqual(r.wasted_inr, r.cost_inr)

    def test_quarantine_keeps_rejected_rows_with_reasons(self):
        r = self.run_mode("null_burst")
        q = pd.read_csv(self.env.pipe.lake.table_dir("quarantine", "orders") / "batch_id=x-null_burst.csv")
        self.assertEqual(len(q), r.rows_quarantined)
        self.assertTrue(q["_violations"].str.contains("not_null:customer_id").all())
        self.assertEqual(r.rows_clean + r.rows_quarantined, r.rows_in)

    def test_self_healing_repairs_without_quarantine_but_raises_incident(self):
        r = self.run_mode("format_drift")
        self.assertGreater(r.rows_healed, 0)
        self.assertEqual(r.rows_quarantined, 0)
        self.assertIn("high_heal_rate", r.incident_types)
        self.assertIn("numeric_parse:amount", r.heal_actions)

    def test_bad_batches_do_not_poison_the_baseline(self):
        n_before = len(self.env.pipe.baseline.profiles)
        self.run_mode("silent_drift")
        n_after = len(self.env.pipe.baseline.profiles)
        self.assertEqual(n_after, n_before + 1)       # only the healthy follow-up batch was learned

    def test_duplicate_flood_symptom_grouping(self):
        r = self.run_mode("duplicate_flood")
        self.assertIn("volume_anomaly", r.incident_types)
        self.assertIn("contract_violation", r.incident_types)

    def test_empty_batch_is_handled(self):
        df = self.env.batch().iloc[0:0]
        r = self.env.pipe.run_batch(df, self.env.t, batch_id="empty")
        self.assertEqual(r.rows_in, 0)
        self.assertEqual(r.quality_score, 0.0)
        self.assertEqual(r.wasted_inr, 0.0)

    def test_run_log_is_written(self):
        runs = self.env.pipe.runs()
        self.assertGreater(len(runs), 10)
        self.assertIn("incident_ids", runs[-1])
        lin = self.env.pipe.lake.read_jsonl("lineage.jsonl")
        self.assertEqual({e["job"]["name"] for e in lin}, {"bronze_ingest", "contract_gate", "gold_build"})


class GoldWithNoData(unittest.TestCase):
    def test_first_batch_blocked_leaves_empty_gold(self):
        env = Env(warmup=0)
        try:
            df = generate_orders(100, env.t, seed=1).rename(columns={"amount": "x"})
            env.run(df)
            self.assertTrue(env.pipe.lake.read_table("gold", "kpi_daily").empty)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
