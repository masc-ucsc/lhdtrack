#!/usr/bin/env python3
"""Import combinational benchmarks from circt-synth-tracker.

    tools/import_circt_tracker.py --list
    tools/import_circt_tracker.py add alu barrel_shifter mul

Three upstream sets, all combinational and all parameterized by bitwidth:

  microbenchmarks/  small inline SystemVerilog (add, alu, mul, mux, ...)
  DatapathBench/    datapath-oriented (Fma, DotProduct, AddMop, ...)
  ELAU/             arithmetic library (AbsVal, AddCsv, ...)

The upstream `// RUN:` lines are lit harness directives, not part of the design,
and are stripped. Upstream sweeps width with `-DBW`; here that becomes a config
in design.toml, so each width gets its own cache key and its own row instead of
silently overwriting the last.

Combinational tops have no clock, so `lhdtrack import seed` will wrap each one
in a registered harness for the simulation track. Synthesis still reads the bare
module, so those wrapper flops never reach the area number.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scaffold import new_test  # noqa: E402
from upstream import git_rev, resolve_includes, stage, write_provenance  # noqa: E402

DEFAULT_UPSTREAM = Path(__file__).resolve().parents[2] / "circt-synth-tracker"
SETS = {
    "microbenchmarks": "benchmarks/comb/microbenchmarks",
    "DatapathBench": "benchmarks/comb/DatapathBench/DatapathBench",
    "ELAU": "benchmarks/comb/ELAU/ELAU",
}


def discover(upstream: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for rel in SETS.values():
        d = upstream / rel
        if not d.is_dir():
            continue
        for sv in sorted(d.rglob("*.sv")):
            out.setdefault(sv.stem, sv)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("add", "list"), nargs="?", default="list")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--bw", type=int, default=8, help="bitwidth for the initial config")
    ap.add_argument("--list", action="store_true", dest="do_list")
    args = ap.parse_args()

    available = discover(args.upstream)
    if not available:
        print(f"no benchmarks under {args.upstream} -- is the checkout there?", file=sys.stderr)
        return 1
    if args.do_list or args.action == "list":
        for name, path in sorted(available.items()):
            print(f"{name:<26} {path.relative_to(args.upstream)}")
        print(f"\n{len(available)} benchmarks available")
        return 0

    rev = git_rev(args.upstream)
    rc = 0
    for name in args.names:
        src = available.get(name)
        if src is None:
            print(f"✗ {name}: not found upstream", file=sys.stderr)
            rc = 1
            continue
        dest = args.root / "tests" / name
        if dest.exists():
            print(f"· {name}: already imported")
            continue
        new_test(args.root, name, top=name, kind="combinational", suite="comb")
        headers = resolve_includes(src, [src.parent])
        stage(dest, [src], headers, args.upstream / "LICENSE")
        _strip_lit_directives(dest / "verilog" / src.name)
        write_provenance(
            dest, "circt-synth-tracker", rev,
            str(src.relative_to(args.upstream)), "Apache-2.0 WITH LLVM-exception",
        )
        _set_config(dest / "design.toml", f"bw{args.bw}", {"BW": args.bw})
        print(f"✓ {name}: imported ({1 + len(headers)} files)")
    print("\nnext: `lhdtrack import seed <name>` to generate the harness, drivers and SDC")
    return rc


def _strip_lit_directives(path: Path) -> None:
    lines = [l for l in path.read_text().splitlines() if not l.lstrip().startswith("// RUN:")]
    path.write_text("\n".join(lines).lstrip("\n") + "\n")


def _set_config(toml: Path, cfg_id: str, params: dict) -> None:
    body = ", ".join(f"{k} = {v}" for k, v in sorted(params.items()))
    s = toml.read_text().replace(
        'id     = "default"\nparams = {}',
        f'id     = "{cfg_id}"\nparams = {{ {body} }}',
    )
    toml.write_text(s)


if __name__ == "__main__":
    raise SystemExit(main())
