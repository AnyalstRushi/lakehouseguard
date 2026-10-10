import contextlib
import io
import tempfile
import unittest
from pathlib import Path


from tests.helpers import CONTRACT_PATH, contract
from lakehouseguard.cli import main
from lakehouseguard.demo import run_demo
from lakehouseguard.generator import CHAOS_MODES, generate_orders, utc
from lakehouseguard.report import build_report


def cli(*args):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([*args])
    return code, out.getvalue(), err.getvalue()


class DemoAndReport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.tmp.name) / "data"
        cls.pipe, cls.results = run_demo(cls.root, contract(), seed=42, warmup=8)

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def test_demo_shape(self):
        self.assertEqual(len(self.results), 8 + 2 * len(CHAOS_MODES))
        bad = [r for r in self.results if r.status != "success"]
        self.assertEqual(len(bad), len(CHAOS_MODES))            # exactly the injected batches, no false positives

    def test_demo_reset_flag(self):
        pipe, res = run_demo(self.root, contract(), seed=1, warmup=6, modes=["null_burst"], reset=True)
        self.assertEqual(len(res), 8)
        self.assertEqual(len(pipe.runs()), 8)

    def test_report_is_self_contained_html(self):
        pipe, _ = run_demo(self.root, contract(), seed=42, warmup=8)
        with tempfile.TemporaryDirectory() as out:
            p = build_report(pipe.lake, contract(), out)
            html = p.read_text(encoding="utf-8")
        for needle in ("<svg", "Incidents", "Root-cause analysis", "Ask the agent", "schema_break", "distribution_drift", "LakehouseGuard"):
            self.assertIn(needle, html)
        self.assertNotIn("http://", html.replace("http://www.w3.org", ""))
        self.assertNotIn("<script", html)

    def test_report_for_empty_lakehouse(self):
        with tempfile.TemporaryDirectory() as d, tempfile.TemporaryDirectory() as out:
            from lakehouseguard.storage import Lakehouse
            html = build_report(Lakehouse(d), contract(), out).read_text(encoding="utf-8")
        self.assertIn("No incidents", html)

    def test_cli_commands(self):
        c = str(CONTRACT_PATH)
        code, out, _ = cli("incidents", "--root", str(self.root), "--contract", c)
        self.assertEqual(code, 0)
        self.assertIn("schema_break", out)
        code, out, _ = cli("rca", "--root", str(self.root), "--contract", c)
        self.assertEqual(code, 0)
        self.assertIn("Remediation", out)
        inc_id = out.split()[1] if out.startswith("###") else None
        self.assertIsNotNone(inc_id)
        code, out, _ = cli("rca", inc_id, "--root", str(self.root), "--contract", c)
        self.assertIn(inc_id, out)
        code, out, _ = cli("ask", "what is the status?", "--root", str(self.root), "--contract", c)
        self.assertIn("Latest run", out)
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = cli("report", "--root", str(self.root), "--contract", c, "--out", d)
            self.assertTrue(Path(out.strip()).exists())

    def test_cli_ask_with_unreachable_ollama_falls_back(self):
        code, out, err = cli("ask", "status", "--root", str(self.root), "--contract", str(CONTRACT_PATH), "--ollama", "none")
        self.assertEqual(code, 0)
        self.assertIn("Latest run", out)

    def test_cli_rca_without_incidents(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = cli("rca", "--root", d, "--contract", str(CONTRACT_PATH))
            self.assertIn("No incidents", out)

    def test_cli_demo(self):
        with tempfile.TemporaryDirectory() as d:
            code, out, _ = cli("demo", "--root", f"{d}/data", "--out", f"{d}/site", "--contract", str(CONTRACT_PATH))
            self.assertEqual(code, 0)
            self.assertIn("with incidents", out)
            self.assertTrue((Path(d) / "site" / "index.html").exists())

    def test_cli_validate_exit_codes(self):
        with tempfile.TemporaryDirectory() as d:
            ok = Path(d) / "ok.csv"
            bad = Path(d) / "bad.csv"
            df = generate_orders(40, utc(2026, 10, 10, 5), seed=1)
            df.to_csv(ok, index=False)
            df.loc[0, "customer_id"] = None
            df.to_csv(bad, index=False)
            self.assertEqual(cli("validate", str(ok), "--contract", str(CONTRACT_PATH))[0], 0)
            code, out, _ = cli("validate", str(bad), "--contract", str(CONTRACT_PATH))
            self.assertEqual(code, 2)
            self.assertIn("not_null:customer_id", out)


if __name__ == "__main__":
    unittest.main()
