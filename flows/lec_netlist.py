"""Three netlist-preservation checks over one LiveHD-mapped design.

A HARDER AND DIFFERENT OBLIGATION from `lec_lhd`/`lec_lgyosys`. Those compare
two SOURCES that a human wrote to describe the same design, so the two sides
look alike and a structural match often settles it. This compares behavioural
RTL against a flat sea of mapped standard cells: no shared module boundaries,
no shared signal names, registers turned into `sky130_fd_sc_hd__dfxtp_1`
instances, and combinational logic rewritten by ABC into a completely different
gate structure.

That makes it the check that actually exercises the equivalence engine, and it
validates something nothing else here does: **that synthesis preserved the
design**. Every area and delay number in the synthesis section is a claim about
a netlist; this is what says the netlist is still the circuit.

The mapped cells need behavioural models or they are opaque black boxes and the
proof degrades to UNKNOWN -- `lhd pass liberty gensim` generates them from the
same Liberty the netlist was mapped against.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext, FlowSkip
from lib.lec import classify, counterexample

NAME = "lec_netlist"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = True


def _check(
    ctx: FlowContext,
    *,
    impl: str,
    ref: str,
    models: str,
    solver: str,
    obligation: str,
    label: str,
) -> dict:
    import json

    lhd = ctx.tool("lhd")
    result_json = ctx.work / f"{label}.json"
    wall = ctx.lec_timeout_s * 2 + 60
    m = ctx.run(
        label,
        [lhd, "lec", "--impl", impl, "--ref", ref, "--lib", models,
         "--top", f"{ctx.top}.{ctx.top}", "--workdir", f"LW-{label}",
         "--set", f"formal.solver={solver}",
         "--set", f"formal.timeout={ctx.lec_timeout_s}",
         "--result-json", str(result_json)],
        check=False,
        timeout=wall,
    )
    result = None
    if result_json.exists():
        try:
            result = json.loads(result_json.read_text())
        except (OSError, json.JSONDecodeError):
            result = None
    verdict = "timeout" if m.timed_out else classify(
        result,
        m.rc,
        elapsed_ms=m.ms,
        timeout_s=ctx.lec_timeout_s,
        solver=solver,
    )
    block = {
        "verdict": verdict,
        "solver": solver,
        "obligation": obligation,
        "declared": "none",
        "ms": m.ms,
        "timeout_s": ctx.lec_timeout_s,
        "wall_limit_s": wall,
    }
    if verdict == "refuted":
        block["counterexample"] = counterexample(result)
    elif verdict in ("unsupported", "inconclusive", "error"):
        err = (result or {}).get("error") or {}
        reason = err.get("hint") or err.get("message")
        if reason:
            block["reason"] = str(reason)[:500]
    return block


def run(ctx: FlowContext) -> dict:
    lhd = ctx.tool("lhd")
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"lhd takes a single Liberty file, but {ctx.tech.name} ships "
            f"{len(ctx.liberty)} cell families"
        )
    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]
    top = f"{ctx.top}.{ctx.top}"

    # 1. The reference: the RTL as written.
    ref = ctx.run(
        "elab_ref",
        [lhd, "compile", "verilog", "--top", ctx.top, "--emit-dir", "lg:ref",
         "--workdir", "rw", "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS",
         "-DBR_PPA_SYNTHESIS", *params],
        check=False,
    )
    if not ref.ok:
        from lib.lec import _why

        raise FlowSkip(f"lhd cannot elaborate the Verilog reference: {_why(ref)}")

    # 2. The implementation: that same design, synthesized and tech-mapped.
    ctx.run("color", [lhd, "pass", "color", "synth", "--top", top, "lg:ref", "--workdir", "W"])
    map_wall = ctx.lec_timeout_s * 2 + 60
    mapped = ctx.run(
        "map",
        [lhd, "pass", "abc", "--top", top, "lg:ref", "--emit-dir", "lg:netlist",
         "--workdir", "W", "--set", f"pass.abc.library={ctx.liberty[0]}",
         "--set", "pass.abc.flatten=true"],
        check=False,
        timeout=map_wall,
    )
    if mapped.timed_out:
        # Mapping is a prerequisite of BOTH equivalence obligations.  It used
        # to have no wall bound, so a large FIFO could occupy one worker/core
        # forever before either solver watchdog even started. Preserve the
        # distinction between "no Pyrope" and "mapping ran out of budget".
        yosys_block = {
            "verdict": "timeout",
            "solver": "lgyosys",
            "obligation": "verilog-vs-netlist",
            "declared": "none",
            "ms": mapped.ms,
            "timeout_s": ctx.lec_timeout_s,
            "wall_limit_s": map_wall,
            "reason": "ABC mapping timed out before a netlist was available",
        }
        if ctx.test.pyrope_status == "none" or not ctx.test.pyrope_top.exists():
            pyrope_block = {
                "verdict": "unsupported",
                "solver": "cvc5",
                "obligation": "pyrope-vs-netlist",
                "declared": ctx.test.lec_status,
                "ms": 0,
                "reason": "test has no Pyrope side yet",
            }
        else:
            pyrope_block = {
                "verdict": "timeout",
                "solver": "cvc5",
                "obligation": "pyrope-vs-netlist",
                "declared": ctx.test.lec_status,
                "ms": mapped.ms,
                "timeout_s": ctx.lec_timeout_s,
                "wall_limit_s": map_wall,
                "reason": "ABC mapping timed out before a netlist was available",
            }
        verilog_cvc5_block = {
            "verdict": "timeout",
            "solver": "cvc5",
            "obligation": "verilog-vs-netlist",
            "declared": "none",
            "ms": mapped.ms,
            "timeout_s": ctx.lec_timeout_s,
            "wall_limit_s": map_wall,
            "reason": "ABC mapping timed out before a netlist was available",
        }
        return {
            "lec": pyrope_block,
            "lec_aux": yosys_block,
            "lec_verilog": verilog_cvc5_block,
            "lec_drift": False,
        }
    if not mapped.ok:
        from lhdtrack.context import FlowError

        raise FlowError(f"map exited {mapped.rc}\n{mapped.tail()}")

    # 3. Behavioural models for the mapped cells. Without them every standard
    #    cell is an opaque Sub and the proof degrades to UNKNOWN -- which would
    #    look like a solver limitation rather than a missing input.
    ctx.run(
        "models",
        [lhd, "pass", "liberty", "gensim", str(ctx.liberty[0]),
         "--emit-dir", "lg:models", "--workdir", "Wm"],
    )

    # Yosys/lgcheck checks the original Verilog against the mapped netlist.
    yosys_block = _check(
        ctx,
        impl="lg:netlist",
        ref="lg:ref",
        models="lg:models",
        solver="lgyosys",
        obligation="verilog-vs-netlist",
        label="lec_yosys_verilog_netlist",
    )

    # The same Verilog-vs-netlist obligation through LiveHD's in-process cvc5
    # engine. This is the discriminator for a Yosys/lgcheck refutation: if cvc5
    # also refutes, synthesis is wrong; if cvc5 proves, the disagreement belongs
    # to the checker/backend rather than to the mapped circuit.
    verilog_cvc5_block = _check(
        ctx,
        impl="lg:netlist",
        ref="lg:ref",
        models="lg:models",
        solver="cvc5",
        obligation="verilog-vs-netlist",
        label="lec_lhd_verilog_netlist",
    )

    # LiveHD's in-process engine checks the hand-written Pyrope against that
    # same netlist.  Keep an explicit unsupported result when the corpus entry
    # has no Pyrope; the Yosys obligation above is still meaningful.
    if ctx.test.pyrope_status == "none" or not ctx.test.pyrope_top.exists():
        pyrope_block = {
            "verdict": "unsupported",
            "solver": "cvc5",
            "obligation": "pyrope-vs-netlist",
            "declared": ctx.test.lec_status,
            "ms": 0,
            "reason": "test has no Pyrope side yet",
        }
    else:
        ctx.run(
            "elab_pyrope_ref",
            [lhd, "compile", str(ctx.test.pyrope_top), "--top", ctx.top,
             "--emit-dir", "lg:pyref", "--workdir", "pw"],
        )
        pyrope_block = _check(
            ctx,
            impl="lg:netlist",
            ref="lg:pyref",
            models="lg:models",
            solver="cvc5",
            obligation="pyrope-vs-netlist",
            label="lec_lhd_pyrope_netlist",
        )

    return {
        "lec": pyrope_block,
        "lec_aux": yosys_block,
        "lec_verilog": verilog_cvc5_block,
        "lec_drift": False,
    }
