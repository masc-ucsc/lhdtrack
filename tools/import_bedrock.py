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
# `shared_fifo_verilog_library(name = "x", src = "x.sv", deps = [...])` -- the
# codegen macros spell their single source `src = "..."`, not `srcs = [...]`.
# `_LIB` matches them anyway (their names END in `verilog_library`), so their
# deps were walked while the module's own .sv was never staged: every
# br_fifo_shared_*_flops filelist elaborated to "unknown module
# br_fifo_shared_*_ctrl".
_SRC = re.compile(r"(?<![\w])src\s*=\s*\"([^\"]+)\"")

# A module instantiation, for `unstaged_modules` below: `br_gate_and2 u (` or
# `br_mux_bin_structured_gates #(`. The identifier has to be followed by an
# instance name or a parameter override, which keeps prose in a comment from
# looking like a dependency.
_INST = re.compile(r"\b(br_\w+)\s*(?:#\s*\(|[A-Za-z_]\w*\s*(?:\[[^\]]*\]\s*)?\()")
_DEFINES = re.compile(r"^\s*module\s+(\w+)", re.M)
# `define NAME(args) body`, body continued with trailing backslashes.
_MACRO_DEF = re.compile(r"^\s*`define\s+(\w+)[^\n\\]*((?:\\\n.*?)*)$", re.M)
_MACRO_USE = re.compile(r"`(\w+)")

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
                "srcs": fields["srcs"] + _SRC.findall(body),
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


def module_index(upstream: Path) -> dict[str, Path]:
    """Every module bedrock declares, and the file that declares it.

    Prefer the file whose STEM IS THE MODULE NAME. `br_mux_bin_structured_gates`
    is declared twice upstream -- once for real, once in
    `br_mux_bin_structured_gates_mock.sv`, which wraps `br_mux_bin` and says in
    its own header "For synthesis, make sure you include
    br_mux_bin_structured_gates.sv in the filelist instead of this file!!". This
    corpus is a synthesis corpus, so the stem match picks the right one.
    """
    index: dict[str, Path] = {}
    for sv in sorted(upstream.glob("**/*.sv")):
        if "bazel-" in str(sv):
            continue
        for name in _DEFINES.findall(sv.read_text(errors="replace")):
            prior = index.get(name)
            if prior is not None and prior.stem == name:
                continue
            index[name] = sv
    return index


def unstaged_modules(srcs: list[Path], macros: list[Path], index: dict[str, Path]) -> list[str]:
    """Modules the staged sources instantiate but no staged source declares.

    bedrock leaves the gate library out of every `verilog_library` ON PURPOSE --
    "Omitting //gate/rtl:br_gate_mock so that downstream targets ... can decide
    whether to use these behavioral models or swap them out for some other
    vendor models" -- and adds it back only in the test suites, which are not
    part of the dependency graph `closure()` walks. Twenty tests were imported
    that way and every one of them elaborated to `unknown module
    br_gate_cdc_sync`.

    THIS CORPUS IS THE DOWNSTREAM INTEGRATOR and the mock gates are the only
    gate models it has, so the omitted file has to be added -- but only where it
    is really reached. `br_ram_flops_tile` names `br_mux_bin_structured_gates`
    inside a generate branch that most parameter sets do not take, so this
    reports rather than stages: a name here is a prompt to add the file to
    `filelist.f` and re-run slang, not proof that the design needs it.
    """
    bodies = [f.read_text(errors="replace") for f in srcs]
    text = _expanded(bodies, macros, srcs)
    defined = set(_DEFINES.findall(text))
    return [
        f"{name} ({index[name].relative_to(index[name].parents[2])})"
        for name in dict.fromkeys(_INST.findall(text))
        if name not in defined and name in index
    ]


def _expanded(sources, macros: list[Path], srcs: list[Path]) -> str:
    """Source text plus the body of every macro the sources actually invoke.

    A gate instance is usually written by a macro: `BR_GATE_CDC_MAXDEL(a, b)` in
    the source expands to `br_gate_cdc_maxdel ... (` inside `br_gates.svh`, so
    scanning the sources alone finds no dependency at all. Scanning the whole
    header instead finds too many -- `br_gates.svh` also defines
    `BR_GATE_CDC_RST_SYNC_STAGES`, whose body names `br_cdc_rst_sync` even in a
    design that never invokes it. Only the bodies of INVOKED macros are added.
    """
    text = "\n".join(sources)
    defs: dict[str, str] = {}
    seen: set[Path] = set()
    for f in srcs:
        for h in resolve_includes(f, [f.parent, *macros]):
            if h in seen:
                continue
            seen.add(h)
            for name, body in _MACRO_DEF.findall(h.read_text(errors="replace")):
                defs.setdefault(name, body)
    pending = list(dict.fromkeys(_MACRO_USE.findall(text)))
    used: set[str] = set()
    while pending:
        name = pending.pop()
        if name in used or name not in defs:
            continue
        used.add(name)
        text += "\n" + defs[name]
        pending.extend(_MACRO_USE.findall(defs[name]))
    return text


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
    index = module_index(args.upstream)
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
        for missing in unstaged_modules(srcs, macros, index):
            print(f"  ! instantiates {missing} -- add it to filelist.f if slang wants it")
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
