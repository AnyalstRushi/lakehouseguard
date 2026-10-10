"""Self-contained HTML dashboard (inline SVG, no JavaScript, no external requests).

Designed to be published on GitHub Pages straight from CI.
"""
from __future__ import annotations

import html
import re
from pathlib import Path
from typing import Any

from .agent import IncidentAgent
from .contracts import Contract
from .incidents import SEVERITY_ORDER
from .storage import Lakehouse

COLORS = {"success": "#2dd4a8", "degraded": "#f5b942", "blocked": "#ff5c7a"}
SEV = {"critical": "#ff5c7a", "high": "#ff9b54", "medium": "#f5b942", "low": "#7aa2ff"}
QUESTIONS = ["What is the status of the pipeline?", "Why did revenue drop?", "What did this cost?", "How do I fix a schema break?"]


def _e(x: Any) -> str:
    return html.escape(str(x))


def _line_chart(values: list[float], statuses: list[str], w: int = 560, h: int = 180, lo: float = 0, hi: float = 100) -> str:
    n = len(values)
    if n == 0:
        return ""
    pad_l, pad_b, pad_t = 34, 20, 10
    xs = [pad_l + i * (w - pad_l - 10) / max(n - 1, 1) for i in range(n)]
    ys = [pad_t + (1 - (v - lo) / (hi - lo)) * (h - pad_t - pad_b) for v in values]
    pts = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, ys))
    grid = "".join(f'<line x1="{pad_l}" x2="{w-10}" y1="{pad_t + f*(h-pad_t-pad_b):.1f}" y2="{pad_t + f*(h-pad_t-pad_b):.1f}" stroke="#25324a" stroke-width="1"/>'
                   f'<text x="{pad_l-6}" y="{pad_t + f*(h-pad_t-pad_b)+4:.1f}" fill="#7d8aa5" font-size="10" text-anchor="end">{hi - f*(hi-lo):.0f}</text>' for f in (0, .5, 1))
    dots = "".join(f'<circle cx="{x:.1f}" cy="{y:.1f}" r="4" fill="{COLORS.get(s, "#7aa2ff")}"><title>run {i+1}: {values[i]:.1f}% ({s})</title></circle>' for i, (x, y, s) in enumerate(zip(xs, ys, statuses)))
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Quality score per run"><{"polyline"} points="{pts}" fill="none" stroke="#5b8cff" stroke-width="2"/>{grid}{dots}</svg>'


def _bar_chart(series: list[tuple[str, list[float], str]], w: int = 560, h: int = 180) -> str:
    n = len(series[0][1])
    totals = [sum(s[1][i] for s in series) for i in range(n)]
    top = max(totals) or 1
    pad_l, pad_b, pad_t = 34, 20, 10
    bw = (w - pad_l - 10) / n
    out = []
    for i in range(n):
        y = h - pad_b
        for _, vals, color in series:
            bh = vals[i] / top * (h - pad_t - pad_b)
            y -= bh
            out.append(f'<rect x="{pad_l + i*bw + 1:.1f}" y="{y:.1f}" width="{max(bw-2, 1):.1f}" height="{bh:.1f}" fill="{color}"><title>run {i+1}: {vals[i]:.0f}</title></rect>')
    axis = f'<text x="{pad_l-6}" y="{pad_t+4}" fill="#7d8aa5" font-size="10" text-anchor="end">{top:.0f}</text><text x="{pad_l-6}" y="{h-pad_b+4}" fill="#7d8aa5" font-size="10" text-anchor="end">0</text>'
    return f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="Rows per run">{axis}{"".join(out)}</svg>'


CSS = """
:root{color-scheme:dark;--bg:#0b1020;--card:#121a2f;--line:#25324a;--tx:#e8eefc;--mu:#8fa0c2;--ac:#5b8cff;--ok:#2dd4a8;--wa:#f5b942;--bad:#ff5c7a}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--tx);font:15px/1.55 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;padding:24px}
main{max-width:1100px;margin:0 auto}h1{font-size:28px;margin:0 0 4px}h2{font-size:18px;margin:34px 0 12px}.sub{color:var(--mu);margin:0 0 22px}
.grid{display:grid;gap:14px}.kpis{grid-template-columns:repeat(auto-fit,minmax(150px,1fr))}.two{grid-template-columns:repeat(auto-fit,minmax(340px,1fr))}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:16px;overflow-x:auto}.kpi b{display:block;font-size:26px}.kpi span{color:var(--mu);font-size:13px}
table{width:100%;border-collapse:collapse;font-size:13.5px}th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}th{color:var(--mu);font-weight:600}
.badge{display:inline-block;padding:2px 9px;border-radius:999px;font-size:12px;font-weight:600;color:#0b1020}
details{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:12px 16px;margin-bottom:10px}summary{cursor:pointer;font-weight:600}
code{white-space:nowrap}code,pre{background:#0a0f1d;border-radius:8px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:12.5px}pre{padding:10px;overflow-x:auto}
svg{width:100%;height:auto}.legend{color:var(--mu);font-size:12px;margin-top:6px}.dot{display:inline-block;width:9px;height:9px;border-radius:50%;margin-right:5px}
.q{color:var(--ac);font-weight:600}.foot{color:var(--mu);font-size:12px;margin-top:34px}
@media(max-width:600px){body{padding:14px}h1{font-size:22px}}
"""


def build_report(lake: Lakehouse, contract: Contract, out_dir: str | Path, title: str = "LakehouseGuard") -> Path:
    runs = lake.read_jsonl("runs.jsonl")
    agent = IncidentAgent(lake, contract)
    incidents = sorted(agent.store.all(), key=lambda i: (i.ts, SEVERITY_ORDER[i.severity]))
    n = len(runs)
    ok = sum(r["status"] == "success" for r in runs)
    quarantined = sum(r["rows_quarantined"] for r in runs)
    healed = sum(r["rows_healed"] for r in runs)
    total_cost = sum(r["cost_inr"] for r in runs)
    wasted = sum(r["wasted_inr"] for r in runs)
    avg_q = sum(r["quality_score"] for r in runs) / n if n else 0

    def kpi(v: str, label: str) -> str:
        return f'<div class="card kpi"><b>{_e(v)}</b><span>{_e(label)}</span></div>'

    kpis = "".join([kpi(str(n), "pipeline runs"), kpi(f"{ok / n:.0%}" if n else "n/a", "runs fully healthy"), kpi(str(len(incidents)), "incidents detected"),
                    kpi(f"{quarantined:,}", "bad rows kept out of Silver"), kpi(f"{healed:,}", "rows auto-repaired"), kpi(f"{avg_q:.1f}%", "avg quality score"),
                    kpi(f"INR {total_cost:.2f}", f"total run cost (INR {wasted:.2f} wasted)")])

    quality = _line_chart([r["quality_score"] for r in runs], [r["status"] for r in runs]) if n else ""
    rows_chart = _bar_chart([("clean", [r["rows_clean"] for r in runs], "#2dd4a8"), ("quarantined", [r["rows_quarantined"] for r in runs], "#ff5c7a")]) if n else ""
    legend_q = "".join(f'<span><span class="dot" style="background:{c}"></span>{s}&nbsp;&nbsp;</span>' for s, c in COLORS.items())

    inc_rows = []
    for i in incidents:
        h = agent.analyze(i).hypotheses[0]
        inc_rows.append(f'<tr><td><code>{_e(i.id)}</code></td><td>{_e(i.run_id.removeprefix("run-"))}</td><td>{_e(i.type)}</td>'
                        f'<td><span class="badge" style="background:{SEV[i.severity]}">{_e(i.severity)}</span></td><td>{_e(i.title)}</td>'
                        f'<td>{_e(h["cause"])} ({h["confidence"]:.0%})</td></tr>')
    inc_table = ('<table><thead><tr><th>ID</th><th>Batch</th><th>Type</th><th>Severity</th><th>What happened</th><th>Most likely cause</th></tr></thead><tbody>'
                 + "".join(inc_rows) + "</tbody></table>") if inc_rows else "<p>No incidents. Everything is healthy.</p>"

    cards = []
    for r in runs:
        if not r.get("incident_ids"):
            continue
        rca = agent.analyze_run(r["run_id"])
        if rca is None:
            continue
        hyp = "".join(f"<li>{h['confidence']:.0%} {_e(h['cause'])} <i>({_e(h['why'])})</i></li>" for h in rca.hypotheses[:3])
        steps = "".join(f"<li>{_e(re.sub(r'^\d+\.\s*', '', s).replace(chr(96), ''))}</li>" for s in rca.steps)
        sym = ("<p><b>Symptoms of this root cause:</b> " + _e(", ".join(s.type for s in rca.symptoms)) + "</p>") if rca.symptoms else ""
        patch = f"<pre>{_e(rca.patch.strip('`').replace('python', '', 1).replace('sql', '', 1).strip())}</pre>" if rca.patch else ""
        br = rca.blast_radius
        cards.append(f'<details><summary>{_e(rca.incident.id)} · {_e(rca.incident.type)} · {_e(rca.incident.title)}</summary>'
                     f'<p><b>Blast radius:</b> {br["rows_affected"]} of {br["rows_in"]} rows ({br["pct_of_batch"]:.0%}), status {_e(br["status"])}; '
                     f'downstream: {_e(", ".join(br["downstream"]))}.</p><p><b>Likely causes</b></p><ul>{hyp}</ul>{sym}'
                     f'<p><b>Remediation</b></p><ol>{steps}</ol>{patch}</details>')

    qa = "".join(f'<div class="card"><div class="q">Q: {_e(q)}</div><p>{_e(agent.ask(q).text.replace("**", ""))}</p></div>' for q in QUESTIONS)

    page = f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)} · data reliability dashboard</title><style>{CSS}</style></head><body><main>
<h1>{_e(title)}</h1><p class="sub">Contract-driven, self-healing Medallion lakehouse with an AI incident agent. Every number below is generated by the pipeline run in CI.</p>
<div class="grid kpis">{kpis}</div>
<h2>Pipeline health</h2><div class="grid two"><div class="card"><b>Quality score per run</b>{quality}<div class="legend">{legend_q}</div></div>
<div class="card"><b>Rows per run</b>{rows_chart}<div class="legend"><span class="dot" style="background:#2dd4a8"></span>clean&nbsp;&nbsp;<span class="dot" style="background:#ff5c7a"></span>quarantined</div></div></div>
<h2>Incidents</h2><div class="card">{inc_table}</div>
<h2>Root-cause analysis</h2>{"".join(cards) or "<p>No incidents to analyse.</p>"}
<h2>Ask the agent</h2><div class="grid two">{qa}</div>
<p class="foot">Contract {_e(contract.name)} v{_e(contract.version)} · generated by LakehouseGuard · offline agent, no API key required.</p></main></body></html>"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    path = out / "index.html"
    path.write_text(page, encoding="utf-8")
    return path
