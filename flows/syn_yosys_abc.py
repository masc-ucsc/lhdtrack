"""BASELINE synthesis: stock yosys `synth` + `abc -liberty`.

This is the reference every LiveHD synthesis number is quoted against, and it is
deliberately the DEFAULT pass -- the same shape circt-synth-tracker uses for its
yosys leg. No hand-tuned script, no per-test script, no ABC recipe chosen to
flatter or to punish. A baseline that has been tuned is not a baseline.

CACHEABLE. The result is reused until the test, the config, this file, the yosys
or abc version, the Liberty, or the host changes -- see keys.FLOW_TOOLS.
"""

from __future__ import annotations

import re
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError, FlowSkip

NAME = "syn_yosys_abc"
KIND = "synth"
NEEDS = ("yosys",)
OPTIONAL = ("abc", "sta")
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    netlist = ctx.work / "mapped.v"
    sources = " ".join(str(p) for p in ctx.verilog_sources())
    chparam = " ".join(f"-chparam {k} {v}" for k, v in sorted(ctx.chparams().items()))
    reads = "\n".join(f"read_liberty -lib {lib}" for lib in ctx.liberty)
    # EVERY Liberty file, not just the first. sky130 ships one file so the
    # distinction is invisible there; ASAP7 splits its cells across five
    # families, and `liberty[0]` is the AND-OR family -- no flops, no
    # inverters, so dfflibmap died with "D flip-flops are not supported".
    libs = " ".join(f"-liberty {lib}" for lib in ctx.liberty)

    # `synth` is yosys's own default script; the only additions are the ones
    # required to land on a real Liberty at all (dfflibmap for the sequential
    # cells, abc -liberty for the combinational ones).
    script = f"""
{reads}
read_verilog -sv {sources}
hierarchy -check -top {ctx.top} {chparam}
synth -top {ctx.top} -flatten
dfflibmap {libs}
abc {libs}
setundef -zero
splitnets
opt_clean -purge
write_verilog -noattr -noexpr {netlist}
"""
    ys = ctx.write("synth.ys", script)
    m = ctx.run("synth", [ctx.tool("yosys"), "-q", "-s", ys], check=False)
    if not m.ok:
        text = m.log.read_text(errors="replace")
        err = next((ln for ln in text.splitlines() if "ERROR" in ln), "")
        # yosys's SystemVerilog front end cannot read a large part of
        # bedrock-rtl (`br_math_pkg` defeats its parser outright; hierarchical
        # access inside a generate block defeats its width inference). That is a
        # property of the BASELINE TOOL, not of the test, and it will not change
        # -- so it is a skip with the parser's own words, not a red row every
        # night forever. The LiveHD rows still run and still report absolute QoR.
        if err:
            raise FlowSkip(
                "yosys cannot elaborate this design: "
                + re.sub(r"^.*?ERROR:\s*", "", err).strip()[:120]
            )
        raise FlowError(f"yosys synthesis failed\n{m.tail()}")

    if not netlist.exists():
        raise FlowError("yosys produced no mapped netlist")

    from qor_endpoint import evaluate

    # No lgraph: a yosys netlist has no LiveHD design behind it, so this row
    # carries OpenSTA only. The OpenTimer correlation lives on the LiveHD rows,
    # which is where a LiveHD timing bug would actually matter.
    return {"netlist": netlist, **evaluate(ctx, netlist, lgraph=None)}
