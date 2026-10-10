import runpy
import sys
import unittest
from unittest import mock


from tests.helpers import Env, contract
from lakehouseguard.agent import IncidentAgent
from lakehouseguard.generator import generate_orders, utc
from lakehouseguard.healing import heal
from lakehouseguard.observability import profile
from lakehouseguard.report import _line_chart, build_report
from lakehouseguard.storage import Lakehouse
import tempfile


class EdgeCases(unittest.TestCase):
    def test_circuit_breaker_blocks_a_mostly_bad_batch(self):
        env = Env(warmup=8)
        try:
            df = env.batch(seed=3)
            df["city"] = df["city"].astype(object)
            df.loc[df.index[: int(len(df) * 0.7)], "city"] = "Atlantis"
            r = env.run(df)
            self.assertEqual(r.status, "blocked")
            self.assertEqual(r.rows_clean, 0)
            crit = [i for i in r.incidents if i.evidence.get("rule") == "circuit_breaker"]
            self.assertEqual(crit[0].severity, "critical")
        finally:
            env.close()

    def test_heal_skips_numeric_object_column_without_text(self):
        df = generate_orders(20, utc(2026, 10, 10, 5), seed=1)
        df["amount"] = df["amount"].astype(object)
        out, actions, rows = heal(df, contract())
        self.assertEqual((actions, rows), ({}, 0))

    def test_profile_bool_kind(self):
        df = generate_orders(10, utc(2026, 10, 10, 5), seed=1)
        df["flag"] = True
        self.assertEqual(profile(df, contract())["dtypes"]["flag"], "bool")

    def test_revenue_question_without_incident_but_big_move(self):
        with tempfile.TemporaryDirectory() as d:
            lake = Lakehouse(d)
            for i, rev in enumerate([100, 100, 100, 100, 40]):
                lake.append_jsonl("runs.jsonl", {"run_id": f"r{i}", "status": "success", "revenue_clean": rev, "rows_in": 10, "rows_clean": 10,
                                                "rows_quarantined": 0, "incident_ids": [], "quality_score": 100})
            ans = IncidentAgent(lake, contract()).ask("why did revenue drop?")
            self.assertIn("genuine business movement", ans.text)

    def test_report_skips_runs_whose_incidents_are_missing(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            lake = Lakehouse(d)
            lake.append_jsonl("runs.jsonl", {"run_id": "r1", "status": "degraded", "revenue_clean": 1, "rows_in": 10, "rows_clean": 10,
                                            "rows_quarantined": 0, "rows_healed": 0, "incident_ids": ["INC-GONE"], "quality_score": 100,
                                            "cost_inr": 0.1, "wasted_inr": 0.0})
            html = build_report(lake, contract(), out).read_text(encoding="utf-8")
            self.assertIn("No incidents to analyse", html)

    def test_line_chart_empty(self):
        self.assertEqual(_line_chart([], []), "")

    def test_python_dash_m_entrypoint(self):
        with mock.patch.object(sys, "argv", ["lakehouseguard", "incidents", "--root", tempfile.mkdtemp(), "--contract", "contracts/orders.yaml"]):
            with self.assertRaises(SystemExit) as cm:
                runpy.run_module("lakehouseguard", run_name="__main__")
        self.assertEqual(cm.exception.code, 0)


if __name__ == "__main__":
    unittest.main()
