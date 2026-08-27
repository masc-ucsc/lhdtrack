"""Cache keys.

    key = sha256( test sources + config params + flow recipe file
                + tool versions + Liberty hash + host class )

Every term is here for a reason a stale result would otherwise violate:

  test sources   an edited .sv or .prp is a different benchmark
  config params  Depth=4 and Depth=16 are different circuits
  flow recipe    the flow's own source file -- change how yosys is invoked and
                 the old number no longer describes the same experiment
  tool versions  read from toolchain.json, i.e. from the binary that will run
  Liberty hash   the same netlist against a different .lib is a different area
  host class     a number from another box is not evidence

The host term is the one people are tempted to drop, because dropping it would
let a fast machine seed the cache for a slow one. That is precisely the
comparison this repo must never make: half of what lhdtrack tracks is wall
clock and peak memory.
"""

from __future__ import annotations

import hashlib
from pathlib import Path

from .corpus import Config, Test
from .toolchain import Toolchain

# Which tool versions actually influence each flow's result. Keying every flow
# on every tool would invalidate cached yosys baselines on an unrelated lhd bump
# -- the exact overhead the cache exists to remove.
FLOW_TOOLS: dict[str, tuple[str, ...]] = {
    "syn_yosys_abc": ("yosys", "abc"),
    "syn_lhd_verilog": ("lhd",),
    "syn_lhd_pyrope": ("lhd",),
    "sim_verilator": ("verilator",),
    "sim_lhd_verilog": ("lhd",),
    "sim_lhd_pyrope": ("lhd",),
    "lec_lhd": ("lhd",),
    "lec_lgyosys": ("lhd", "yosys"),
    "lec_netlist": ("lhd",),
}

# qor_endpoint runs downstream of every synthesis flow, so its tools are part of
# every synthesis key.
QOR_TOOLS = ("sta", "lhd", "yosys")


def file_digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _content_digest(paths: list[Path]) -> str:
    """Hash names AND contents -- a renamed file is a changed benchmark.

    The name is the PARENT-QUALIFIED one, so `verilog/foo.sv` and `sim/foo.sv`
    are two different terms rather than one.
    """
    h = hashlib.sha256()
    for p in sorted(paths):
        h.update(f"{p.parent.name}/{p.name}".encode())
        h.update(file_digest(p).encode())
    return h.hexdigest()


def _shared_recipe(flows_dir: Path, flow_name: str) -> list[Path]:
    """The files OTHER than the flow's own that shape its measurement.

    `lib/` is imported by several flows; `qor_endpoint.py` is where every
    synthesis flow's area and delay actually come from, which is why its tools
    are already in QOR_TOOLS.
    """
    paths = sorted(p for p in (flows_dir / "lib").glob("*.py") if p.is_file())
    if flow_name.startswith("syn_"):
        endpoint = flows_dir / "qor_endpoint.py"
        if endpoint.exists():
            paths.append(endpoint)
    return paths


def cache_key(
    test: Test,
    config: Config,
    tech: str | None,
    flow_name: str,
    flow_file: Path,
    tc: Toolchain,
) -> str:
    h = hashlib.sha256()
    h.update(b"lhdtrack-key-v1\0")
    h.update(f"{test.name}\0{test.top}\0{test.kind}\0".encode())
    h.update(_content_digest(test.source_files()).encode())
    h.update(f"\0{config.id}\0".encode())
    for k, v in sorted(config.params.items()):
        h.update(f"{k}={v};".encode())
    h.update(b"\0")
    h.update(f"{flow_name}\0".encode())
    h.update(file_digest(flow_file).encode())
    # THE SHARED RECIPE COUNTS TOO. A flow's own file is not the whole recipe:
    # `flows/lib/lec.py` decides how a verdict is classified and
    # `flows/qor_endpoint.py` computes every area and delay number. Hashing only
    # the flow file meant editing either of those left every cached baseline
    # describing an experiment that no longer exists -- the one thing the flow
    # digest is here to prevent.
    h.update(_content_digest(_shared_recipe(flow_file.parent, flow_name)).encode())

    tools = set(FLOW_TOOLS.get(flow_name, ()))
    if flow_name.startswith("syn_"):
        tools |= set(QOR_TOOLS)
    for tool in sorted(tools):
        h.update(f"\0{tool}={tc.version(tool)}".encode())

    if tech:
        h.update(f"\0tech={tech}={tc.tech(tech).sha256}".encode())
    h.update(f"\0host={tc.host_class}".encode())
    return h.hexdigest()[:32]
