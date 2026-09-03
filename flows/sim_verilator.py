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
  * INITIAL STATE -- both use zero for otherwise-unspecified state and unknown
                    literal bits.  The final checksum is the oracle; internal
                    delta-cycle scheduling and VCD transitions need not match.
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
    # NO -G HERE, AND NONE NEEDED ANYWHERE. Every top pins its parameter point
    # in the source (`localparam` in the port list; see tools/monomorphize.py),
    # so `chparams()` is empty and the harness instantiates the DUT bare. The
    # list is kept because verilator would reject a -G naming a parameter a top
    # does not have, which is exactly what a re-imported, still-parameterized
    # top would produce -- `lhdtrack check` fails that case first.
    params = [] if harness.exists() else [
        f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())
    ]
    setup = [
        ctx.tool("verilator"), "--cc", "--exe", "--Mdir", str(vobj),
        "--top-module", sim_top, "-Wno-fatal", "--x-initial", "0", "--x-assign", "0",
        "-DSYNTHESIS", "-DBR_PPA_SYNTHESIS",
        "-DBR_VERILATOR",
        "-I" + str(ctx.test.verilog_dir),
        *params,
        "-F", str(ctx.test.filelist),
    ]
    if harness.exists():
        setup.append(str(harness))
    setup.append(str(tb))
    ctx.run("setup", setup)

    # 2. host C++ compile + link
    # Each flow invocation is single-threaded by benchmark contract. The outer
    # runner already schedules independent jobs across cores; an inner
    # `-j $(nproc)` here oversubscribes the host and makes both wall time and
    # peak RSS depend on what the other three workers happen to be compiling.
    ctx.run("cc", ["make", "-C", str(vobj), "-f", f"V{sim_top}.mk", "-j", "1", f"V{sim_top}"])

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
