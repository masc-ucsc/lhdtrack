"""The corpus: `tests/<name>/`, one directory per test, all the same shape.

A test carries both languages, its own generated testbench pair, and its
constraints. `design.toml` is the manifest; everything the runner needs to
schedule and key a job is in it, so discovery never has to guess from the
filesystem.

Test names are flat and globally unique. `suite` is a manifest FIELD, not a path
component, so recategorizing a test in the report never moves its directory --
which would change its cache key and silently discard its whole history.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

TESTS_DIR = "tests"

# A design.toml is REQUIRED to name these; there is no inference from filenames.
REQUIRED_FIELDS = ("name", "top", "kind", "suite")
KINDS = ("combinational", "sequential")
PYROPE_STATUS = ("none", "auto", "idiomatic")
# How the Pyrope side takes its parameters. `monomorphic` means the .prp pins
# them as comptime constants, so the test may declare only ONE config -- a
# second one would silently synthesize the same width twice and report it as
# two different measurements.
PYROPE_BINDING = ("monomorphic", "set")
# The SAME vocabulary the LEC flows emit. Two spellings for one concept ("failed"
# here, "refuted" there) is how a manifest ends up rejecting a verdict the runner
# just produced.
LEC_STATUS = (
    "none",
    "proven",
    "refuted",
    "timeout",
    "inconclusive",
    "unsupported",
    "error",
)


class CorpusError(ValueError):
    pass


@dataclass(frozen=True)
class Config:
    """One elaborated parameter set of a test.

    Configs live in the manifest rather than as sibling directories: bedrock's
    214 modules times two PPA parameter sets each would be 428 near-identical
    trees. The benchmark ID is `test#config`.

    `params` IS EMPTY FOR EVERY TEST IN THIS CORPUS, and `lhdtrack check` fails
    a test where it is not. A top module pins its point in the source as
    `localparam` (tools/monomorphize.py) so that yosys, slang, verilator, the
    generated harness and the .prp -- which cannot take a parameter at all --
    all elaborate one circuit with no flags. The field survives because the
    override plumbing is what makes a second parameter point possible later; the
    `id` still names the point the source pins.
    """

    id: str
    params: dict[str, int | str] = field(default_factory=dict)

    def verilog_defines(self) -> list[str]:
        """`-G<K>=<V>` for slang/verilator; yosys uses -chparam."""
        return [f"-G{k}={v}" for k, v in sorted(self.params.items())]


@dataclass
class Test:
    root: Path
    name: str
    top: str
    kind: str
    suite: str
    configs: list[Config]
    synth_flows: list[str]
    sim_flows: list[str]
    lec_flows: list[str]
    pyrope_status: str
    pyrope_binding: str
    lec_status: str
    sim_cycles: int
    sim_marker: str
    provenance: dict[str, str]
    raw: dict

    # -- paths -------------------------------------------------------------
    @property
    def verilog_dir(self) -> Path:
        return self.root / "verilog"

    @property
    def pyrope_dir(self) -> Path:
        return self.root / "pyrope"

    @property
    def sim_dir(self) -> Path:
        return self.root / "sim"

    @property
    def filelist(self) -> Path:
        return self.verilog_dir / "filelist.f"

    def sdc_for(self, tech: str | None) -> Path:
        """Constraints for one technology.

        PER-TECHNOLOGY, because a clock period is expressed in the LIBRARY's
        time unit: sky130 is 1ns, ASAP7 is 1ps. One shared `period = 10.0`
        means 10 ns on one and 10 ps on the other, and the 7nm run comes back
        with -73 ns of slack for a reason that is about units, not about the
        design.
        """
        if tech:
            per_tech = self.root / "constraints" / f"{tech}.sdc"
            if per_tech.exists():
                return per_tech
        return self.root / "constraints" / "default.sdc"

    @property
    def sdc(self) -> Path:
        return self.sdc_for(None)

    @property
    def pyrope_top(self) -> Path:
        return self.pyrope_dir / f"{self.top}.prp"

    @property
    def is_combinational(self) -> bool:
        return self.kind == "combinational"

    def verilog_sources(self) -> list[Path]:
        """Sources in filelist order -- order matters to slang and verilator."""
        if not self.filelist.exists():
            return sorted(self.verilog_dir.glob("*.sv"))
        out = []
        for line in self.filelist.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith(("#", "//")):
                out.append(self.verilog_dir / line)
        return out

    def source_files(self) -> list[Path]:
        """Every file whose CONTENT belongs in this test's cache key."""
        out: list[Path] = []
        for sub in ("verilog", "pyrope", "sim", "constraints"):
            d = self.root / sub
            if d.is_dir():
                out.extend(sorted(p for p in d.rglob("*") if p.is_file()))
        out.append(self.root / "design.toml")
        return [p for p in out if p.exists()]

    # -- policy ------------------------------------------------------------
    def comparable(self) -> bool:
        """May this test's numbers enter a headline comparison?

        No, until its Pyrope is hand-written AND LEC-proven against the Verilog.
        An `auto` seed enters the same LGraph the Verilog does, so it measures
        front ends rather than languages; an unproven pair might simply be two
        different circuits, which makes a QoR win meaningless.
        """
        return self.pyrope_status == "idiomatic" and self.lec_status == "proven"

    def flows_for(self, kind: str) -> list[str]:
        return {"synth": self.synth_flows, "sim": self.sim_flows}.get(kind, self.lec_flows)


def load_test(path: Path) -> Test:
    manifest = path / "design.toml"
    if not manifest.exists():
        raise CorpusError(f"{path}: no design.toml")
    doc = tomllib.loads(manifest.read_text())
    d = doc.get("design", {})

    for f in REQUIRED_FIELDS:
        if not d.get(f):
            raise CorpusError(f"{manifest}: [design].{f} is required")
    if d["kind"] not in KINDS:
        raise CorpusError(f"{manifest}: kind must be one of {KINDS}, got {d['kind']!r}")
    if d["name"] != path.name:
        raise CorpusError(f"{manifest}: [design].name {d['name']!r} != directory {path.name!r}")

    binding = doc.get("pyrope", {}).get("param_binding", "monomorphic")
    if binding not in PYROPE_BINDING:
        raise CorpusError(f"{manifest}: [pyrope].param_binding must be one of {PYROPE_BINDING}")

    status = doc.get("status", {})
    for key, allowed in (("pyrope", PYROPE_STATUS), ("lec", LEC_STATUS)):
        val = status.get(key, "none")
        if val not in allowed:
            raise CorpusError(f"{manifest}: [status].{key} must be one of {allowed}, got {val!r}")

    configs = [
        Config(id=c["id"], params=dict(c.get("params", {}))) for c in doc.get("config", [])
    ] or [Config(id="default")]

    synth = doc.get("synth", {})
    sim = doc.get("sim", {})
    return Test(
        root=path,
        name=d["name"],
        top=d["top"],
        kind=d["kind"],
        suite=d["suite"],
        configs=configs,
        synth_flows=list(synth.get("flows", [])),
        sim_flows=list(sim.get("flows", [])),
        lec_flows=list(doc.get("lec", {}).get("flows", [])),
        pyrope_status=status.get("pyrope", "none"),
        pyrope_binding=doc.get("pyrope", {}).get("param_binding", "monomorphic"),
        lec_status=status.get("lec", "none"),
        sim_cycles=int(sim.get("cycles", 0)),
        sim_marker=sim.get("marker", "LHDTRACK-DONE"),
        provenance=dict(doc.get("provenance", {})),
        raw=doc,
    )


def discover(root: Path, names: list[str] | None = None) -> list[Test]:
    """Load every test under `tests/`, or just the named ones."""
    base = root / TESTS_DIR
    if not base.is_dir():
        return []
    out = []
    for path in sorted(p for p in base.iterdir() if p.is_dir()):
        if names and path.name not in names:
            continue
        out.append(load_test(path))
    if names:
        found = {t.name for t in out}
        for missing in sorted(set(names) - found):
            raise CorpusError(f"no such test: tests/{missing}")
    return out
