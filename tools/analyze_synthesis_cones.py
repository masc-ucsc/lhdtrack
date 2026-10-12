#!/usr/bin/env python3
"""Compare named data/output cones in two flattened, mapped Yosys JSON netlists."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lib.logic_cones import compare_cones, index_cones
from lib.logic_depth import liberty_cells
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path, required=True)
    parser.add_argument("--measured", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--tech", default="asap7")
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--data-port", action="append", help="default: D")
    args = parser.parse_args()
    tc = Toolchain.load(args.toolchain_root)
    tech = tc.tech(args.tech)
    known, sequential = liberty_cells(tech.liberty)
    data_ports = frozenset(args.data_port or ["D"])
    indexed = []
    for path in (args.baseline, args.measured):
        module = json.loads(path.read_text())["modules"][args.top]
        indexed.append(index_cones(module, known, sequential, data_ports))
    result = compare_cones(*indexed)
    result["inputs"] = {label: {"path": str(path.resolve()),
                                "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
                        for label, path in (("baseline", args.baseline),
                                            ("measured", args.measured))}
    result["library"] = {"tech": args.tech, "sha256": tech.sha256,
                         "files_sha256": {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                                          for p in tech.liberty}}
    result["top"], result["data_ports"] = args.top, sorted(data_ports)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(f"Matched {result['matched_endpoints']} endpoints; baseline-only "
          f"{len(result['baseline_only'])}; measured-only {len(result['measured_only'])}")
    for row in result["groups"][:12]:
        b, m = row["baseline"], row["measured"]
        print(f"{row['group']}: cells {b['cells']} -> {m['cells']} "
              f"({row['cell_delta']:+d}), levels {b['levels']} -> {m['levels']}")
    print(args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
