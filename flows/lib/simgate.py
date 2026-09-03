"""The simulation contract shared by all three simulators.

THE CHECKSUM IS THE ORACLE. Every generated testbench drives its DUT from the
same LFSR and folds every output into the same checksum, then prints:

    LHDTRACK-DONE cycles=<N> checksum=<UNSIGNED-64-BIT>

All three simulators must print the same checksum for a test to pass. That is
what stops a miscompile from being reported as a speedup -- a simulator that
runs twice as fast because it computed the wrong thing fails instead of topping
the chart. Functional correctness is the LEC gate's job; this is the tripwire.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError

_RESULT = re.compile(
    r"(?P<marker>[A-Z0-9-]*DONE)\s+cycles=(?P<cycles>\d+)\s+checksum=(?P<sum>\d+)"
)


@dataclass
class SimResult:
    marker: str
    cycles: int
    checksum: str


def parse_result(log: Path, marker: str) -> SimResult:
    text = log.read_text(errors="replace")
    m = _RESULT.search(text)
    if not m:
        raise FlowError(
            f"testbench printed no '{marker} cycles=<N> checksum=<N>' line -- "
            f"the simulation may have died before its report\n"
            + "\n".join(text.splitlines()[-15:])
        )
    return SimResult(m.group("marker"), int(m.group("cycles")), m.group("sum"))


def run_lhd_sim(ctx: FlowContext, design_input: str, tb: Path) -> dict:
    """The `lhd sim` half of a simulation row: setup / cc / exec.

    THE TIME SPLIT MATTERS. `--setup-only` only WRITES the driver sources; the
    host C++ compile happens inside `--run-only`, which rebuilds drv.bin every
    invocation. Reporting `--run-only`'s wall clock as "simulation" would report
    a clang timing. So the binary it just built is re-run separately: that is
    the simulation alone, and the remainder is the compile.

    sim.ninja=false PINS the build path. `lhd sim` uses ninja when it finds one
    on PATH and its own parallel compile otherwise; leaving that to chance makes
    cc_ms depend on whether the machine happens to have ninja installed.
    """
    lhd = ctx.tool("lhd")
    cycles = ctx.test.sim_cycles
    inputs = [design_input, str(tb)]

    # Match Verilator's two-state startup contract.  State without an explicit
    # initializer or reset powers on at zero, and source-level unknown literal
    # bits are concretized as zero.  Keep both knobs explicit: init_zero covers
    # storage while unknown_zero covers `?` literals, so neither simulator's
    # private randomization policy can affect the checksum.
    sim_policy = [
        "--set", "sim.init_zero=true",
        "--set", "sim.unknown_zero=true",
        "--set", "sim.vcd=false",
    ]
    ctx.run(
        "setup",
        [lhd, "sim", *inputs, "--setup-only", *sim_policy, "--workdir", "SW"],
    )
    run = ctx.run(
        "run",
        [lhd, "sim", *inputs, "--run-only", "--arg", f"cycles={cycles}",
         *sim_policy, "--set", "sim.ninja=false",
         "--diag-fmt", "pretty", "--workdir", "SW"],
    )

    drv = ctx.work / "SW" / "sim" / "drv.bin"
    if not drv.exists():
        raise FlowError(f"lhd sim built no driver at {drv}")
    # `sim.init_zero=true` configures the driver invocation performed inside
    # `lhd sim --run-only`; it is not baked into drv.bin.  The separately timed
    # execution must carry the equivalent runtime switch or it silently falls
    # back to seeded-random storage initialization and no longer matches the
    # Verilator `--x-initial 0` contract above.
    best, samples = ctx.run_best("exec", [drv, "--cycles", cycles, "--init-zero"])

    # cc = the whole --run-only minus the simulation it also paid for. Clamped
    # at zero: a negative value is only reachable if a stall landed in the lhd
    # run and not in the re-run, and a negative compile time is worse than a
    # zero one.
    cc_ms = max(run.ms - best.ms, 0)
    ctx.stage.time_ms["cc"] = cc_ms
    ctx.stage.time_ms.pop("run", None)

    result = parse_result(best.log, ctx.test.sim_marker)
    return {
        "sim": {
            "cycles": cycles,
            "exec_ms": best.ms,
            "exec_samples_ms": samples,
            "cycles_per_s": int(cycles / (best.ms / 1000)) if best.ms else 0,
            "checksum": result.checksum,
        }
    }
