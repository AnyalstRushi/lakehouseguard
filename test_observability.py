import unittest

import numpy as np
import pandas as pd

from tests.helpers import Env, contract
from lakehouseguard.generator import generate_orders, inject_chaos, utc
from lakehouseguard.observability import (Baseline, Detector, MIN_HISTORY, Thresholds, profile, psi_categorical, psi_numeric)
from lakehouseguard.storage import Lakehouse
import tempfile


class PSI(unittest.TestCase):
    def test_numeric_identical_is_near_zero_and_shift_is_large(self):
        rng = np.random.default_rng(0)
        a = pd.Series(rng.normal(100, 10, 3000))
        b = pd.Series(rng.normal(100, 10, 3000))
        self.assertLess(psi_numeric(a, b), 0.05)
        self.assertGreater(psi_numeric(a, b * 1.8), 1.0)

    def test_numeric_guards(self):
        self.assertEqual(psi_numeric(pd.Series([1.0] * 5), pd.Series([1.0] * 5)), 0.0)          # too few rows
        self.assertEqual(psi_numeric(pd.Series([1.0] * 100), pd.Series([1.0] * 100)), 0.0)      # constant column

    def test_categorical(self):
        a = pd.Series(["x"] * 70 + ["y"] * 30)
        self.assertLess(psi_categorical(a, a), 1e-9)
        self.assertGreater(psi_categorical(a, pd.Series(["x"] * 10 + ["y"] * 10 + ["z"] * 80)), 0.5)
        self.assertEqual(psi_categorical(pd.Series([], dtype=object), a), 0.0)


class Profiling(unittest.TestCase):
    def test_profile_fields(self):
        df = generate_orders(100, utc(2026, 10, 10, 4), seed=1)
        df.loc[:9, "customer_id"] = None
        p = profile(df, contract())
        self.assertEqual(p["rows"], 100)
        self.assertAlmostEqual(p["null_rates"]["customer_id"], 0.1)
        self.assertEqual(p["dtypes"]["amount"], "number")
        self.assertEqual(p["dtypes"]["event_ts"], "timestamp")
        self.assertEqual(p["dtypes"]["city"], "text")
        self.assertIsNotNone(p["max_event_ts"])

    def test_profile_empty(self):
        p = profile(generate_orders(5, utc(2026, 10, 10, 4)).iloc[0:0], contract())
        self.assertEqual(p["rows"], 0)
        self.assertIsNone(p["max_event_ts"])


class BaselineBehaviour(unittest.TestCase):
    def test_not_ready_until_history(self):
        with tempfile.TemporaryDirectory() as d:
            b = Baseline(Lakehouse(d))
            self.assertFalse(b.ready)
            self.assertIsNone(b.reference())
            df = generate_orders(50, utc(2026, 10, 10, 4))
            for _ in range(MIN_HISTORY):
                b.update(profile(df, contract()), df)
            self.assertTrue(b.ready)
            self.assertEqual(len(b.reference()), 50 * MIN_HISTORY)

    def test_reference_is_capped(self):
        with tempfile.TemporaryDirectory() as d:
            b = Baseline(Lakehouse(d))
            df = generate_orders(4000, utc(2026, 10, 10, 4))
            b.update(profile(df, contract()), df)
            b.update(profile(df, contract()), df)
            self.assertLessEqual(len(b.reference()), 6000)

    def test_detector_is_quiet_without_baseline_except_freshness(self):
        with tempfile.TemporaryDirectory() as d:
            det = Detector(contract(), Baseline(Lakehouse(d)))
            now = utc(2026, 10, 10, 5)
            df = inject_chaos(generate_orders(300, now), "stale_data")
            found = det.detect(df, profile(df, contract()), now)
            self.assertEqual([f.type for f in found], ["freshness_breach"])


class Detectors(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(warmup=8)

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def detect(self, mode, seed=11):
        base = generate_orders(1200, self.env.t, seed=seed, id_offset=9_000_000)
        df = inject_chaos(base, mode, seed=seed) if mode else base
        det = self.env.pipe.detector
        return {f.type: f for f in det.detect(df, profile(df, contract()), self.env.t)}

    def test_healthy_batch_is_clean(self):
        self.assertEqual(self.detect(None), {})

    def test_volume_drop_and_spike(self):
        f = self.detect("volume_drop")["volume_anomaly"]
        self.assertEqual(f.evidence["direction"], "drop")
        base = generate_orders(4500, self.env.t, seed=2, id_offset=9_000_000)
        spike = self.env.pipe.detector.detect(base, profile(base, contract()), self.env.t)
        self.assertEqual([x.evidence["direction"] for x in spike if x.type == "volume_anomaly"], ["spike"])

    def test_freshness(self):
        f = self.detect("stale_data")["freshness_breach"]
        self.assertGreater(f.evidence["age_minutes"], 300)

    def test_null_drift_and_schema_drift(self):
        self.assertEqual(self.detect("null_burst")["null_rate_drift"].column, "customer_id")
        s = self.detect("schema_drift")["schema_drift"]
        self.assertIn("order_amount", s.evidence["added"])
        self.assertIn("amount", s.evidence["removed"])

    def test_silent_drift_is_only_visible_to_observability(self):
        f = self.detect("silent_drift")["distribution_drift"]
        self.assertEqual(f.column, "amount")
        self.assertGreater(f.evidence["current_mean"], f.evidence["baseline_mean"] * 1.5)

    def test_categorical_drift(self):
        base = generate_orders(1200, self.env.t, seed=4, id_offset=9_000_000)
        base["city"] = "Pune"
        found = self.env.pipe.detector.detect(base, profile(base, contract()), self.env.t)
        self.assertTrue(any(f.type == "distribution_drift" and f.column == "city" for f in found))

    def test_heal_rate_threshold(self):
        det = self.env.pipe.detector
        df = generate_orders(100, self.env.t, seed=3, id_offset=9_000_000)
        self.assertFalse([f for f in det.detect(df, profile(df, contract()), self.env.t, heal_rate=0.05) if f.type == "high_heal_rate"])
        self.assertTrue([f for f in det.detect(df, profile(df, contract()), self.env.t, heal_rate=0.4) if f.type == "high_heal_rate"])

    def test_missing_sla_disables_freshness(self):
        c = contract()
        c2 = type(c)(c.name, c.version, c.owner, c.description, {}, c.primary_key, c.columns)
        det = Detector(c2, self.env.pipe.baseline, Thresholds())
        df = inject_chaos(generate_orders(200, self.env.t, seed=1, id_offset=9_000_000), "stale_data")
        self.assertFalse([f for f in det.detect(df, profile(df, c2), self.env.t) if f.type == "freshness_breach"])


if __name__ == "__main__":
    unittest.main()
