#!/usr/bin/env python3
"""Render a paired ABC/native-stage matrix from the append-only ledger."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.ledger import Ledger
from lhdtrack.report.usyn_ablation import render, summarize


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("abc", "selection", "residual", "feedback"):
        parser.add_argument(f"--{name}", required=True, help="retained evaluation run ID")
    parser.add_argument("--output", type=Path, required=True, help="JSON summary under data/")
    args = parser.parse_args()
    specs = {name: json.loads((ROOT / "var/runs" / getattr(args, name)
                              / "evaluation.json").read_text())
             for name in ("abc", "selection", "residual", "feedback")}
    summary = summarize(Ledger(ROOT).load(specs["abc"]["host"]), specs)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    target = ROOT / "target" / (args.output.stem + ".html")
    target.write_text(render(summary))
    print(target)
    print(json.dumps(summary["headline"], sort_keys=True))


if __name__ == "__main__":
    main()
