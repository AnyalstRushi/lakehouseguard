import tempfile
import unittest

from lakehouseguard import lineage
from lakehouseguard.cost import CostModel
from lakehouseguard.incidents import Incident, IncidentStore
from lakehouseguard.storage import Lakehouse


def make(run="r1", t="schema_break", col=None):
    return Incident(run_id=run, ts="2026-10-10T00:00:00+00:00", type=t, severity="high", dataset="orders", title="t", evidence={"rule": "x"}, column=col)


class Incidents(unittest.TestCase):
    def test_store_dedupes_and_tracks_recurrence_and_resolution(self):
        with tempfile.TemporaryDirectory() as d:
            store = IncidentStore(Lakehouse(d))
            a = make("r1")
            self.assertTrue(store.add(a))
            self.assertFalse(store.add(make("r1")))             # same fingerprint + run -> same id
            b = make("r2")
            self.assertTrue(store.add(b))
            self.assertEqual(store.recurrence(b), 2)
            self.assertEqual(len(store.for_run("r1")), 1)
            self.assertEqual(store.get(a.id).id, a.id)
            self.assertIsNone(store.get("INC-NOPE"))
            self.assertTrue(store.resolve(a.id))
            self.assertFalse(store.resolve(a.id))
            self.assertEqual(store.get(a.id).status, "resolved")

    def test_ids_are_stable_and_severity_validated(self):
        self.assertEqual(make().id, make().id)
        self.assertNotEqual(make("r1").id, make("r2").id)
        self.assertTrue(make().id.startswith("INC-"))
        with self.assertRaises(ValueError):
            Incident(run_id="r", ts="t", type="x", severity="catastrophic", dataset="d", title="t")


class Cost(unittest.TestCase):
    def test_cost_and_waste(self):
        m = CostModel()
        c = m.run_cost(10.0, 1_000_000)
        self.assertAlmostEqual(c["total_inr"], c["compute_inr"] + c["storage_inr"], places=3)
        self.assertGreater(m.run_cost(100.0, 1000)["compute_inr"], m.run_cost(1.0, 1000)["compute_inr"])
        self.assertEqual(m.wasted(1.0, 100, 25, False), 0.25)
        self.assertEqual(m.wasted(1.0, 100, 25, True), 1.0)
        self.assertEqual(m.wasted(1.0, 0, 0, False), 0.0)


class Lineage(unittest.TestCase):
    def test_graph_walks(self):
        self.assertEqual(lineage.upstream("gold.kpi_daily")[-1], "source.orders_api")
        self.assertIn("gold.kpi_daily", lineage.downstream("bronze.orders"))
        self.assertEqual(lineage.upstream("source.orders_api"), [])
        self.assertEqual(lineage.downstream("gold.kpi_daily"), [])

    def test_event_shape(self):
        e = lineage.event("job", "run-1", "ts", ["a"], ["b"], {"rows": 3})
        self.assertEqual(e["job"]["name"], "job")
        self.assertEqual(e["inputs"][0]["name"], "a")
        self.assertEqual(e["run"]["facets"]["rows"], 3)


if __name__ == "__main__":
    unittest.main()
