"""Prove the retained USYN Pyrope netlist against its compiled source graph (cvc5)."""
from __future__ import annotations

from lhdtrack.context import FlowContext, FlowSkip
from lec_netlist import _check
from lib.lhd_synth_policy import abc_settings, mapping_settings

NAME = "lec_netlist_pyrope_usyn"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = True
AFTER = ("syn_lhd_pyrope_usyn",)


def run(ctx: FlowContext) -> dict:
    lhd = ctx.tool("lhd")
    if ctx.test.pyrope_status == "none" or not ctx.test.pyrope_top.exists():
        return {"lec": {"verdict": "unsupported", "solver": "cvc5", "obligation": "pyrope-vs-netlist",
                        "declared": ctx.test.lec_status, "ms": 0, "reason": "test has no Pyrope side yet"},
                "lec_drift": False}
    work = ctx.work.parent / "syn_lhd_pyrope_usyn"
    net, ref = work / "netlist", work / "W/synth/lg"
    if not ((net / "library.txt").is_file() and ref.is_dir()):
        # The synthesis flow's workdir is gone (no --keep-work): map again with
        # the identical settings, so the verdict still covers the measured mapping.
        work = ctx.work / "pyrope-synth"
        net, ref = work / "netlist", work / "W/synth/lg"
        knobs = ["--set", "synth.mapper=usyn", "--set", f"synth.liberty={ctx.liberty[0]}",
                 "--set", f"abc.delay={ctx.abc_delay_ps()}",
                 *mapping_settings(ctx.tech.name, "usyn", str(ctx.abc_delay_ps())),
                 *abc_settings(ctx.tech.name)]
        if ctx.test.pyrope_binding == "set":
            knobs += [a for k, v in sorted(ctx.chparams().items()) for a in ("--set", f"compile.{k}={v}")]
        ctx.run("synth_pyrope",
                [lhd, "synth", str(ctx.test.pyrope_top), "--top", ctx.top, "--emit-dir", f"lg:{net}",
                 "--workdir", str(work / "W"), *knobs],
                timeout=ctx.lec_timeout_s * 2 + 60)
    ctx.run("models", [lhd, "pass", "liberty", "gensim", str(ctx.liberty[0]),
                       "--emit-dir", "lg:models", "--workdir", "Wm"])
    block = _check(ctx, impl=f"lg:{net}", ref=f"lg:{ref}", models="lg:models", solver="cvc5",
                   obligation="pyrope-vs-netlist", label="lec_lhd_pyrope_usyn_netlist")
    return {"lec": block, "lec_drift": False}
