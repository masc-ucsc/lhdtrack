"""Create and seed test directories.

`lhdtrack new` makes the skeleton; `lhdtrack import seed` fills in everything
that is DERIVED -- the Pyrope seed, the harness pair, the driver pair, the SDC.

Nothing here overwrites a file it did not write. Every generated file carries a
`lhdtrack-generated` marker, and a file without that marker is hand-written and
therefore authoritative. That is the whole mechanism behind promoting a test
from `status.pyrope = "auto"` to `"idiomatic"`: rewrite the file, drop the
marker, and the generator leaves it alone forever after.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import gen_sdc
import gen_testbench as gtb
import prp_fixups
from ports import extract

DESIGN_TOML = """# {name} -- see README.md for provenance.
[design]
name  = "{name}"
top   = "{top}"
kind  = "{kind}"          # combinational | sequential
suite = "{suite}"         # groups the report; NOT a path component

[provenance]
upstream = ""             # e.g. "bedrock-rtl"
rev      = ""             # the exact commit this was taken from
path     = ""             # the upstream path of the source
license  = ""             # SPDX id; LICENSE carries the full text

[status]
# none | auto | idiomatic -- `auto` is a machine emission from the Verilog and
# measures FRONT ENDS, not languages, so it never enters a headline geomean.
pyrope = "none"
# none | proven | refuted | timeout | unsupported | error -- the SAME words the
# LEC flows emit (corpus.LEC_STATUS). "failed" is not one of them: a manifest
# written from this comment would be rejected by every load_test() call.
# Until `proven`, a QoR win might just be a different circuit.
lec    = "none"

# One elaborated parameter set per entry. Benchmark id is `{name}#<id>`.
[[config]]
id     = "default"
params = {{}}

[synth]
flows = ["syn_yosys_abc", "syn_lhd_verilog", "syn_lhd_pyrope"]

[sim]
flows  = ["sim_verilator", "sim_lhd_verilog", "sim_lhd_pyrope"]
# Tuned so one simulation lands in the 2-10 s window. Re-check after any large
# simulator speedup: a benchmark that has outrun its cycle count reports
# process startup, not the simulator.
cycles = 1000000
marker = "LHDTRACK-DONE"

[lec]
# Both backends prove the same obligation -- lgcheck (yosys) is the baseline,
# lhd's in-process solver is what is being measured against it.
flows = ["lec_lgyosys", "lec_lhd"]
"""

README = """# {name}

<!-- What this block does, in a sentence or two. -->

| | |
| --- | --- |
| top | `{top}` |
| kind | {kind} |
| suite | {suite} |
| upstream | _fill in_ |
| revision | _fill in_ |
| upstream path | _fill in_ |

## Known gaps

<!-- Anything a reader of the report needs to know: an unproven LEC, a Pyrope
     side that is still a machine emission, a parameter set that does not
     elaborate, a harness that needed hand-adjustment. -->

## Generated files

`sim/` and `constraints/` are produced by `lhdtrack import seed {name}` and
carry a `lhdtrack-generated` marker. Remove the marker to take a file over by
hand; the generator will not touch it again.
"""


def new_test(root: Path, name: str, top: str, kind: str, suite: str) -> Path:
    path = root / "tests" / name
    if path.exists():
        raise FileExistsError(f"{path} already exists")
    for sub in ("verilog", "pyrope", "sim", "constraints"):
        (path / sub).mkdir(parents=True)
    (path / "design.toml").write_text(
        DESIGN_TOML.format(name=name, top=top, kind=kind, suite=suite)
    )
    (path / "README.md").write_text(README.format(name=name, top=top, kind=kind, suite=suite))
    (path / "LICENSE").write_text(
        "Replace with the upstream licence text. An imported design must carry\n"
        "the licence it was published under, verbatim.\n"
    )
    (path / "verilog" / "filelist.f").write_text(
        "# One source per line, in compile order. Read by slang, verilator and yosys.\n"
    )
    return path


def seed_test(root: Path, test, tc, cfg: dict, force: bool = False) -> list[str]:
    """Generate the derived files of one test. Returns what was written."""
    made: list[str] = []
    sources = test.verilog_sources()
    if not sources:
        raise FileNotFoundError("no Verilog sources listed in verilog/filelist.f")

    params = test.configs[0].params if test.configs else {}
    pl = extract(
        test.top,
        sources,
        params,
        yosys=tc.bin("yosys") if tc.has("yosys") else None,
        include_dir=test.verilog_dir,
        verilator=tc.bin("verilator") if tc.has("verilator") else None,
        filelist=test.filelist,
    )
    if pl.source == "regex":
        # A regex scan cannot resolve a width that is a parameter expression, and
        # a harness generated from a wrong width mis-drives a bus SILENTLY --
        # the checksum would still agree across simulators because all three
        # would be equally wrong.
        raise RuntimeError(
            "neither verilator nor yosys could elaborate this design, so the port "
            "widths are unresolved; a harness built on them would mis-drive a bus "
            "silently"
        )
    if not pl.outputs:
        raise RuntimeError(f"{test.top} has no outputs -- nothing to checksum")

    # 1. Pyrope seed, if the test has no Pyrope side at all yet.
    if test.pyrope_status == "none" and tc.has("lhd") and (force or not test.pyrope_top.exists()):
        if _emit_pyrope_seed(test, tc, params):
            # Record the status HERE rather than telling a human to. A seed
            # whose manifest still says `none` is invisible to the runner, and
            # "remember to edit 172 files" is not a workflow.
            _set_status(test.root / "design.toml", "pyrope", "auto")
            made.append('pyrope/ (auto seed, status.pyrope = "auto")')
    elif test.pyrope_status == "none" and test.pyrope_top.exists():
        # Seeded by an earlier run but never recorded. Without this the manifest
        # says `none`, the runner skips every LiveHD-Pyrope flow, and the .prp
        # sitting right there is invisible.
        _set_status(test.root / "design.toml", "pyrope", "auto")
        made.append('status.pyrope = "auto" (seed was already present)')

    # 2. Harness pair + driver pair.
    for rel, text in (
        (f"{test.top}_harness.sv", gtb.harness_sv(pl, params)),
        (f"{test.top}_harness.prp", gtb.harness_prp(pl)),
        (f"{test.top}_tb.prp", gtb.tb_prp(pl, test.sim_cycles or 1_000_000)),
        (f"{test.top}_tb_verilator.cpp", gtb.tb_verilator(pl, test.sim_cycles or 1_000_000)),
    ):
        path = test.sim_dir / rel
        if path.exists() and not gtb.is_generated(path) and not force:
            continue  # hand-written: authoritative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        made.append(f"sim/{rel}")

    # 3. One SDC PER TECHNOLOGY: the period is in the library's own time unit.
    periods = cfg.get("sdc", {}).get("period", {}) or {}
    for tech, spec in sorted(periods.items()):
        period = float(spec["value"]) if isinstance(spec, dict) else float(spec)
        unit = spec.get("unit", "ns") if isinstance(spec, dict) else "ns"
        sdc = test.root / "constraints" / f"{tech}.sdc"
        if sdc.exists() and not gen_sdc.is_generated(sdc) and not force:
            continue
        sdc.parent.mkdir(parents=True, exist_ok=True)
        sdc.write_text(gen_sdc.generate(pl, period, unit))
        made.append(f"constraints/{tech}.sdc")

    return made


def _set_status(toml: Path, key: str, value: str) -> None:
    s = toml.read_text()
    pat = re.compile(rf'^({key}\s*=\s*)"[^"]*"', re.M)
    if pat.search(s):
        toml.write_text(pat.sub(rf'\g<1>"{value}"', s, count=1))


def _emit_pyrope_seed(test, tc, params: dict) -> bool:
    """`lhd compile verilog --emit-dir pyrope:` -- the machine emission.

    Runnable the day it lands, and honestly labelled: both LiveHD flows then
    enter the same LGraph, so the row measures the two front ends rather than
    the two languages until somebody rewrites it by hand.
    """
    ok = True
    try:
        subprocess.run(  # noqa: S603
            [
                str(tc.bin("lhd")), "compile", "verilog", "--top", test.top,
                "--emit-dir", f"pyrope:{test.pyrope_dir}",
                "--workdir", str(test.root / ".seed"),
                # NORMALLY EMPTY, and deliberately still passed. The top pins
                # its parameter point in the source now (`localparam`; see
                # tools/monomorphize.py), so there is nothing to override and
                # the seed elaborates the same circuit the Verilog flows do.
                # Before that, omitting these elaborated br_counter_incr at
                # MaxValue=1 -- every port u1 -- while the Verilog side used
                # MaxValue=255, and LEC rightly refuted two different circuits.
                "--", "-F", str(test.filelist), "-DSYNTHESIS",
                *[f"-G{k}={v}" for k, v in sorted(params.items())],
            ],
            check=True, capture_output=True, timeout=900,
        )
    except (subprocess.SubprocessError, OSError):
        ok = False
    # The emitter does not backtick every Pyrope keyword it can produce as a net
    # name, so its own reader rejects part of what it just wrote. Repair the
    # emission here rather than by hand, so a re-seed cannot silently reintroduce
    # a tree that `lhd compile` cannot read. See tools/prp_fixups.py.
    #
    # ALSO ON A FAILED EXIT. The emitter writes the units it could and then fails
    # on one it could not, and the next `import seed` adopts that partial tree
    # through the "seed was already present" branch -- unrepaired, it would be
    # adopted broken.
    if test.pyrope_dir.is_dir():
        prp_fixups.fix_dir(test.pyrope_dir)
    return ok and test.pyrope_top.exists()
