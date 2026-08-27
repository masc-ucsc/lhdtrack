"""`lhd sim`, VERILOG in. The middle simulation row.

Compiles the Verilog straight to an lgraph library and simulates THAT. It does
NOT detour through `--emit-dir pyrope:` and simulate the re-emitted source:
that would measure the Pyrope front end a second time and put an emitter round
trip between the Verilog and the thing being simulated.

The three legs match sim_verilator's exactly -- setup / cc / exec -- because
`lhd sim --setup-only` writes the driver sources and `--run-only` does the host
C++ compile plus the simulation. Timing --run-only alone would report a clang
timing as if it were a simulator timing.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext, FlowSkip

NAME = "sim_lhd_verilog"
KIND = "sim"
NEEDS = ("lhd",)
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    from lib.simgate import run_lhd_sim

    tb = ctx.test.sim_dir / f"{ctx.top}_tb.prp"
    if not tb.exists():
        raise FlowSkip(f"no Pyrope testbench: {tb.name} (run `lhdtrack import seed`)")

    harness = ctx.test.sim_dir / f"{ctx.top}_harness.sv"
    sim_top = f"{ctx.top}_harness" if harness.exists() else ctx.top

    # NO -G HERE. The harness instantiates the DUT with this config's parameters
    # already baked in (gen_testbench writes `add #(.BW(8)) dut`), and the
    # harness top itself declares none -- verilator rejects a -G naming a
    # parameter the top does not have.
    params = [] if harness.exists() else [
        f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())
    ]
    sources = ["-F", str(ctx.test.filelist), "-DSYNTHESIS", *params]
    if harness.exists():
        sources.append(str(harness))

    ctx.run(
        "elab",
        [ctx.tool("lhd"), "compile", "verilog", "--top", sim_top,
         "--emit-dir", "lg:dut", "--workdir", "tw", "--", *sources],
    )
    return run_lhd_sim(ctx, design_input="lg:dut", tb=tb)
