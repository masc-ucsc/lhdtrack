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
from lib.lhd_synth_policy import abc_settings

NAME = "syn_lhd_verilog"
KIND = "synth"
NEEDS = ("lhd", "yosys")
OPTIONAL = ("sta",)
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    lhd = ctx.tool("lhd")
    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]
    # TELL lhd WHICH LIBRARY. `synth.liberty` -- the ONE Liberty pass.abc,
    # pass.opentimer and `lhd synth` all read (the former per-pass
    # `pass.abc.library` was removed) -- defaults to
    # $HAGENT_TECH_DIR/sky130_..., so without this every technology would
    # silently map to sky130 and the ASAP7 rows would be sky130 wearing an
    # ASAP7 label. The staged ASAP7 Liberty is merged ahead of time because
    # ABC's read_lib takes one file.
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"lhd's synth.liberty takes a single Liberty file, but "
            f"{ctx.tech.name} staged {len(ctx.liberty)} Liberty files; "
            "merge the technology before running LiveHD"
        )
    settings = [
        "--set", f"synth.liberty={ctx.liberty[0]}",
        "--set", f"pass.abc.delay={ctx.abc_delay_ps()}",
        *abc_settings(ctx.tech.name),
    ]
    lhd = ctx.tool("lhd")
    top = f"{ctx.top}.{ctx.top}"
    ctx.run(
        "synth",
        [lhd, "synth", "--reader", "slang", "--top", ctx.top,
         "--emit-dir", "lg:netlist", "--workdir", "W",
         "--result-json", "synth.json", "--stats", *settings,
         "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS", "-DBR_PPA_SYNTHESIS", *params],
        timeout=ctx.lec_timeout_s * 2 + 60,
    )
    # Both languages use the fused flow and the compiler's threading defaults.
    from syn_lhd_pyrope import _phases

    phases = _phases(ctx)
    if phases:
        del ctx.stage.time_ms["synth"]
        rss = ctx.stage.peak_rss_kb.pop("synth", 0)
        ctx.stage.time_ms.update(phases)
        ctx.stage.peak_rss_kb["map"] = rss

    # There is no `pass cgen`. A gate-level Verilog emission comes from feeding
    # the mapped lg: library back through `lhd compile` with an
    # `--emit-dir verilog:` -- lg: directories are valid compile INPUTS.
    #
    # This emission is what lets OpenSTA time the SAME netlist LiveHD's own
    # OpenTimer times. Without it the two engines would be timing two different
    # things and the correlation column would mean nothing.
    ctx.run(
        "emit",
        # The graph is already technology-mapped, so this is only the typed
        # LG->Verilog emitter. `--recipe O0` used to pin that; recipes were
        # removed from the CLI (compile now always runs cprop + bitwidth), and
        # passing one is a hard usage error.
        [lhd, "compile", "lg:netlist", "--top", top,
         "--emit-dir", "verilog:netv", "--workdir", "Wemit"],
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
                     qor_json=ctx.work / "W" / "synth" / "qor.json")}
