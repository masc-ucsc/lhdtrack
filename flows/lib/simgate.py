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

import json
import os
import re
from dataclasses import dataclass
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError

_RESULT = re.compile(
    r"(?P<marker>[A-Z0-9-]*DONE)\s+cycles=(?P<cycles>\d+)\s+checksum=(?P<sum>\d+)"
)


CPROP_ENGINES = ("classic", "aegraph", "aegraph+classic")


def compile_sets(engine: str | None = None) -> list[str]:
    """`--set` flags that ride the COMPILE of the design under simulation.

    `lhd sim` compiles the design with the same compile.* knobs as `lhd compile`,
    so the constant-propagation engine of a simulation row is one `--set`.
    Selectors, in priority order:

      engine=...              explicit argument (a flow that IS the A/B variant)
      LHDTRACK_CPROP_ENGINE   classic | aegraph | aegraph+classic, for every sim flow
      LHDTRACK_SIM_SET        space separated key=value lhd settings, e.g.
                              "compile.cprop.engine=aegraph compile.cprop.engine_slots=first"

    Unset uses the compiler's own default, recorded from its result recipe. The
    sets ride BOTH `lhd sim` legs (--setup-only and --run-only), as the knobs
    must match for the second leg to reuse the first leg's objects.
    """
    engine = engine or os.environ.get("LHDTRACK_CPROP_ENGINE") or None
    out: list[str] = []
    if engine is not None:
        if engine not in CPROP_ENGINES:
            raise ValueError(f"cprop engine must be one of {CPROP_ENGINES}, got {engine!r}")
        out += ["--set", f"compile.cprop.engine={engine}"]
    for item in os.environ.get("LHDTRACK_SIM_SET", "").split():
        if "=" not in item:
            raise ValueError(f"LHDTRACK_SIM_SET entries must be key=value, got {item!r}")
        out += ["--set", item]
    return out


def cprop_engine_from_log(log: Path) -> str | None:
    """Read the effective engine from structured tool results, never an argv echo."""
    if not log.is_file():
        return None
    engine = None
    for line in log.read_text(errors="replace").splitlines():
        if line.startswith("$ "):
            continue
        try:
            result = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(result, dict) or result.get("tool") != "lhd":
            continue
        passes = {step.split()[0] for step in (result.get("recipe") or [])
                  if isinstance(step, str) and step.split()}
        actual = []
        if "pass.aegraph" in passes:
            actual.append("aegraph")
        if "pass.cprop" in passes:
            actual.append("classic")
        if actual:
            engine = "+".join(actual)
    return engine


def simulation_engine_metadata(work: Path, setup_log: Path, design_input: str) -> dict:
    """Keep source compilation separate from compilation of the Pyrope driver."""
    driver = cprop_engine_from_log(setup_log)
    source = (cprop_engine_from_log(work / "logs" / "elab.log")
              if design_input.startswith("lg:") else driver)
    return {"cprop_engine": source or "unknown", "driver_cprop_engine": driver or "unknown"}


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
    backend: str = "slop", direct_compile_timing: bool = False, engine: str | None = None,
) -> dict:
    """The `lhd sim` half of a simulation row: setup / cc / exec.

    THE TIME SPLIT MATTERS. `--setup-only` writes driver sources and, for LLVM,
    kernel objects. `--run-only` completes host compilation/linking, rebuilds
    drv.bin and runs it. The binary is re-run separately to measure simulation
    alone; subtracting its best execution from run-only estimates compile time.
    With direct_compile_timing, sim.compile_only stops run-only after linking;
    its measured time is compilation, with no execution subtraction.
    Compare setup + compile across backends because object generation straddles
    those stages differently. Verilog's separate elab stage also belongs in
    the total cost of preparing and executing one simulation.
    """
    lhd = ctx.tool("lhd")
    if backend not in ("slop", "llvm"):
        raise ValueError(f"unknown simulation backend: {backend}")
    if validation_only and direct_compile_timing:
        raise ValueError("direct compile timing requires standalone simulation measurements")
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
        "--set", f"lhd.jobs={build_jobs}",  # was sim.jobs (renamed)
        *compile_sets(engine),
    ]
    setup = ctx.run(
        "setup",
        [lhd, "sim", *inputs, "--setup-only", *sim_policy, "--workdir", "SW"],
    )
    run = ctx.run(
        "run",
        [lhd, "sim", *inputs, "--run-only", "--arg", f"cycles={cycles}",
         *sim_policy,
         *(["--set", "sim.compile_only=true"] if direct_compile_timing else []),
         "--diag-fmt", "pretty", "--workdir", "SW"],
    )

    if validation_only:
        # Correctness needs one complete manifest-length execution. Performance
        # flows still use their separately timed repeated executions below.
        result = parse_result(run.log, ctx.test.sim_marker)
        if result.cycles != cycles:
            raise FlowError(f"simulation ran {result.cycles} cycles, expected {cycles}")
        return {"sim": {"backend": backend, "build_jobs": build_jobs,
                        **simulation_engine_metadata(ctx.work, setup.log, design_input),
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
    cc_ms = run.ms if direct_compile_timing else max(run.ms - best.ms, 0)
    ctx.stage.time_ms["cc"] = cc_ms
    ctx.stage.time_ms.pop("run", None)

    result = parse_result(best.log, ctx.test.sim_marker)
    if result.cycles != cycles:
        raise FlowError(f"simulation ran {result.cycles} cycles, expected {cycles}")
    return {
        "sim": {
            "backend": backend,
            **simulation_engine_metadata(ctx.work, setup.log, design_input),
            "build_jobs": build_jobs,
            "compile_timing": "direct" if direct_compile_timing else "execution-subtracted",
            "cycles": cycles,
            "exec_ms": best.ms,
            "exec_samples_ms": samples,
            "cycles_per_s": int(cycles / (best.ms / 1000)) if best.ms else 0,
            "checksum": result.checksum,
        }
    }
