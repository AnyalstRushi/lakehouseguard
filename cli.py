"""Command line interface: ``lakehouseguard <demo|validate|ask|incidents|rca|report>``."""
from __future__ import annotations

import argparse
import sys

import pandas as pd

from .agent import IncidentAgent, OllamaClient
from .contracts import Contract, validate
from .demo import run_demo
from .report import build_report
from .storage import Lakehouse

DEFAULT_CONTRACT = "contracts/orders.yaml"


def _parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="lakehouseguard", description="Contract-driven self-healing lakehouse")
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp: argparse.ArgumentParser) -> None:
        sp.add_argument("--root", default="lakehouse_data", help="lakehouse directory")
        sp.add_argument("--contract", default=DEFAULT_CONTRACT)

    d = sub.add_parser("demo", help="run the full demo (healthy warm-up, then 9 failure modes)")
    common(d)
    d.add_argument("--seed", type=int, default=42)
    d.add_argument("--out", default="site", help="where to write the HTML dashboard")

    v = sub.add_parser("validate", help="check a CSV against a contract (non-zero exit on failure; use in producer CI)")
    v.add_argument("file")
    v.add_argument("--contract", default=DEFAULT_CONTRACT)

    a = sub.add_parser("ask", help="ask the incident agent a question")
    common(a)
    a.add_argument("question")
    a.add_argument("--ollama", metavar="MODEL", help="optionally phrase the answer with a local Ollama model")

    i = sub.add_parser("incidents", help="list incidents")
    common(i)

    r = sub.add_parser("rca", help="root-cause analysis for an incident id (or the latest)")
    common(r)
    r.add_argument("incident", nargs="?")

    rp = sub.add_parser("report", help="rebuild the HTML dashboard")
    common(rp)
    rp.add_argument("--out", default="site")
    return p


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    contract = Contract.from_yaml(args.contract)

    if args.cmd == "demo":
        pipe, results = run_demo(args.root, contract, seed=args.seed)
        path = build_report(pipe.lake, contract, args.out)
        bad = [r for r in results if r.status != "success"]
        print(f"{len(results)} runs, {len(bad)} with incidents, dashboard: {path}")
        for r in bad:
            print(f"  {r.batch_id:<34} {r.status:<9} {', '.join(r.incident_types)}")
        return 0

    if args.cmd == "validate":
        df = pd.read_csv(args.file)
        res = validate(df, contract)
        print(f"rows={res.total_rows} clean={len(res.clean)} quarantined={len(res.quarantine)} quality={res.quality_score}%")
        for v in res.violations:
            print(f"  {v.rule}:{v.column} x{v.count} e.g. {v.sample}")
        return 0 if (not res.violations and not res.blocked) else 2

    lake = Lakehouse(args.root)
    llm = OllamaClient(args.ollama) if getattr(args, "ollama", None) else None
    agent = IncidentAgent(lake, contract, llm=llm)

    if args.cmd == "ask":
        ans = agent.ask(args.question)
        print(ans.text)
        if ans.llm_error:
            print(f"\n(local LLM unavailable, showing the offline answer: {ans.llm_error})", file=sys.stderr)
        return 0
    if args.cmd == "incidents":
        for inc in agent.store.all():
            print(f"{inc.id}  {inc.severity:<8} {inc.type:<20} {inc.run_id}  {inc.title}")
        return 0
    if args.cmd == "rca":
        if args.incident:
            rca = agent.analyze(args.incident)
        else:
            run_id = agent.latest_incident_run()
            rca = agent.analyze_run(run_id) if run_id else None
        if rca is None:
            print("No incidents.")
            return 0
        print(rca.to_markdown())
        return 0
    if args.cmd == "report":
        print(build_report(lake, contract, args.out))
        return 0
    return 1  # pragma: no cover


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
