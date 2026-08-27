"""LEC of the RTL against its own synthesized netlist.

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

from pathlib import Path

from lhdtrack.context import FlowContext, FlowError, FlowSkip
from lib.lec import classify, counterexample

NAME = "lec_netlist"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    import json

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
         "--workdir", "rw", "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS", *params],
        check=False,
    )
    if not ref.ok:
        from lib.lec import _why

        raise FlowSkip(f"lhd cannot elaborate the Verilog reference: {_why(ref)}")

    # 2. The implementation: that same design, synthesized and tech-mapped.
    ctx.run("color", [lhd, "pass", "color", "synth", "--top", top, "lg:ref", "--workdir", "W"])
    ctx.run(
        "map",
        [lhd, "pass", "abc", "--top", top, "lg:ref", "--emit-dir", "lg:netlist",
         "--workdir", "W", "--set", f"pass.abc.library={ctx.liberty[0]}"],
    )

    # 3. Behavioural models for the mapped cells. Without them every standard
    #    cell is an opaque Sub and the proof degrades to UNKNOWN -- which would
    #    look like a solver limitation rather than a missing input.
    ctx.run(
        "models",
        [lhd, "pass", "liberty", "gensim", str(ctx.liberty[0]),
         "--emit-dir", "lg:models", "--workdir", "Wm"],
    )

    result_json = ctx.work / "lec_netlist.json"
    # The same wall-clock watchdog lib/lec.py explains: `formal.timeout` is the
    # solver's own budget and is not, on every backend, a promise that the
    # process returns. This obligation is over a MAPPED netlist -- thousands of
    # cells against the RTL -- so it is the likeliest of the three to run long.
    wall = ctx.lec_timeout_s * 2 + 60
    m = ctx.run(
        "lec",
        [lhd, "lec", "--impl", "lg:netlist", "--ref", "lg:ref", "--lib", "lg:models",
         "--top", top, "--workdir", "LW",
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
    verdict = "timeout" if m.timed_out else classify(result, m.rc)

    block = {
        "verdict": verdict,
        "solver": "cvc5",
        # A DIFFERENT obligation from the pyrope-vs-verilog pair, so it must not
        # be averaged in with them or checked against their verdicts.
        "obligation": "rtl-vs-netlist",
        "declared": "none",
        "ms": m.ms,
        "timeout_s": ctx.lec_timeout_s,
        "wall_limit_s": wall,
    }
    if verdict == "refuted":
        # A refutation here means SYNTHESIS BROKE THE DESIGN -- far more serious
        # than the two source descriptions differing.
        block["counterexample"] = counterexample(result)
    return {"lec": block, "lec_drift": False}
