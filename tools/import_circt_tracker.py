#!/usr/bin/env python3
"""Import combinational benchmarks from circt-synth-tracker.

    tools/import_circt_tracker.py --list
    tools/import_circt_tracker.py add alu barrel_shifter mul
    tools/import_circt_tracker.py add Fma=32 SqrUns=28     # per-design width

Three upstream sets, all combinational and all parameterized by bitwidth:

  microbenchmarks/  small inline SystemVerilog (add, alu, mul, mux, ...)
  DatapathBench/    datapath-oriented (Fma, DotProduct, AddMop, ...)
  ELAU/             arithmetic library (AbsVal, AddCsv, ...)

DatapathBench and ELAU are git SUBMODULES with their own repository and licence
(MIT and SolderPad respectively), so their provenance names that repository,
its own revision and its own LICENSE -- not circt-synth-tracker's Apache text.
Their tests are named in snake_case (`FmaShareSgn` -> `fma_share_sgn`); the top
keeps its upstream name.

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
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scaffold import new_test  # noqa: E402
from upstream import git_rev, resolve_includes, stage, write_provenance  # noqa: E402

DEFAULT_UPSTREAM = Path(__file__).resolve().parents[2] / "circt-synth-tracker"
TRACKER_URL = "https://github.com/uenoku/circt-synth-tracker"


@dataclass(frozen=True)
class Set:
    rel: str            # the set's directory inside circt-synth-tracker
    upstream: str       # provenance name
    url: str
    licence: str        # SPDX id
    own_repo: bool      # a submodule: rev, path and LICENSE are its own
    suite: str


SETS = {
    "microbenchmarks": Set("benchmarks/comb/microbenchmarks", "circt-synth-tracker",
                           TRACKER_URL, "Apache-2.0 WITH LLVM-exception", False, "comb"),
    "DatapathBench": Set("benchmarks/comb/DatapathBench/DatapathBench", "DatapathBench",
                         "https://github.com/cowardsa/DatapathBench", "MIT", True, "datapath"),
    "ELAU": Set("benchmarks/comb/ELAU/ELAU", "ELAU",
                "https://github.com/pulp-platform/ELAU", "SHL-0.51", True, "elau"),
}


def discover(upstream: Path) -> dict[str, tuple[Path, Set]]:
    out: dict[str, tuple[Path, Set]] = {}
    for st in SETS.values():
        d = upstream / st.rel
        if not d.is_dir():
            continue
        for sv in sorted(d.rglob("*.sv")):
            out.setdefault(sv.stem, (sv, st))
    return out


def test_name(stem: str, st: Set) -> str:
    """`FmaShareSgn` -> `fma_share_sgn` for the submodule sets; micro names are already flat."""
    if not st.own_repo:
        return stem
    return re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", stem).lower()


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
        for name, (path, st) in sorted(available.items()):
            print(f"{name:<26} {test_name(name, st):<24} {path.relative_to(args.upstream)}")
        print(f"\n{len(available)} benchmarks available")
        return 0

    rc = 0
    for spec in args.names:
        stem, _, bw_text = spec.partition("=")
        bw = int(bw_text) if bw_text else args.bw
        found = available.get(stem)
        if found is None:
            print(f"✗ {stem}: not found upstream", file=sys.stderr)
            rc = 1
            continue
        src, st = found
        name = test_name(stem, st)
        dest = args.root / "tests" / name
        if dest.exists():
            print(f"· {name}: already imported")
            continue
        repo = args.upstream / st.rel if st.own_repo else args.upstream
        new_test(args.root, name, top=stem, kind="combinational", suite=st.suite)
        headers = resolve_includes(src, [src.parent])
        stage(dest, [src], headers, repo / "LICENSE")
        _strip_lit_directives(dest / "verilog" / src.name)
        licence = _own_licence(src, st, dest / "LICENSE", args.upstream)
        write_provenance(
            dest, st.upstream, git_rev(repo), str(src.relative_to(repo)), licence, st.url,
        )
        _set_config(dest / "design.toml", f"bw{bw}", {"BW": bw})
        print(f"✓ {name}: imported {stem} at BW={bw} ({1 + len(headers)} files)")
    print("\nnext: `lhdtrack import seed <name>` to generate the harness, drivers and SDC")
    return rc


def _own_licence(src: Path, st: Set, licence_file: Path, upstream: Path) -> str:
    """The source's OWN licence when its SPDX header differs from its set's.

    DatapathBench (MIT) carries several ELAU designs that keep their SolderPad
    header. The file's header is what governs it, so the test records that id
    and its LICENSE carries that text first, then the repository's own.
    """
    m = re.search(r"SPDX-License-Identifier:\s*(\S+)", src.read_text(errors="replace"))
    if not m or m.group(1) == st.licence:
        return st.licence
    spdx = m.group(1)
    origin = next((o for o in SETS.values() if o.licence == spdx and o.own_repo), None)
    if origin is None:
        raise SystemExit(f"{src}: header says {spdx}, and no known set carries that licence text")
    text = (upstream / origin.rel / "LICENSE").read_text()
    licence_file.write_text(
        f"{src.name} is licensed under {spdx} (its SPDX header; it originates from "
        f"{origin.url}).\nThe {st.upstream} repository it was taken from is {st.licence}; "
        "both texts follow.\n\n" + text + "\n\n----\n\n" + licence_file.read_text()
    )
    return spdx


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
