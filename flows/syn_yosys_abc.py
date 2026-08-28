"""BASELINE synthesis: yosys-slang frontend + stock yosys `synth` and ABC.

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
NEEDS = ("yosys", "yosys_slang")
OPTIONAL = ("abc", "sta")
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    abc_delay = ctx.abc_delay_ps()
    netlist = ctx.work / "mapped.v"
    slang_params = " ".join(f"-G{k}={v}" for k, v in sorted(ctx.chparams().items()))
    reads = "\n".join(f"read_liberty -lib {lib}" for lib in ctx.liberty)
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"yosys ABC mapping needs one complete Liberty, but {ctx.tech.name} "
            f"staged {len(ctx.liberty)} files"
        )
    liberty = ctx.liberty[0]

    # `synth` is yosys's own default script; the only additions are the ones
    # required to land on a real Liberty at all (dfflibmap for the sequential
    # cells, abc -liberty for the combinational ones).
    script = f"""
{reads}
read_slang --top {ctx.top} --no-proc -DSYNTHESIS -DBR_PPA_SYNTHESIS {slang_params} -F {ctx.test.filelist}
hierarchy -check -top {ctx.top}
synth -top {ctx.top} -flatten
dfflibmap -liberty {liberty}
abc -liberty {liberty} -D {abc_delay}
setundef -zero
splitnets
opt_clean -purge
write_verilog -noattr -noexpr {netlist}
"""
    ys = ctx.write("synth.ys", script)
    m = ctx.run(
        "synth",
        [ctx.tool("yosys"), "-m", ctx.tool("yosys_slang"), "-q", "-s", ys],
        check=False,
    )
    if not m.ok:
        text = m.log.read_text(errors="replace")
        err = next((ln for ln in text.splitlines() if "ERROR" in ln), "")
        # A frontend limitation is a property of the baseline tool, not of the
        # test, so preserve its own diagnostic as an explicit skip.
        if err:
            raise FlowSkip(
                "yosys+slang cannot elaborate this design: "
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
