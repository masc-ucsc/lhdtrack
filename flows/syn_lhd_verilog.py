"""LiveHD synthesis, VERILOG in.

The middle row of the three-flow rule, and the one that makes the other two
readable:

    syn_lhd_verilog vs syn_lhd_pyrope   -> Pyrope vs Verilog as a LANGUAGE
                                           (identical backend)
    syn_lhd_verilog vs syn_yosys_abc    -> LiveHD vs yosys+slang/abc as a TOOL
                                           (identical source)

Without it every daily movement is confounded and nothing can be attributed.

NOT CACHEABLE: the lhd SHA moves daily, so the key would always miss anyway --
and re-measuring LiveHD is the entire point.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext, FlowError, FlowSkip

NAME = "syn_lhd_verilog"
KIND = "synth"
NEEDS = ("lhd", "yosys")
OPTIONAL = ("sta",)
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    lhd = ctx.tool("lhd")
    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]
    # TELL lhd WHICH LIBRARY. `pass.abc.library` defaults to
    # $HAGENT_TECH_DIR/sky130_..., so without this every technology would
    # silently map to sky130 and the ASAP7 rows would be sky130 wearing an
    # ASAP7 label. The staged ASAP7 Liberty is merged ahead of time because
    # ABC's read_lib takes one file.
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"lhd's pass.abc.library takes a single Liberty file, but "
            f"{ctx.tech.name} staged {len(ctx.liberty)} Liberty files; "
            "merge the technology before running LiveHD"
        )
    lib_args = [
        "--set", f"pass.abc.library={ctx.liberty[0]}",
        "--set", f"pass.abc.delay={ctx.abc_delay_ps()}",
        # QoR is a whole-design comparison. Keep source hierarchy for compile
        # and LEC, but let ABC optimize paths that cross module boundaries just
        # as the Yosys baseline does.
        "--set", "pass.abc.flatten=true",
        # A STRUCTURAL NETLIST, LIKE THE BASELINE'S. Bit-blast memories into
        # DFF cells plus mux logic, and never keep a region's flops native.
        # The yosys baseline maps every flop, so whatever lhd leaves
        # behavioural is mapped later by qor_endpoint's normalization -- yosys's
        # own dfflibmap+abc, with no -D -- and then measured as lhd's result.
        # Measured 2026-09-01 on ASAP7: br_ram_flops (5808 register bits, over
        # the 4096 default of register_max_bits) read 906 ps with its flops
        # kept native against 360 ps mapped here (yosys 1292 ps); memory=true
        # alone moved the 25 memory tests from 11/25 to 22/25 faster than yosys.
        "--set", "pass.abc.memory=true",
        "--set", "pass.abc.register_max_bits=0",
    ]

    # 1. Verilog -> lgraph, through slang. One filelist read, same sources and
    #    same -DSYNTHESIS the verilator side gets, so both front ends see
    #    identical RTL rather than one reading a re-emission of it.
    ctx.run(
        "elab",
        [
            lhd, "compile", "verilog", "--top", ctx.top,
            "--emit-dir", "lg:design", "--workdir", "cw",
            "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS", "-DBR_PPA_SYNTHESIS", *params,
        ],
    )
    return _synthesize(ctx, "design", lib_args)


def _synthesize(ctx: FlowContext, lgraph: str, lib_args: list) -> dict:
    """color -> abc -> emit, shared with syn_lhd_pyrope's manual path."""
    lhd = ctx.tool("lhd")
    top = f"{ctx.top}.{ctx.top}"
    map_wall = ctx.lec_timeout_s * 2 + 60

    ctx.run("color", [lhd, "pass", "color", "synth", "--top", top, f"lg:{lgraph}", "--workdir", "W"])
    ctx.run(
        "map",
        [
            lhd, "pass", "abc", "--top", top, f"lg:{lgraph}",
            "--emit-dir", "lg:netlist", "--workdir", "W", "--result-json", "abc.json",
            *lib_args,
        ],
        timeout=map_wall,
    )
    # There is no `pass cgen`. A gate-level Verilog emission comes from feeding
    # the mapped lg: library back through `lhd compile` with an
    # `--emit-dir verilog:` -- lg: directories are valid compile INPUTS.
    #
    # This emission is what lets OpenSTA time the SAME netlist LiveHD's own
    # OpenTimer times. Without it the two engines would be timing two different
    # things and the correlation column would mean nothing.
    ctx.run(
        "emit",
        [lhd, "compile", "lg:netlist", "--top", top,
         # The graph is already technology-mapped. O0 is the typed LG->Verilog
         # emitter path; the default O1 would run source cprop over internal
         # mapped-region glue and can reject its deliberately boundary-sized
         # pins before emission.
         "--recipe", "O0", "--emit-dir", "verilog:netv", "--workdir", "Wemit"],
    )

    from qor_endpoint import emitted_verilog, evaluate

    try:
        netlist = emitted_verilog(ctx, ctx.work / "netv")
    except FlowError as error:
        raise FlowError(
            "lhd emitted no gate-level Verilog; OpenSTA cannot time the netlist "
            "(check `lhd pass cgen verilog` support for mapped designs)"
        ) from error

    return {"netlist": netlist, **evaluate(ctx, netlist, lgraph=ctx.work / "netlist",
                     qor_json=ctx.work / "W" / "qor.json")}
