import unittest
from unittest import mock

from tests.helpers import Env, contract
from lakehouseguard.agent import (IncidentAgent, KnowledgeBase, OllamaClient, hypothesize, suggest_patch, triage, contract_chunk, Chunk)
from lakehouseguard.generator import inject_chaos
from lakehouseguard.incidents import Incident


def inc(t, sev="high", ev=None, col=None, run="run-x"):
    return Incident(run_id=run, ts="2026-10-10T00:00:00+00:00", type=t, severity=sev, dataset="orders", title=f"{t} title", evidence=ev or {}, column=col)


class Knowledge(unittest.TestCase):
    def test_retrieval_finds_right_runbook(self):
        kb = KnowledgeBase.from_dir(contract=contract())
        top = kb.search("column renamed upstream breaking change schema")[0][1]
        self.assertEqual(top.source, "schema_break")
        self.assertEqual(kb.search("zzzz qqqq xxxx"), [])
        self.assertIsNone(kb.get("schema_break", "nope"))
        self.assertEqual(kb.search("allowed values city Pune")[0][1].source, "contract")

    def test_empty_kb_rejected(self):
        with self.assertRaises(ValueError):
            KnowledgeBase([])

    def test_contract_chunk_mentions_rules(self):
        text = contract_chunk(contract()).text
        self.assertIn("not null", text)
        self.assertIn("allowed values", text)
        self.assertIn("range", text)
        self.assertIn("pattern", text)


class Triage(unittest.TestCase):
    def test_schema_break_explains_everything_else(self):
        sb = inc("schema_break", "critical", {"missing": ["amount"], "unexpected": ["order_amount"]})
        others = [inc("schema_drift", "medium"), inc("volume_anomaly"), inc("null_rate_drift", col="x")]
        primary, rest = triage(others + [sb])
        self.assertEqual(primary.type, "schema_break")
        self.assertEqual(len(rest), 3)

    def test_duplicates_explain_volume_spike_and_nulls_explain_null_drift(self):
        uniq = inc("contract_violation", ev={"rule": "unique"}, col="order_id")
        spike = inc("volume_anomaly", "medium", {"direction": "spike"})
        p, _ = triage([spike, uniq])
        self.assertEqual(p.id, uniq.id)
        nn = inc("contract_violation", ev={"rule": "not_null"}, col="customer_id")
        nd = inc("null_rate_drift", "medium", col="customer_id")
        self.assertEqual(triage([nd, nn])[0].id, nn.id)

    def test_retype_is_a_symptom_of_type_violation(self):
        tv = inc("contract_violation", ev={"rule": "type"}, col="amount")
        sd = inc("schema_drift", "medium", {"retyped": ["amount"]})
        self.assertEqual(triage([sd, tv])[0].id, tv.id)

    def test_empty_and_single(self):
        self.assertEqual(triage([]), (None, []))
        one = inc("freshness_breach")
        self.assertEqual(triage([one]), (one, []))


class Hypotheses(unittest.TestCase):
    def test_all_types_return_normalised_confidences(self):
        samples = [
            inc("schema_break", ev={"missing": ["a"], "unexpected": ["b"]}), inc("schema_break", ev={"missing": ["a"], "unexpected": []}),
            *[inc("contract_violation", ev={"rule": r}, col="c") for r in ("not_null", "type", "range", "unique", "pattern", "allowed_values", "circuit_breaker", "weird")],
            inc("freshness_breach", ev={"age_minutes": 360}), inc("freshness_breach", ev={"age_minutes": 200}),
            inc("volume_anomaly", ev={"direction": "drop"}), inc("volume_anomaly", ev={"direction": "spike"}),
            inc("null_rate_drift", col="x"), inc("distribution_drift", ev={"baseline_mean": 100, "current_mean": 180}), inc("distribution_drift", ev={"kind": "categorical"}),
            inc("high_heal_rate"), inc("schema_drift"),
        ]
        for i in samples:
            h = hypothesize(i)
            self.assertAlmostEqual(sum(x["confidence"] for x in h), 1.0, delta=0.03, msg=i.type)
            self.assertEqual(h, sorted(h, key=lambda x: -x["confidence"]))

    def test_clock_skew_is_recognised(self):
        self.assertIn("Timezone", hypothesize(inc("freshness_breach", ev={"age_minutes": 361}))[0]["cause"])

    def test_multiplier_is_quantified(self):
        self.assertIn("1.80x", hypothesize(inc("distribution_drift", ev={"baseline_mean": 100.0, "current_mean": 180.0}))[0]["cause"])

    def test_unknown_type_falls_back(self):
        i = inc("schema_drift")
        i.type = "mystery"
        self.assertEqual(hypothesize(i)[0]["cause"], "Unclassified incident")

    def test_patches(self):
        self.assertIn("rename", suggest_patch(inc("schema_break", ev={"missing": ["amount"], "unexpected": ["order_amount"]})))
        self.assertIn("SELECT _violations", suggest_patch(inc("contract_violation")))
        self.assertIn("AVG(amount)", suggest_patch(inc("distribution_drift", ev={"kind": "numeric"})))
        self.assertIn("aliases", suggest_patch(inc("high_heal_rate")))
        self.assertIn("MAX(event_ts)", suggest_patch(inc("freshness_breach")))
        self.assertIsNone(suggest_patch(inc("volume_anomaly")))


class AgentEndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.env = Env(warmup=9, seed=5)
        cls.agent = IncidentAgent(cls.env.pipe.lake, contract())

    @classmethod
    def tearDownClass(cls):
        cls.env.close()

    def test_no_incidents_yet(self):
        self.assertIn("healthy", self.agent.ask("what is the latest incident?").text)
        self.assertIsNone(self.agent.analyze_run(self.env.results[0].run_id))
        self.assertIn("within", self.agent.ask("why did revenue drop?").text)

    def test_zz_incident_flow(self):
        env, agent = self.env, self.agent
        df = env.batch(seed=8)
        env.pipe.run_batch(inject_chaos(df, "schema_drift", seed=8), env.t, batch_id="sb")
        rca = agent.analyze_run("run-sb")
        self.assertEqual(rca.incident.type, "schema_break")
        self.assertGreater(rca.confidence, 0.7)
        self.assertEqual(rca.blast_radius["pct_of_batch"], 1.0)
        self.assertGreater(rca.blast_radius["revenue_at_risk"], 0)
        md = rca.to_markdown()
        for needle in ("schema_break", "Blast radius", "Remediation", "rename", "Symptoms explained"):
            self.assertIn(needle, md)
        self.assertEqual(agent.latest_incident_run(), "run-sb")
        self.assertIn("schema_break", agent.ask("what broke in the latest incident?").text)
        self.assertIn("data-quality effect", agent.ask("why did revenue drop?").text)
        self.assertIn("Runbook for **duplicate".replace("duplicate", "contract_violation"), agent.ask("how do I fix duplicates").text)
        self.assertIn("schema_break", agent.ask("how do I fix a schema break").text)
        self.assertIn("blocked", agent.ask("what's the pipeline status").text)
        self.assertIn("per million rows", agent.ask("what did this cost").text)
        self.assertEqual(agent.ask("tell me about pii").intent, "search")
        # unknown keyword -> falls back on latest incident type
        self.assertIn("Runbook", agent.ask("what should I do").text)
        # repeated failure is flagged as recurring
        df2 = env.batch(seed=9)
        env.pipe.run_batch(inject_chaos(df2, "schema_drift", seed=9), env.t, batch_id="sb2")
        self.assertIn("recurring", agent.analyze_run("run-sb2").to_markdown())
        # analyze by id and unknown id
        self.assertEqual(agent.analyze(rca.incident.id).incident.id, rca.incident.id)
        with self.assertRaises(KeyError):
            agent.analyze("INC-NOPE")

    def test_zzz_llm_hook(self):
        class Fake:
            def complete(self, prompt):
                assert "FACTS" in prompt
                return "polished answer"

        class Broken:
            def complete(self, prompt):
                raise ConnectionError("no server")

        ok = self.agent.ask("what is the status?", llm=Fake())
        self.assertTrue(ok.llm_used)
        self.assertEqual(ok.text, "polished answer")
        bad = self.agent.ask("what is the status?", llm=Broken())
        self.assertFalse(bad.llm_used)
        self.assertIn("ConnectionError", bad.llm_error)
        self.assertIn("Latest run", bad.text)

    def test_ollama_client_request_shape(self):
        class Resp:
            def __enter__(self): return self
            def __exit__(self, *a): return False
            def read(self): return b'{"response": "hello"}'

        with mock.patch("urllib.request.urlopen", return_value=Resp()) as m:
            self.assertEqual(OllamaClient("m", "http://h:1/").complete("p"), "hello")
            req = m.call_args[0][0]
            self.assertEqual(req.full_url, "http://h:1/api/generate")


class NoHistory(unittest.TestCase):
    def test_fresh_lakehouse_answers_gracefully(self):
        env = Env(warmup=0)
        try:
            a = IncidentAgent(env.pipe.lake, contract())
            self.assertIn("No runs", a.ask("status?").text)
            self.assertIn("No runs", a.ask("what did it cost").text)
            self.assertIn("Not enough history", a.ask("why did sales drop").text)
            self.assertIn("Runbook", a.ask("how to fix freshness").text)
            generic = a.ask("what should I do about idempotency keys")          # no type keyword, no incident -> retrieval
            self.assertEqual(generic.intent, "fix")
            self.assertTrue(generic.sources)
            self.assertEqual(a.ask("explain the identifier pattern rule").intent, "search")
            self.assertIn("could not find", IncidentAgent(env.pipe.lake, kb=KnowledgeBase([Chunk("x", "y", "alpha")])).ask("zzzz").text)
        finally:
            env.close()


if __name__ == "__main__":
    unittest.main()
