"""Zero-dependency line coverage (Python 3.12+, uses sys.monitoring).

Usage: python scripts/coverage_lines.py [--fail-under 90]
CI uses coverage.py; this exists so the suite can be measured on a machine with no pip access.
Lines marked ``# pragma: no cover`` (and the block they open) are excluded, like coverage.py.
"""
import argparse
import ast
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src" / "lakehouseguard"
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

mon = sys.monitoring
TOOL = mon.COVERAGE_ID
hit: dict[str, set[int]] = {}


def on_line(code, line):
    if code.co_filename.startswith(str(SRC)):
        hit.setdefault(code.co_filename, set()).add(line)
    return mon.DISABLE


def code_lines(code) -> set[int]:
    lines = {ln for _, _, ln in code.co_lines() if ln}
    for const in code.co_consts:
        if hasattr(const, "co_lines"):
            lines |= code_lines(const)
    return lines


def excluded(source: str) -> set[int]:
    lines = source.splitlines()
    out: set[int] = set()
    for node in ast.walk(ast.parse(source)):
        if hasattr(node, "lineno") and "pragma: no cover" in lines[node.lineno - 1]:
            out |= set(range(node.lineno, node.end_lineno + 1))
    for i, text in enumerate(lines, 1):
        if "pragma: no cover" in text:
            out.add(i)
    return out


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fail-under", type=float, default=0)
    args = ap.parse_args()
    mon.use_tool_id(TOOL, "cov")
    mon.register_callback(TOOL, mon.events.LINE, on_line)
    mon.set_events(TOOL, mon.events.LINE)
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"), top_level_dir=str(ROOT))
    result = unittest.TextTestRunner(verbosity=0).run(suite)
    mon.set_events(TOOL, 0)

    total = covered = 0
    print(f"\n{'file':<28}{'stmts':>7}{'miss':>6}{'cover':>8}  missing")
    for f in sorted(SRC.glob("*.py")):
        src = f.read_text(encoding="utf-8")
        want = code_lines(compile(src, str(f), "exec")) - excluded(src)
        got = hit.get(str(f), set()) & want
        miss = sorted(want - got)
        total += len(want)
        covered += len(got)
        pct = 100 * len(got) / len(want) if want else 100
        print(f"{f.name:<28}{len(want):>7}{len(miss):>6}{pct:>7.0f}%  {','.join(map(str, miss[:14]))}")
    pct = 100 * covered / total if total else 100
    print(f"{'TOTAL':<28}{total:>7}{total - covered:>6}{pct:>7.1f}%")
    ok = result.wasSuccessful() and pct >= args.fail_under
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
