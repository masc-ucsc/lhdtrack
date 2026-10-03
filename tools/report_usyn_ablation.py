#!/usr/bin/env python3
"""Render a paired ABC/native-stage matrix from retained measurements."""
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
        parser.add_argument(f"--{name}", help="retained evaluation run ID")
    parser.add_argument("--improved", help="optional retained native timing-iteration run ID")
    parser.add_argument("--from-summary", type=Path,
                        help="render committed JSON without local run state")
    parser.add_argument("--output", type=Path, help="JSON summary under data/")
    args = parser.parse_args()
    names = ["abc", "selection", "residual", "feedback"]
    if args.from_summary:
        if any(getattr(args, name) for name in (*names, "improved")):
            parser.error("--from-summary cannot be combined with run IDs")
        summary = json.loads(args.from_summary.read_text())
        output = args.output or args.from_summary
    else:
        if not all(getattr(args, name) for name in names) or not args.output:
            parser.error("provide all four stage run IDs and --output, or use --from-summary")
        if args.improved:
            names.append("improved")
        specs = {name: json.loads((ROOT / "var/runs" / getattr(args, name)
                                  / "evaluation.json").read_text()) for name in names}
        summary = summarize(Ledger(ROOT).load(specs["abc"]["host"]), specs)
        output = args.output
    if not args.from_summary or args.output:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(summary, indent=2) + "\n")
    target = ROOT / "target" / (output.stem + ".html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render(summary))
    print(target)
    print(json.dumps(summary["headline"], sort_keys=True))


if __name__ == "__main__":
    main()
