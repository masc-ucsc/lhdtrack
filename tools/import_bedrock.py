#!/usr/bin/env python3
"""Import sequential library blocks from bedrock-rtl.

    tools/import_bedrock.py --list
    tools/import_bedrock.py add br_fifo_flops br_arb_rr
    tools/import_bedrock.py add br_counter_incr --config 'w8:MaxValue=255'

bedrock is the sequential half of the corpus: FIFOs, arbiters, counters, CDC,
ECC, AMBA. Two things make it importable rather than just copyable.

MACRO HEADERS. Every module `include`s `br_asserts_internal.svh`,
`br_registers.svh` and friends from `macros/`. They are resolved transitively
and copied beside the source, because slang looks relative to the including file
while verilator only searches its -I list -- leaving them upstream would make
the two front ends read different files.

ASSERTIONS OFF. Flows compile with `-DSYNTHESIS` and without `BR_ASSERT_ON`, so
`BR_ASSERT_*` expands to nothing. bedrock documents this as the intended
integration default for a synthesis build.

PARAMETER SETS. bedrock's own PPA.md pins a baseline and a scaled parameter set
per top; `--ppa` reads them straight out of that table so the corpus measures
the same points its authors do, rather than points chosen here.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scaffold import new_test  # noqa: E402
from upstream import git_rev, resolve_includes, stage, write_provenance  # noqa: E402

DEFAULT_UPSTREAM = Path(__file__).resolve().parents[2] / "bedrock-rtl"

# `verilog_library(name = "x", srcs = [...], deps = [...])` -- bedrock's own
# dependency graph. A module here rarely stands alone: it imports a package
# (`br_amba_pkg`) or instantiates sibling modules, and copying only its own .sv
# produces "Non-constant range in declaration" the moment yosys elaborates it.
# The BUILD files are the authoritative answer to "what does this need", so the
# importer reads them rather than guessing from `include` lines.
_LIB = re.compile(
    r"verilog_library\(\s*name\s*=\s*\"(?P<name>[^\"]+)\"(?P<body>.*?)\n\)",
    re.S,
)
_LIST = re.compile(r"(?P<key>srcs|deps)\s*=\s*\[(?P<items>.*?)\]", re.S)
_ITEM = re.compile(r"\"([^\"]+)\"")

# bedrock's PPA table row: | `top` | `A=1, B=2` | cells | ... |
PPA_ROW = re.compile(r"^\|\s*`(?P<top>br_\w+)`\s*\|\s*`(?P<params>[^`]*)`\s*\|", re.M)


def discover(upstream: Path) -> dict[str, Path]:
    out: dict[str, Path] = {}
    for sv in sorted(upstream.glob("*/rtl/*.sv")):
        out.setdefault(sv.stem, sv)
    return out


def build_graph(upstream: Path) -> dict[str, dict]:
    """Every `verilog_library` in the tree, keyed by its full bazel label."""
    graph: dict[str, dict] = {}
    for build in upstream.glob("**/BUILD.bazel"):
        if "bazel-" in str(build):
            continue
        pkg = "//" + str(build.parent.relative_to(upstream)).replace("\\", "/")
        try:
            text = build.read_text(errors="replace")
        except OSError:
            continue
        for m in _LIB.finditer(text):
            body = m.group("body")
            fields = {k: [] for k in ("srcs", "deps")}
            for lm in _LIST.finditer(body):
                fields[lm.group("key")] = _ITEM.findall(lm.group("items"))
            graph[f"{pkg}:{m.group('name')}"] = {
                "dir": build.parent,
                "srcs": fields["srcs"],
                "deps": [_abs_label(d, pkg) for d in fields["deps"]],
            }
    return graph


def _abs_label(label: str, pkg: str) -> str:
    return f"{pkg}{label}" if label.startswith(":") else label


def closure(graph: dict[str, dict], label: str) -> list[Path]:
    """Every source `label` transitively needs, DEPENDENCIES FIRST.

    Order matters: slang and verilator both read a filelist top to bottom, and
    a package has to be declared before the module that imports it.
    """
    seen: set[str] = set()
    files: list[Path] = []

    def walk(lbl: str) -> None:
        if lbl in seen or lbl not in graph:
            return
        seen.add(lbl)
        node = graph[lbl]
        for dep in node["deps"]:
            walk(dep)
        for src in node["srcs"]:
            path = node["dir"] / src
            if path.exists() and path not in files:
                files.append(path)

    walk(label)
    return files


def label_for(graph: dict[str, dict], name: str) -> str | None:
    return next((lbl for lbl in graph if lbl.rsplit(":", 1)[1] == name), None)


def ppa_configs(upstream: Path, top: str) -> list[tuple[str, dict]]:
    """The parameter sets bedrock's own PPA table measures for `top`."""
    table = upstream / "PPA.md"
    if not table.exists():
        return []
    out = []
    for m in PPA_ROW.finditer(table.read_text(errors="replace")):
        if m.group("top") != top:
            continue
        params = {}
        for kv in m.group("params").split(","):
            if "=" in kv:
                k, v = kv.split("=", 1)
                params[k.strip()] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()
        if params:
            out.append((_config_id(params), params))
    return out


def _config_id(params: dict) -> str:
    """A short, stable id: initials of each parameter plus its value.

    A long parameter set is truncated with a content hash appended rather than
    cut mid-token. The config id is part of the CACHE KEY, so two distinct
    parameter sets colliding on a truncated id would silently reuse the wrong
    result -- the one class of bug this whole repository exists to prevent.
    """
    initials = ["".join(c for c in k if c.isupper()).lower() for k, _ in sorted(params.items())]
    full = "_".join(i + str(v) for i, (_, v) in zip(initials, sorted(params.items())))
    if not full:
        return "default"
    digest = hashlib.sha256(repr(sorted(params.items())).encode()).hexdigest()[:8]
    # A SHORT ID CAN COLLIDE TOO. Initials are lossy -- `Depth` and `Data` are
    # both "d", and a lowercase name contributes nothing at all -- so two
    # distinct parameter sets could land on the same `test#config` id while the
    # length check happily left them untouched. The hash settles it whenever the
    # initials do not reconstruct the names they came from.
    if len(full) <= 40 and len(set(initials)) == len(initials) and all(initials):
        return full
    return f"{full[:31]}_{digest}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("action", choices=("add", "list"), nargs="?", default="list")
    ap.add_argument("names", nargs="*")
    ap.add_argument("--upstream", type=Path, default=DEFAULT_UPSTREAM)
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--ppa", action="store_true",
                    help="take parameter sets from the upstream PPA.md table")
    ap.add_argument("--config", action="append", default=[],
                    help="explicit config, 'id:K=V,K=V' (repeatable)")
    ap.add_argument("--list", action="store_true", dest="do_list")
    ap.add_argument(
        "--max-configs", type=int, default=1,
        help="keep at most N parameter sets (default 1: the Pyrope side is "
             "monomorphic, so a second config would synthesize the same design twice)",
    )
    args = ap.parse_args()

    available = discover(args.upstream)
    if not available:
        print(f"no RTL under {args.upstream}/*/rtl -- is the checkout there?", file=sys.stderr)
        return 1
    if args.do_list or args.action == "list":
        for name, path in sorted(available.items()):
            n = len(ppa_configs(args.upstream, name))
            print(f"{name:<44} {path.parent.parent.name:<12} {n} PPA config(s)")
        print(f"\n{len(available)} modules available")
        return 0

    rev = git_rev(args.upstream)
    macros = [args.upstream / "macros", args.upstream]
    graph = build_graph(args.upstream)
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

        suite = src.parent.parent.name
        new_test(args.root, name, top=name, kind="sequential", suite=suite)

        # Transitive sources from bedrock's own BUILD graph, then the macro
        # headers those pull in. Headers are staged separately from `srcs`
        # because they are `include`d, not compiled.
        label = label_for(graph, name)
        srcs = closure(graph, label) if label else [src]
        if src not in srcs:
            srcs.append(src)
        srcs = [f for f in srcs if f.suffix == ".sv"]
        headers: list[Path] = []
        for f in srcs:
            for h in resolve_includes(f, [f.parent, *macros]):
                if h not in headers:
                    headers.append(h)
        stage(dest, srcs, headers, args.upstream / "LICENSE")
        write_provenance(
            dest, "bedrock-rtl", rev, str(src.relative_to(args.upstream)), "Apache-2.0"
        )

        configs = [_parse_config(c) for c in args.config]
        if not configs and args.ppa:
            configs = ppa_configs(args.upstream, name)
        if args.max_configs > 0:
            configs = configs[: args.max_configs]
        if configs:
            _set_configs(dest / "design.toml", configs)
        print(
            f"✓ {name}: imported ({len(srcs)} source(s) + {len(headers)} header(s), "
            f"{len(configs) or 1} config(s))"
        )
    print("\nnext: `lhdtrack import seed <name>` to generate the harness, drivers and SDC")
    return rc


def _parse_config(spec: str) -> tuple[str, dict]:
    cid, _, rest = spec.partition(":")
    params = {}
    for kv in rest.split(","):
        if "=" in kv:
            k, v = kv.split("=", 1)
            params[k.strip()] = int(v) if v.strip().lstrip("-").isdigit() else v.strip()
    return cid or "default", params


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _set_configs(toml: Path, configs: list[tuple[str, dict]]) -> None:
    blocks = []
    for cid, params in configs:
        # QUOTE WHAT IS NOT A NUMBER. `Mode = fast` is not TOML, and the file it
        # lands in is parsed by tomllib on every single `discover()` -- one
        # unquoted string value makes the whole corpus unreadable.
        body = ", ".join(f"{k} = {_toml_value(v)}" for k, v in sorted(params.items()))
        blocks.append(f'[[config]]\nid     = "{cid}"\nparams = {{ {body} }}')
    s = toml.read_text().replace(
        '[[config]]\nid     = "default"\nparams = {}', "\n\n".join(blocks)
    )
    toml.write_text(s)


if __name__ == "__main__":
    raise SystemExit(main())
