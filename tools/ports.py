"""Extract a module's port list.

Everything generated from a test -- the testbench pair, the registered harness,
the SDC -- is derived from this one list, so both languages and both simulators
are guaranteed to agree about what the DUT looks like.

verilator first, then yosys: both ELABORATE the design, so a port whose width
is a parameter expression comes back resolved, which a source-level parse cannot
do. The regex fallback exists only so `lhdtrack new` works before the toolchain
is built, and it says so rather than pretending to be equivalent.
"""

from __future__ import annotations

import json
import re
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

# Conventional names, matched case-insensitively. A clock or reset is DRIVEN by
# the harness, never by the stimulus LFSR -- randomizing a reset would make the
# checksum a function of the reset schedule instead of the logic.
# PREFIXES AND SUFFIXES COUNT. bedrock's multi-domain blocks name their ports
# `wr_clk`/`rd_clk` and `wr_rst`/`rd_rst`; an exact-match list classified those
# as ordinary inputs, so the generated harness clocked the DUT from a random
# LFSR bit and randomized its reset every cycle. All three simulators share the
# harness, so the checksum oracle agreed with itself and said nothing.
CLOCK_RE = re.compile(r"^(?:[A-Za-z0-9]+_)*(?:a?clk|clock)(?:_i)?$", re.I)
RESET_RE = re.compile(r"^(?:[A-Za-z0-9]+_)*(?:a?rst|a?reset)(?:_?n)?(?:_?i)?$", re.I)
_ACTIVE_LOW_RE = re.compile(r"n_?i?$", re.I)


@dataclass(frozen=True)
class Port:
    name: str
    direction: str  # input | output | inout
    width: int

    @property
    def is_clock(self) -> bool:
        return bool(CLOCK_RE.match(self.name))

    @property
    def is_reset(self) -> bool:
        return bool(RESET_RE.match(self.name))

    @property
    def active_low_reset(self) -> bool:
        # `rst_ni` is active low: the `n` is the polarity, the `i` is the
        # direction. Testing only for a trailing "n" missed it and the harness
        # then drove an active-low reset as if it were active high, holding the
        # DUT in reset for the whole simulation.
        return self.is_reset and bool(_ACTIVE_LOW_RE.search(self.name))

    @property
    def is_stimulus(self) -> bool:
        return self.direction == "input" and not self.is_clock and not self.is_reset

    @property
    def decl(self) -> str:
        return f"[{self.width-1}:0]" if self.width > 1 else ""


@dataclass
class PortList:
    top: str
    ports: list[Port]
    source: str  # "verilator" | "yosys+slang" | "yosys" | "regex"

    @property
    def inputs(self) -> list[Port]:
        return [p for p in self.ports if p.is_stimulus]

    @property
    def outputs(self) -> list[Port]:
        return [p for p in self.ports if p.direction == "output"]

    @property
    def clocks(self) -> list[Port]:
        """EVERY clock port. A multi-domain block has several (`wr_clk`,
        `rd_clk`), and wiring only the first leaves the rest dangling."""
        return [p for p in self.ports if p.direction == "input" and p.is_clock]

    @property
    def resets(self) -> list[Port]:
        # Direction matters: protocol status outputs such as
        # `push_receiver_in_reset` are named like resets but must be checksummed,
        # not driven by the harness as a second DUT input connection.
        return [p for p in self.ports if p.direction == "input" and p.is_reset]

    @property
    def clock(self) -> Port | None:
        return next(iter(self.clocks), None)

    @property
    def reset(self) -> Port | None:
        return next(iter(self.resets), None)


def extract(
    top: str,
    sources: list[Path],
    params: dict | None = None,
    yosys: Path | None = None,
    yosys_slang: Path | None = None,
    include_dir: Path | None = None,
    verilator: Path | None = None,
    filelist: Path | None = None,
) -> PortList:
    """Elaborate the design and return its ports, widths resolved.

    VERILATOR FIRST, yosys+slang second. Both elaborate, so both resolve a width
    that is a parameter expression. The Slang plugin is important for the
    package, interface, and generate constructs that plain `read_verilog -sv`
    cannot elaborate in this corpus.

    The regex scan is last and is never silently accepted: it cannot resolve a
    parameterized width, and a harness built on a wrong width mis-drives a bus
    without anyone noticing -- all three simulators would be equally wrong, so
    even the checksum oracle would agree.
    """
    if verilator and verilator.exists():
        try:
            return _from_verilator(top, sources, params or {}, verilator, include_dir, filelist)
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError, ValueError):
            pass
    if yosys and yosys.exists():
        try:
            return _from_yosys(
                top,
                sources,
                params or {},
                yosys,
                yosys_slang,
                include_dir,
                filelist,
            )
        except (subprocess.SubprocessError, OSError, json.JSONDecodeError, KeyError):
            pass
    return _from_regex(top, sources)


def _from_verilator(top, sources, params, verilator, include_dir, filelist) -> PortList:
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "tree.json"
        cmd = [
            str(verilator), "--json-only", "--top-module", top, "-Wno-fatal",
            "-DSYNTHESIS", "--json-only-output", str(out), "--Mdir", td,
        ]
        if include_dir:
            cmd.append(f"-I{include_dir}")
        cmd += [f"-G{k}={v}" for k, v in sorted(params.items())]
        if filelist and filelist.exists():
            cmd += ["-F", str(filelist)]
        else:
            cmd += [str(s) for s in sources]
        subprocess.run(cmd, check=True, capture_output=True, timeout=600)  # noqa: S603
        doc = json.loads(out.read_text())

    by_addr: dict[str, dict] = {}
    modules: list[dict] = []

    def walk(node):
        if isinstance(node, dict):
            if node.get("addr"):
                by_addr.setdefault(node["addr"], node)
            if node.get("type") == "MODULE" and node.get("name") == top:
                modules.append(node)
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)

    walk(doc)
    if not modules:
        raise KeyError(f"verilator produced no MODULE node for {top}")

    ports = []
    for child in (
        c for k, v in modules[0].items() if isinstance(v, list)
        for c in v if isinstance(c, dict)
    ):
        if child.get("type") != "VAR":
            continue
        direction = (child.get("direction") or "").lower()
        if direction not in ("input", "output", "inout"):
            continue
        ports.append(Port(child["name"], direction, _width(by_addr, child.get("dtypep"))))
    if not ports:
        raise KeyError(f"verilator resolved no ports for {top}")
    return PortList(top, ports, "verilator")


def _width(by_addr: dict, dtype_addr: str | None, depth: int = 0) -> int:
    """Total bit width of a resolved verilator dtype.

    The width lives in a `range` STRING -- `"7:0"` -- not in the `rangep` child
    list, which is empty for an ordinary packed vector. Reading only `rangep`
    resolved every port to 1 bit, and a generated harness then drove one bit of
    each bus while the checksum still agreed between any two simulators using
    the same harness. Silent, and wrong everywhere at once.

    A packed array multiplies its range by what it contains, so a
    `[3:0][7:0]` port is 32 bits -- which is what a flat harness must drive.
    """
    if not dtype_addr or depth > 12:
        return 1
    node = by_addr.get(dtype_addr)
    if node is None:
        return 1

    span = _span(node.get("range"))
    for rng in node.get("rangep") or []:
        left, right = _const(rng.get("leftp")), _const(rng.get("rightp"))
        if left is not None and right is not None:
            span *= abs(left - right) + 1

    if node.get("type") == "BASICDTYPE":
        return max(span, 1)
    inner = node.get("subDTypep") or node.get("refDTypep")
    return max(span, 1) * _width(by_addr, inner, depth + 1)


def _from_yosys(top, sources, params, yosys, yosys_slang, include_dir, filelist) -> PortList:
    """Elaborate with yosys+slang and read the ports out of `write_json`.

    The width is `len(bits)` on the port object, which is post-elaboration and
    therefore resolves a parameter expression -- the whole reason a source-level
    scan is not good enough. Module names arrive RTLIL-escaped (`\\br_delay`).
    """
    with tempfile.TemporaryDirectory() as td:
        out = Path(td) / "ports.json"
        inc = f"-I{include_dir} " if include_dir else ""
        src = " ".join(str(s) for s in sources)
        if yosys_slang and yosys_slang.exists():
            source_arg = f"-F {filelist}" if filelist and filelist.exists() else src
            params_arg = " ".join(f"-G{k}={v}" for k, v in sorted(params.items()))
            read = (
                f"read_slang --top {top} --no-proc {inc}-DSYNTHESIS "
                f"{params_arg} {source_arg}"
            )
            hierarchy = f"hierarchy -check -top {top}"
        else:
            ch = " ".join(f"-chparam {k} {v}" for k, v in sorted(params.items()))
            read = f"read_verilog -sv {inc}-DSYNTHESIS {src}"
            hierarchy = f"hierarchy -check -top {top} {ch}"
        script = (
            f"{read}; "
            f"{hierarchy}; "
            # `proc` first: the JSON backend refuses a design that still has
            # processes ("not supported by JSON backend"). It rewrites the
            # bodies, never the port list, which is all this reads.
            f"proc; "
            f"write_json {out}"
        )
        argv = [str(yosys)]
        if yosys_slang and yosys_slang.exists():
            argv += ["-m", str(yosys_slang)]
        subprocess.run(  # noqa: S603
            [*argv, "-q", "-p", script],
            check=True, capture_output=True, timeout=600,
        )
        doc = json.loads(out.read_text())

    modules = {k.lstrip("\\"): v for k, v in (doc.get("modules") or {}).items()}
    mod = modules.get(top)
    if mod is None:
        raise KeyError(f"yosys produced no module named {top}")

    ports = []
    for name, spec in (mod.get("ports") or {}).items():
        direction = str(spec.get("direction", "")).lower()
        if direction not in ("input", "output", "inout"):
            continue
        ports.append(Port(name.lstrip("\\"), direction, max(1, len(spec.get("bits") or []))))
    if not ports:
        raise KeyError(f"yosys resolved no ports for {top}")
    return PortList(top, ports, "yosys+slang" if yosys_slang else "yosys")


_RANGE = re.compile(r"^\s*(-?\d+)\s*:\s*(-?\d+)\s*$")


def _span(rng) -> int:
    """`"7:0"` -> 8. Anything unparseable is one bit, not zero."""
    if not isinstance(rng, str):
        return 1
    m = _RANGE.match(rng)
    if not m:
        return 1
    return abs(int(m.group(1)) - int(m.group(2))) + 1


def _const(nodes) -> int | None:
    for n in nodes or []:
        if isinstance(n, dict) and n.get("type") == "CONST":
            raw = str(n.get("name", ""))
            tail = raw.split("'")[-1] if "'" in raw else raw
            m = re.search(r"(?:h([0-9a-fA-F]+)|d?(\d+))\s*$", tail)
            if m:
                return int(m.group(1), 16) if m.group(1) else int(m.group(2))
    return None


# ANSI-style port declarations. `msb`/`lsb` are captured as raw text because a
# parameterized bound is not an integer -- _from_regex reports that as width 0
# rather than guessing, and scaffold.py refuses to build a harness from it.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.S)

_PORT_RE = re.compile(
    r"\b(?P<dir>input|output|inout)\b"
    r"(?:\s+(?:wire|reg|logic|bit|signed|unsigned|var))*"
    r"(?:\s*\[\s*(?P<msb>[^\]:]+?)\s*:\s*(?P<lsb>[^\]]+?)\s*\])?"
    r"\s+(?P<name>\w+)",
    re.M,
)


def _from_regex(top: str, sources: list[Path]) -> PortList:
    """Last-resort source scan.

    Widths that are parameter expressions cannot be resolved without
    elaboration, so they come back as 1 and the caller is told the list came
    from `regex`. Generating a testbench from this would silently mis-drive a
    bus, which is why scaffold.py refuses to and asks for the toolchain instead.
    """
    text = "\n".join(p.read_text(errors="replace") for p in sources if p.exists())
    # Comments first: bedrock documents its ports in prose ("// the input signal
    # ... the output is ..."), and scanning that prose invented ports named
    # `signal` and `is` that no module has.
    text = _COMMENT_RE.sub(" ", text)
    ports, seen = [], set()
    for m in _PORT_RE.finditer(text):
        name = m.group("name")
        if name in seen:
            continue
        seen.add(name)
        width = 1
        msb, lsb = m.group("msb"), m.group("lsb")
        if msb and lsb:
            try:
                width = int(msb) - int(lsb) + 1
            except ValueError:
                width = 0  # unresolved: a parameter expression
        ports.append(Port(name, m.group("dir"), width))
    return PortList(top, ports, "regex")
