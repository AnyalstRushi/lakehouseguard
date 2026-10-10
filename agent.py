"""Offline-first AI incident agent.

Pipeline of reasoning (no API key, no network needed):

1. **Triage**: collapse a run's incidents into one *primary* incident and its *symptoms*
   using a causal rule table (a schema break explains the nulls and the volume drop it causes).
2. **Hypotheses**: evidence-driven root-cause candidates with normalised confidence.
3. **Retrieval (RAG)**: TF-IDF search over runbooks and the data contract finds the matching
   remediation steps and related guidance.
4. **Blast radius**: lineage gives the downstream assets affected; run metrics give rows and
   estimated revenue at risk.

An optional ``LLMClient`` (for example a local Ollama model) can rewrite the answer in natural
language. It only ever receives the retrieved facts, and the deterministic answer is always
kept alongside as evidence.
"""
from __future__ import annotations

import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from sklearn.feature_extraction.text import TfidfVectorizer

from . import lineage
from .contracts import Contract
from .incidents import SEVERITY_ORDER, Incident, IncidentStore
from .storage import Lakehouse

KNOWLEDGE_DIR = Path(__file__).with_name("knowledge")


# --------------------------------------------------------------------- knowledge base
@dataclass
class Chunk:
    source: str    # runbook name (incident type) or "contract"
    title: str     # section heading
    text: str


class KnowledgeBase:
    def __init__(self, chunks: list[Chunk]):
        if not chunks:
            raise ValueError("knowledge base needs at least one chunk")
        self.chunks = chunks
        self._vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), sublinear_tf=True)
        self._matrix = self._vec.fit_transform([f"{c.source} {c.title} {c.text}" for c in chunks])

    @classmethod
    def from_dir(cls, directory: Path | str = KNOWLEDGE_DIR, contract: Contract | None = None) -> "KnowledgeBase":
        chunks: list[Chunk] = []
        for md in sorted(Path(directory).glob("*.md")):
            chunks += cls._parse(md.stem, md.read_text(encoding="utf-8"))
        if contract is not None:
            chunks.append(contract_chunk(contract))
        return cls(chunks)

    @staticmethod
    def _parse(source: str, text: str) -> list[Chunk]:
        out, title, buf = [], "Overview", []
        for line in text.splitlines():
            if line.startswith("## "):
                if buf:
                    out.append(Chunk(source, title, "\n".join(buf).strip()))
                title, buf = line[3:].strip(), []
            elif not line.startswith("# "):
                buf.append(line)
        if buf:
            out.append(Chunk(source, title, "\n".join(buf).strip()))
        return [c for c in out if c.text]

    def get(self, source: str, title: str) -> Chunk | None:
        for c in self.chunks:
            if c.source == source and c.title.lower() == title.lower():
                return c
        return None

    def search(self, query: str, k: int = 3) -> list[tuple[float, Chunk]]:
        q = self._vec.transform([query])
        scores = (self._matrix @ q.T).toarray().ravel()
        order = np.argsort(-scores)[:k]
        return [(float(scores[i]), self.chunks[i]) for i in order if scores[i] > 0]


def contract_chunk(contract: Contract) -> Chunk:
    lines = [f"Data contract {contract.name} v{contract.version}, owner {contract.owner}. SLA: {contract.sla}."]
    for c in contract.columns:
        rules = []
        if not c.nullable:
            rules.append("not null")
        if c.unique:
            rules.append("unique")
        if c.allowed_values:
            rules.append("allowed values " + ", ".join(c.allowed_values))
        if c.min is not None or c.max is not None:
            rules.append(f"range {c.min}..{c.max}")
        if c.pattern:
            rules.append(f"pattern {c.pattern}")
        lines.append(f"Column {c.name} ({c.type}): " + ("; ".join(rules) or "no extra rules"))
    return Chunk("contract", f"{contract.name} contract", "\n".join(lines))


# --------------------------------------------------------------------- LLM hook
class LLMClient(Protocol):
    def complete(self, prompt: str) -> str: ...


class OllamaClient:
    """Talks to a local Ollama server (free, private). Not needed for the agent to work."""

    def __init__(self, model: str = "llama3.1", host: str = "http://localhost:11434", timeout: float = 60.0):
        self.model, self.host, self.timeout = model, host.rstrip("/"), timeout

    def complete(self, prompt: str) -> str:
        body = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode()
        req = urllib.request.Request(f"{self.host}/api/generate", data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - local host by design
            return json.loads(resp.read().decode())["response"]


# --------------------------------------------------------------------- reasoning rules
SYMPTOMS_OF: dict[str, set[str]] = {
    "schema_break": {"contract_violation", "null_rate_drift", "schema_drift", "distribution_drift", "volume_anomaly", "high_heal_rate"},
}


def triage(incidents: list[Incident]) -> tuple[Incident | None, list[Incident]]:
    """Return (primary, symptoms). Symptoms are incidents explained by another incident in the same run."""
    if not incidents:
        return None, []

    def is_symptom(i: Incident) -> bool:
        for j in incidents:
            if j is i:
                continue
            if i.type in SYMPTOMS_OF.get(j.type, set()):
                return True
            if i.type == "volume_anomaly" and i.evidence.get("direction") == "spike" and j.type == "contract_violation" and j.evidence.get("rule") == "unique":
                return True
            if i.type == "schema_drift" and i.evidence.get("retyped") and j.type == "contract_violation" and j.evidence.get("rule") == "type" and j.column in i.evidence["retyped"]:
                return True
            if i.type == "null_rate_drift" and j.type == "contract_violation" and j.evidence.get("rule") == "not_null" and j.column == i.column:
                return True
        return False

    symptoms = [i for i in incidents if is_symptom(i)]
    primaries = [i for i in incidents if i not in symptoms] or incidents
    primaries.sort(key=lambda i: (SEVERITY_ORDER[i.severity], i.type))
    primary = primaries[0]
    rest = [i for i in incidents if i is not primary]
    return primary, rest


def hypothesize(inc: Incident) -> list[dict[str, Any]]:
    t, ev = inc.type, inc.evidence
    cands: list[tuple[str, float, str]] = []
    if t == "schema_break":
        missing, extra = ev.get("missing", []), ev.get("unexpected", [])
        if missing and extra:
            cands.append((f"Upstream renamed {', '.join(missing)} to {', '.join(extra)} without a contract version bump", 0.75, "a column vanished while a new one appeared in the same batch"))
            cands.append(("Source API or extraction query version changed", 0.25, "breaking schema change without a new column"))
        else:
            cands.append((f"Upstream dropped {', '.join(missing)}", 0.7, "required column absent and nothing new appeared"))
            cands.append(("Ingestion column mapping was reverted or misconfigured", 0.3, "mapping errors drop columns silently"))
    elif t == "contract_violation":
        rule = ev.get("rule")
        table = {
            "not_null": [("An upstream join or lookup stopped populating the field", 0.5), ("A producer app version no longer sends the field", 0.3), ("Partial outage of an enrichment service", 0.2)],
            "type": [("Upstream emits placeholders or free text in a numeric field", 0.6), ("Locale or currency formatting changed upstream", 0.4)],
            "range": [("Unit or currency scaling error (a misplaced decimal)", 0.5), ("Test or synthetic data leaking into production", 0.3), ("Genuine outliers: confirm with the business", 0.2)],
            "unique": [("At-least-once delivery or retry storm without an idempotency key", 0.6), ("Overlapping replay or backfill of already-loaded data", 0.4)],
            "pattern": [("New identifier format introduced upstream", 0.6), ("Corrupted or truncated values", 0.4)],
            "allowed_values": [("New code value launched upstream without a contract update", 0.5), ("Typos or casing variants not covered by aliases", 0.5)],
            "circuit_breaker": [("Systemic upstream failure: most rows are invalid", 0.7), ("Wrong contract version applied to this source", 0.3)],
        }
        for cause, w in table.get(rule, [("Unclassified contract failure", 1.0)]):
            cands.append((cause, w, f"rule '{rule}' failed" + (f" on {inc.column}" if inc.column else "")))
    elif t == "freshness_breach":
        hours = ev.get("age_minutes", 0) / 60
        if hours >= 1 and abs(hours - round(hours)) < 0.1:
            cands.append((f"Timezone or clock-skew bug: events are shifted by about {round(hours)} h", 0.55, "age is a whole number of hours"))
            cands.append(("Upstream extract delayed or stuck", 0.45, "stale data without partial arrival"))
        else:
            cands.append(("Upstream extract delayed, paused or stuck", 0.6, "newest event is older than the SLA"))
            cands.append(("Stale watermark: job read an old window", 0.4, "orchestrator may be replaying an old range"))
    elif t == "volume_anomaly":
        if ev.get("direction") == "drop":
            cands += [("Partial extract or pagination bug", 0.45, "volume is a fraction of baseline"), ("Filter added upstream by mistake", 0.3, "uniform shrink"), ("Source outage in one region or service", 0.25, "check Gold by city")]
        else:
            cands += [("Duplicate delivery or retry storm", 0.5, "volume above baseline"), ("Backfill overlapping live data", 0.3, "check batch ids"), ("Genuine surge such as a promotion", 0.2, "confirm with the business calendar")]
    elif t == "null_rate_drift":
        cands += [("An upstream join or lookup stopped matching", 0.5, f"null rate on {inc.column} rose"), ("A producer release stopped sending the field", 0.3, "sudden step change"), ("Enrichment service degraded", 0.2, "partial population")]
    elif t == "distribution_drift":
        cur, base = ev.get("current_mean"), ev.get("baseline_mean")
        if cur and base:
            ratio = cur / base
            cands.append((f"Unit, currency or conversion multiplier of about {ratio:.2f}x applied upstream", 0.6, f"mean moved {base:,.0f} to {cur:,.0f} while every value stays in range"))
            cands.append(("Price change or business event (sale, product mix)", 0.3, "contracts pass, so values are plausible"))
            cands.append(("Mix shift toward higher-value segments", 0.1, "distribution moved without outliers"))
        else:
            cands += [("New regional or product mix (launch)", 0.5, "category shares shifted"), ("Code mapping change upstream", 0.5, "values re-labelled")]
    elif t == "high_heal_rate":
        cands += [("Producer changed value formatting (currency symbols, casing, whitespace)", 0.7, "auto-repair rate is above limit"), ("A new client or app version sends free-text values", 0.3, "fresh variety in formats")]
    elif t == "schema_drift":
        cands += [("Producer added or retyped a field", 0.7, "columns or dtypes differ from the healthy baseline"), ("Accidental type change upstream", 0.3, "type moved from number to text")]
    else:
        cands.append(("Unclassified incident", 1.0, "no playbook for this type"))
    total = sum(w for _, w, _ in cands)
    return [{"cause": c, "confidence": round(w / total, 2), "why": why} for c, w, why in sorted(cands, key=lambda x: -x[1])]


def suggest_patch(inc: Incident) -> str | None:
    ev = inc.evidence
    if inc.type == "schema_break" and ev.get("missing") and ev.get("unexpected") and len(ev["missing"]) == len(ev["unexpected"]):
        mapping = dict(zip(ev["unexpected"], ev["missing"]))
        return "```python\n# TEMPORARY shim in the ingestion step until the producer rolls back or the contract is bumped\n" \
               f"df = df.rename(columns={mapping!r})\n```"
    if inc.type == "contract_violation":
        return ("```sql\n-- inspect exactly which rows failed and why\nSELECT _violations, COUNT(*) AS rows\n"
                f"FROM quarantine.orders WHERE _batch_id = '{inc.run_id.removeprefix('run-')}'\nGROUP BY _violations ORDER BY rows DESC;\n```")
    if inc.type == "distribution_drift" and ev.get("kind") == "numeric":
        return ("```sql\n-- find when the shift started\nSELECT date, AVG(amount) AS avg_amount, COUNT(*) AS orders\n"
                "FROM silver.orders GROUP BY date ORDER BY date;\n```")
    if inc.type == "high_heal_rate":
        return "```yaml\n# contracts/orders.yaml: accept a legitimate new spelling explicitly\n- name: city\n  aliases: {Bangalore: Bengaluru, Bombay: Mumbai}\n```"
    if inc.type == "freshness_breach":
        return "```sql\n-- how stale is each source watermark?\nSELECT MAX(event_ts) AS newest FROM silver.orders;\n```"
    return None


# --------------------------------------------------------------------- RCA
@dataclass
class RCA:
    incident: Incident
    hypotheses: list[dict[str, Any]]
    symptoms: list[Incident]
    blast_radius: dict[str, Any]
    steps: list[str]
    retrieved: list[tuple[float, Chunk]]
    patch: str | None
    recurrence: int

    @property
    def confidence(self) -> float:
        return self.hypotheses[0]["confidence"] if self.hypotheses else 0.0

    def to_markdown(self) -> str:
        i = self.incident
        lines = [f"### {i.id} · {i.type} · {i.severity.upper()}", f"**{i.title}**", ""]
        if self.recurrence > 1:
            lines.append(f"_Seen {self.recurrence} times: recurring problem, prioritise a permanent fix._\n")
        lines.append("**Most likely causes**")
        for h in self.hypotheses[:3]:
            lines.append(f"- {h['confidence']:.0%}: {h['cause']} _(evidence: {h['why']})_")
        br = self.blast_radius
        lines += ["", "**Blast radius**",
                  f"- Rows affected: {br['rows_affected']} of {br['rows_in']} ({br['pct_of_batch']:.0%}); batch status: {br['status']}",
                  f"- Downstream assets: {', '.join(br['downstream'])}"]
        if br.get("revenue_at_risk"):
            lines.append(f"- Estimated revenue at risk: INR {br['revenue_at_risk']:,.0f}")
        if self.symptoms:
            lines += ["", "**Symptoms explained by this root cause**"]
            lines += [f"- {s.type}: {s.title}" for s in self.symptoms]
        lines += ["", "**Remediation (from runbook)**"] + (self.steps or ["No runbook steps found."])
        if self.patch:
            lines += ["", "**Suggested starting point**", self.patch]
        return "\n".join(lines)


@dataclass
class AgentAnswer:
    question: str
    intent: str
    text: str
    evidence: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)
    llm_used: bool = False
    llm_error: str | None = None


class IncidentAgent:
    def __init__(self, lake: Lakehouse, contract: Contract | None = None, kb: KnowledgeBase | None = None, llm: LLMClient | None = None):
        self.lake = lake
        self.store = IncidentStore(lake)
        self.kb = kb or KnowledgeBase.from_dir(KNOWLEDGE_DIR, contract)
        self.llm = llm

    # ---- core ----------------------------------------------------------------
    def analyze(self, incident: Incident | str, symptoms: list[Incident] | None = None) -> RCA:
        inc = self.store.get(incident) if isinstance(incident, str) else incident
        if inc is None:
            raise KeyError(f"unknown incident {incident}")
        run = next((r for r in self.lake.read_jsonl("runs.jsonl") if r["run_id"] == inc.run_id), {})
        rows_in = run.get("rows_in", 0)
        affected = rows_in if run.get("status") == "blocked" else run.get("rows_quarantined", 0)
        kpi = self.lake.read_table("gold", "kpi_daily")
        aov = float(kpi["avg_order_value"].median()) if len(kpi) else 0.0
        blast = {
            "rows_in": rows_in, "rows_affected": affected, "pct_of_batch": (affected / rows_in) if rows_in else 0.0,
            "status": run.get("status", "unknown"), "downstream": lineage.downstream("bronze.orders"),
            "revenue_at_risk": round(affected * aov, 2) if aov else 0.0,
        }
        query = f"{inc.type} {inc.title} {inc.column or ''} {inc.evidence.get('rule', '')}"
        retrieved = self.kb.search(query, k=3)
        rem = self.kb.get(inc.type, "Remediation")
        steps = [ln.strip() for ln in rem.text.splitlines() if ln.strip()] if rem else []
        return RCA(inc, hypothesize(inc), symptoms or [], blast, steps, retrieved, suggest_patch(inc), self.store.recurrence(inc))

    def analyze_run(self, run_id: str) -> RCA | None:
        primary, symptoms = triage(self.store.for_run(run_id))
        return self.analyze(primary, symptoms) if primary else None

    def latest_incident_run(self) -> str | None:
        for r in reversed(self.lake.read_jsonl("runs.jsonl")):
            if r.get("incident_ids"):
                return r["run_id"]
        return None

    # ---- natural-language Q&A ---------------------------------------------------
    def ask(self, question: str, llm: LLMClient | None = None) -> AgentAnswer:
        q = question.lower()
        client = llm or self.llm
        if re.search(r"(revenue|sales|gmv).*(drop|fall|fell|down|lower|declin|dip)|why.*(revenue|sales)", q):
            ans = self._revenue(question)
        elif re.search(r"\b(cost|spend|inr|rupee|waste|wasted|finops)\b", q):
            ans = self._cost(question)
        elif re.search(r"\b(fix|resolve|remediat|runbook|mitigate|prevent)\b|what should i do", q):
            ans = self._fix(question)
        elif re.search(r"\b(status|health|summary|overview|latest run)\b", q):
            ans = self._status(question)
        elif re.search(r"incident|broke|broken|fail|wrong|issue|alert|happen|latest|why", q):
            ans = self._incident(question)
        else:
            ans = self._fallback(question)
        if client is not None:
            ans = self._with_llm(ans, client)
        return ans

    def _with_llm(self, ans: AgentAnswer, client: LLMClient) -> AgentAnswer:
        prompt = ("You are a data reliability assistant. Using ONLY the facts below, answer the question in "
                  "at most 6 sentences. Do not invent numbers.\n\nFACTS:\n" + "\n".join(ans.evidence) +
                  f"\n\nDRAFT ANSWER:\n{ans.text}\n\nQUESTION: {ans.question}\nANSWER:")
        try:
            ans.text = client.complete(prompt).strip() or ans.text
            ans.llm_used = True
        except Exception as exc:  # network down, model missing... keep the deterministic answer
            ans.llm_error = f"{type(exc).__name__}: {exc}"
        return ans

    def _status(self, question: str) -> AgentAnswer:
        runs = self.lake.read_jsonl("runs.jsonl")
        if not runs:
            return AgentAnswer(question, "status", "No runs recorded yet. Run `lakehouseguard demo` first.")
        last = runs[-1]
        ok = sum(1 for r in runs if r["status"] == "success")
        open_inc = [i for i in self.store.all() if i.status == "open"]
        text = (f"Latest run {last['run_id']} is **{last['status']}**: {last['rows_clean']}/{last['rows_in']} rows clean "
                f"(quality {last['quality_score']}%). {ok}/{len(runs)} runs succeeded; {len(open_inc)} incident(s) open.")
        return AgentAnswer(question, "status", text, [f"runs={len(runs)}", f"open_incidents={len(open_inc)}", f"last_status={last['status']}"])

    def _cost(self, question: str) -> AgentAnswer:
        runs = self.lake.read_jsonl("runs.jsonl")
        if not runs:
            return AgentAnswer(question, "cost", "No runs recorded yet.")
        total = sum(r["cost_inr"] for r in runs)
        waste = sum(r["wasted_inr"] for r in runs)
        rows = sum(r["rows_in"] for r in runs)
        per_m = total / rows * 1e6 if rows else 0
        text = (f"Across {len(runs)} runs the pipeline cost about INR {total:.2f} (INR {per_m:,.0f} per million rows); "
                f"INR {waste:.2f} ({(waste / total if total else 0):.1%}) produced no usable data because rows were quarantined or batches blocked.")
        return AgentAnswer(question, "cost", text, [f"total_inr={total:.4f}", f"wasted_inr={waste:.4f}", f"rows={rows}"])

    def _revenue(self, question: str) -> AgentAnswer:
        runs = self.lake.read_jsonl("runs.jsonl")
        if len(runs) < 3:
            return AgentAnswer(question, "revenue", "Not enough history to explain a revenue change yet.")
        last = runs[-1]
        past = [r["revenue_clean"] for r in runs[:-1] if r["status"] == "success"] or [r["revenue_clean"] for r in runs[:-1]]
        typical = float(np.median(past))
        change = (last["revenue_clean"] - typical) / typical if typical else 0.0
        if abs(change) < 0.15 and not last.get("incident_ids"):
            return AgentAnswer(question, "revenue", f"Latest batch revenue (INR {last['revenue_clean']:,.0f}) is within {abs(change):.0%} of normal (INR {typical:,.0f}); no data-quality incident explains a drop.",
                               [f"revenue={last['revenue_clean']}", f"typical={typical:.0f}"])
        incs = self.store.for_run(last["run_id"])
        primary, symptoms = triage(incs)
        parts = [f"Revenue in the latest batch is INR {last['revenue_clean']:,.0f}, {change:+.0%} versus the typical INR {typical:,.0f}."]
        evidence = [f"revenue={last['revenue_clean']}", f"typical={typical:.0f}", f"change={change:+.1%}"]
        if primary:
            rca = self.analyze(primary, symptoms)
            parts.append(f"This is a data-quality effect, not a business drop: **{primary.type}** ({primary.title}). Most likely cause: {rca.hypotheses[0]['cause']}.")
            if last["rows_quarantined"] or last["status"] == "blocked":
                parts.append(f"{last['rows_quarantined']} of {last['rows_in']} rows were held back in quarantine, so Gold under-reports until the batch is replayed.")
            evidence.append(f"incident={primary.id}")
        else:
            parts.append("No incident was raised, so this looks like a genuine business movement. Check the promotion and pricing calendar.")
        return AgentAnswer(question, "revenue", " ".join(parts), evidence, [primary.type] if primary else [])

    def _incident(self, question: str) -> AgentAnswer:
        run_id = self.latest_incident_run()
        if run_id is None:
            return AgentAnswer(question, "incident", "No incidents recorded: all runs are healthy.")
        rca = self.analyze_run(run_id)
        assert rca is not None
        top = rca.hypotheses[0]
        text = (f"The latest incident is **{rca.incident.type}** ({rca.incident.severity}): {rca.incident.title}. "
                f"Most likely cause ({top['confidence']:.0%}): {top['cause']}. "
                f"{rca.blast_radius['rows_affected']} of {rca.blast_radius['rows_in']} rows affected. "
                + (f"{len(rca.symptoms)} other alert(s) are symptoms of it. " if rca.symptoms else "")
                + "First step: " + (rca.steps[0] if rca.steps else "see the runbook."))
        return AgentAnswer(question, "incident", text, [f"incident={rca.incident.id}", f"run={run_id}"], [rca.incident.type])

    _TYPE_WORDS = {
        "schema break": "schema_break", "schema_break": "schema_break", "rename": "schema_break",
        "contract": "contract_violation", "quarantine": "contract_violation", "duplicate": "contract_violation", "null": "contract_violation",
        "fresh": "freshness_breach", "stale": "freshness_breach", "late": "freshness_breach",
        "volume": "volume_anomaly", "row count": "volume_anomaly",
        "drift": "distribution_drift", "distribution": "distribution_drift",
        "heal": "high_heal_rate", "format": "high_heal_rate", "schema drift": "schema_drift",
    }

    def _fix(self, question: str) -> AgentAnswer:
        q = question.lower()
        itype = next((t for w, t in sorted(self._TYPE_WORDS.items(), key=lambda kv: -len(kv[0])) if w in q), None)
        if itype is None:
            run_id = self.latest_incident_run()
            primary, _ = triage(self.store.for_run(run_id)) if run_id else (None, [])
            itype = primary.type if primary else None
        if itype:
            rem = self.kb.get(itype, "Remediation")
            if rem:
                return AgentAnswer(question, "fix", f"Runbook for **{itype}**:\n{rem.text}", [f"runbook={itype}"], [itype])
        hits = self.kb.search(question, k=2)
        return self._from_hits(question, "fix", hits)

    def _fallback(self, question: str) -> AgentAnswer:
        return self._from_hits(question, "search", self.kb.search(question, k=3))

    def _from_hits(self, question: str, intent: str, hits: list[tuple[float, Chunk]]) -> AgentAnswer:
        if not hits:
            return AgentAnswer(question, intent, "I could not find anything relevant in the runbooks or the contract.")
        text = "\n\n".join(f"**{c.source} / {c.title}**\n{c.text}" for _, c in hits[:2])
        return AgentAnswer(question, intent, text, [f"match={c.source}/{c.title} score={s:.2f}" for s, c in hits], [c.source for _, c in hits])
