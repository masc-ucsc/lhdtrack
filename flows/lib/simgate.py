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
    text = "\n".join(line for line in log.read_text(errors="replace").splitlines()
                     if not line.startswith("$ "))
    m = next((match for match in _RESULT.finditer(text) if match["marker"] == marker), None)
    if not m:
        raise FlowError(
            f"testbench printed no '{marker} cycles=<N> checksum=<N>' line -- "
            f"the simulation may have died before its report\n"
            + "\n".join(text.splitlines()[-15:])
        )
    return SimResult(m.group("marker"), int(m.group("cycles")), m.group("sum"))


def run_lhd_sim(
    ctx: FlowContext, design_input: str, tb: Path, *, validation_only: bool = False,
    backend: str = "slop",
) -> dict:
    """The `lhd sim` half of a simulation row: setup / cc / exec.

    THE TIME SPLIT MATTERS. `--setup-only` writes driver sources and, for LLVM,
    kernel objects. `--run-only` completes host compilation/linking, rebuilds
    drv.bin and runs it. The binary is re-run separately to measure simulation
    alone; subtracting its best execution from run-only estimates compile time.
    Compare setup + compile across backends because object generation straddles
    those stages differently. Verilog's separate elab stage also belongs in
    the total cost of preparing and executing one simulation.

    sim.ninja=false PINS the build path. `lhd sim` uses ninja when it finds one
    on PATH and its own parallel compile otherwise; leaving that to chance makes
    cc_ms depend on whether the machine happens to have ninja installed.
    """
    lhd = ctx.tool("lhd")
    if backend not in ("slop", "llvm"):
        raise ValueError(f"unknown simulation backend: {backend}")
    cycles = ctx.test.sim_cycles
    inputs = [design_input, str(tb)]
    build_jobs = ctx.sim_build_jobs

    # Match Verilator's two-state startup contract.  State without an explicit
    # initializer or reset powers on at zero, and source-level unknown literal
    # bits are concretized as zero.  Keep both knobs explicit: init_zero covers
    # storage while unknown_zero covers `?` literals, so neither simulator's
    # private randomization policy can affect the checksum.
    sim_policy = [
        "--set", "sim.init_zero=true",
        "--set", "sim.unknown_zero=true",
        "--set", f"sim.tune.backend={backend}",
        "--set", "sim.tune.profile=off",
        "--set", f"sim.jobs={build_jobs}",
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

    if validation_only:
        # Correctness needs one complete manifest-length execution. Performance
        # flows still use their separately timed repeated executions below.
        result = parse_result(run.log, ctx.test.sim_marker)
        if result.cycles != cycles:
            raise FlowError(f"simulation ran {result.cycles} cycles, expected {cycles}")
        return {"sim": {"backend": backend, "build_jobs": build_jobs,
                        "cycles": result.cycles, "checksum": result.checksum,
                        "validation_only": True}}

    drv = ctx.work / "SW" / "sim" / "drv.bin"
    if not drv.exists():
        raise FlowError(f"lhd sim built no driver at {drv}")
    # `sim.init_zero=true` configures the driver invocation performed inside
    # `lhd sim --run-only`; it is not baked into drv.bin.  The separately timed
    # execution must carry the equivalent runtime switch or it silently falls
    # back to seeded-random storage initialization and no longer matches the
    # Verilator `--x-initial 0` contract above.
    best, samples = ctx.run_best("exec", [drv, f"+cycles={cycles}", "--init-zero"])

    # cc = the whole --run-only minus the simulation it also paid for. Clamped
    # at zero: a negative value is only reachable if a stall landed in the lhd
    # run and not in the re-run, and a negative compile time is worse than a
    # zero one.
    cc_ms = max(run.ms - best.ms, 0)
    ctx.stage.time_ms["cc"] = cc_ms
    ctx.stage.time_ms.pop("run", None)

    result = parse_result(best.log, ctx.test.sim_marker)
    if result.cycles != cycles:
        raise FlowError(f"simulation ran {result.cycles} cycles, expected {cycles}")
    return {
        "sim": {
            "backend": backend,
            "build_jobs": build_jobs,
            "cycles": cycles,
            "exec_ms": best.ms,
            "exec_samples_ms": samples,
            "cycles_per_s": int(cycles / (best.ms / 1000)) if best.ms else 0,
            "checksum": result.checksum,
        }
    }
