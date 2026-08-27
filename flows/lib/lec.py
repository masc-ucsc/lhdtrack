"""The LEC contract shared by both equivalence backends.

Both flows prove the SAME obligation -- `pyrope/` is equivalent to `verilog/` --
and differ only in the engine. That is what makes their times comparable and
their verdicts checkable against each other:

    lec_lhd       --set formal.solver=cvc5      in-process SMT over the LGraph
    lec_lgyosys   --set formal.solver=lgyosys   inou/yosys/lgcheck, the former
                                                `lhd check`, over the emitted
                                                Verilog

THE BACKENDS CAN LEGITIMATELY DISAGREE, and that is the point of running both.
cvc5 reasons on the LGraph while lgyosys reasons on the cgen-emitted Verilog, so
a code-generation bug shows up as one backend proving what the other refutes --
a discrepancy no single-engine run could ever surface.

A VERDICT IS THREE-STATE, NOT TWO. `proven` and `refuted` are answers;
`timeout`, `unsupported` and `error` are the absence of one. Collapsing the
third state into "fail" would make a solver that gave up look like a design
that is wrong, and collapsing it into "pass" would be far worse.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lhdtrack.context import FlowContext, FlowSkip

# lhd's own `error.class` -> our verdict. Read from the RESULT JSON, never from
# the log: the log echoes "timeout=300s" in an info line, which made every run
# classify as `timeout` in ten milliseconds -- including ones that had proven
# their obligation. Keyword-matching a log is guessing; the result object is the
# tool telling you.
_CLASS_TO_VERDICT = {
    "equiv_fail": "refuted",
    "timeout": "timeout",
    "resource": "timeout",
    "unsupported": "unsupported",
}


def classify(result: dict | None, rc: int) -> str:
    """proven | refuted | timeout | unsupported | error.

    Three-state on purpose. `proven` and `refuted` are answers; the rest are the
    absence of one. Collapsing a timeout into "fail" would report a design as
    wrong when the honest answer is that the solver ran out of time -- and
    collapsing it into "pass" would be far worse.
    """
    if result is None:
        # NO RESULT JSON IS NOT A PROOF. `rc == 0` only says the process exited
        # cleanly; treating that as `proven` turns a tool that never wrote its
        # result object -- an older lhd, a full disk, a truncated write -- into
        # an equivalence proof, and run.py then writes that "verdict" back into
        # the manifest and lets the test into the headline geomean.
        return "error"
    if result.get("status") == "pass":
        return "proven"
    err = result.get("error") or {}
    cls = str(err.get("class", "")).lower()
    if cls in _CLASS_TO_VERDICT:
        return _CLASS_TO_VERDICT[cls]
    # A solver that gave up sometimes reports its own class; fall back to the
    # message, but only for the words that can only mean giving up.
    if re.search(r"\btimed?\s?out\b|time limit|resource limit", str(err.get("message", "")), re.I):
        return "timeout"
    return "error"


def counterexample(result: dict | None) -> str:
    """The first divergence, when there is one -- the whole point of a refutation."""
    hint = ((result or {}).get("error") or {}).get("hint", "")
    m = re.search(r"counterexample:\s*(.+)", hint)
    return (m.group(1) if m else hint)[:300]


def _why(measured) -> str:
    """The tool's own error message, from its JSON diagnostics."""
    for line in measured.log.read_text(errors="replace").splitlines():
        line = line.strip()
        if line.startswith("{") and '"message"' in line:
            try:
                doc = json.loads(line)
            except json.JSONDecodeError:
                continue
            msg = (doc.get("error") or {}).get("message") or doc.get("message")
            if msg:
                return str(msg)[:120]
    return f"exit {measured.rc}"


def run_lec(ctx: FlowContext, solver: str, timeout_s: int) -> dict:
    """Prove pyrope == verilog with one backend. Returns the `lec` block."""
    lhd = ctx.tool("lhd")
    if ctx.test.pyrope_status == "none":
        raise FlowSkip("test has no Pyrope side yet")
    if not ctx.test.pyrope_top.exists():
        raise FlowSkip(f"no pyrope source: {ctx.test.pyrope_top.name}")

    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]

    # Both sides are elaborated the same way for both backends, so the timing
    # difference below is the SOLVER's, not the front end's.
    ref = ctx.run(
        "elab_ref",
        [lhd, "compile", "verilog", "--top", ctx.top, "--emit-dir", "lg:ref",
         "--workdir", "rw", "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS", *params],
        check=False,
    )
    if not ref.ok:
        # lhd's slang reader could not elaborate the reference. Same class as
        # yosys failing to parse: a tool limitation, reported as a skip with the
        # tool's own words rather than as a red row every night.
        raise FlowSkip(f"lhd cannot elaborate the Verilog reference: {_why(ref)}")
    ctx.run(
        "elab_impl",
        [lhd, "compile", str(ctx.test.pyrope_top), "--top", ctx.top,
         "--emit-dir", "lg:impl", "--workdir", "iw"],
    )

    result_json = ctx.work / f"lec_{solver}.json"
    m = ctx.run(
        "lec",
        [
            lhd, "lec", "--impl", "lg:impl", "--ref", "lg:ref",
            "--top", f"{ctx.top}.{ctx.top}", "--workdir", "LW",
            "--set", f"formal.solver={solver}",
            "--set", f"formal.timeout={timeout_s}",
            "--result-json", str(result_json),
        ],
        check=False,
    )
    result = None
    if result_json.exists():
        try:
            result = json.loads(result_json.read_text())
        except (OSError, json.JSONDecodeError):
            result = None
    verdict = classify(result, m.rc)

    block = {
        "verdict": verdict,
        "solver": solver,
        # Named so the split gate can group by it. `lec_netlist` proves a
        # DIFFERENT thing (rtl-vs-netlist), and comparing verdicts across the
        # two would flag "pyrope is wrong, synthesis is fine" as a backend
        # disagreement -- which is not a disagreement, it is two answers to two
        # questions.
        "obligation": "pyrope-vs-verilog",
        "declared": ctx.test.lec_status,
        "ms": m.ms,
        "timeout_s": timeout_s,
    }
    if verdict == "refuted":
        # Carry the first divergence: "this is wrong" is not actionable,
        # "out_stages(ref=0 impl=254) at step 1" is.
        block["counterexample"] = counterexample(result)

    return {
        "lec": block,
        # A manifest claiming `proven` while the prover disagrees would let an
        # unverified test into the headline geomean. run.py fails the test on
        # this -- but only for a real answer, never for a timeout.
        "lec_drift": (
            verdict in ("proven", "refuted")
            and ctx.test.lec_status not in ("none", verdict)
        ),
    }
