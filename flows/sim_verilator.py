"""BASELINE simulation: verilator.

Split into the same three legs as `lhd sim`, under the same metric names, so the
rows read against each other stage for stage:

    setup_ms   verilator --cc --exe        front end + C++ emission
    cc_ms      make -f V<top>.mk -j        host C++ compile + link
    exec_ms    the built binary, re-run    the simulation ALONE, best-of-N

WHAT IS HELD EQUAL to the LiveHD side (this is the whole premise):
  * the SOURCE   -- same filelist, same -DSYNTHESIS. Both simulators read the
                    same RTL, not one of them reading a re-emission of it.
  * the HARNESS  -- both drive sim/<top>_tb*, generated as a matched pair from
                    one port list, so identical work goes into both.
  * the GATE     -- both must print the same checksum.
  * NO TRACING   -- no --trace here, sim.vcd=false there. A VCD writer inside
                    the measured interval turns a simulation benchmark into a
                    filesystem benchmark.

What is NOT equalized is verilator's optimization level: it runs as a user would
out of the box, not tuned for this benchmark.

CACHEABLE -- including its compile time, which only moves when verilator or the
test moves, both of which are in the key.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext, FlowError, FlowSkip

NAME = "sim_verilator"
KIND = "sim"
NEEDS = ("verilator",)
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    tb = ctx.test.sim_dir / f"{ctx.top}_tb_verilator.cpp"
    if not tb.exists():
        raise FlowSkip(f"no verilator testbench: {tb.name} (run `lhdtrack import seed`)")

    cycles = ctx.test.sim_cycles
    vobj = ctx.work / "vobj"
    harness = ctx.test.sim_dir / f"{ctx.top}_harness.sv"
    sim_top = f"{ctx.top}_harness" if harness.exists() else ctx.top

    # 1. verilate. --cc --exe rather than --build, so the C++ compile is a
    #    separately timed step exactly as --setup-only / --run-only splits it.
    #    -Wno-fatal: these are third-party designs and verilator's lint is not
    #    the thing under test. -I<verilog dir> because verilator only searches
    #    its -I list while slang looks relative to the including file, and the
    #    two front ends must see the same files for the comparison to mean
    #    anything.
    # NO -G HERE. The harness instantiates the DUT with this config's parameters
    # already baked in (gen_testbench writes `add #(.BW(8)) dut`), and the
    # harness top itself declares none -- verilator rejects a -G naming a
    # parameter the top does not have.
    params = [] if harness.exists() else [
        f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())
    ]
    setup = [
        ctx.tool("verilator"), "--cc", "--exe", "--Mdir", str(vobj),
        "--top-module", sim_top, "-Wno-fatal", "-DSYNTHESIS",
        "-I" + str(ctx.test.verilog_dir),
        *params,
        "-F", str(ctx.test.filelist),
    ]
    if harness.exists():
        setup.append(str(harness))
    setup.append(str(tb))
    ctx.run("setup", setup)

    # 2. host C++ compile + link
    import os

    jobs = str(os.cpu_count() or 4)
    ctx.run("cc", ["make", "-C", str(vobj), "-f", f"V{sim_top}.mk", "-j", jobs, f"V{sim_top}"])

    # 3. the simulation alone, best-of-N
    binary = vobj / f"V{sim_top}"
    if not binary.exists():
        raise FlowError(f"verilator built no binary at {binary}")
    best, samples = ctx.run_best("exec", [binary, "--cycles", cycles])

    from lib.simgate import parse_result

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
