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

A VERDICT IS NOT BOOLEAN. `proven` and `refuted` are answers; `timeout`,
`inconclusive`, `unsupported` and `error` are the absence of one. A fast
INCONCLUSIVE result is distinct from a timeout: the former exhausted the
backend's proof strategies, while the latter exhausted its time budget.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError, FlowSkip


def check_elaboration_internal_error(result_path: Path) -> None:
    """A compiler defect is a failed measurement, not a missing capability."""
    try:
        result = json.loads(result_path.read_text())
    except (OSError, json.JSONDecodeError):
        return
    error = result.get("error") or {}
    if error.get("class") == "internal":
        raise FlowError(f"Verilog elaboration failed: {error.get('message', 'internal compiler error')}")

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


def classify(
    result: dict | None,
    rc: int,
    *,
    elapsed_ms: int | None = None,
    timeout_s: int | None = None,
    solver: str | None = None,
) -> str:
    """proven | refuted | timeout | inconclusive | unsupported | error.

    Three-state on purpose. `proven` and `refuted` are answers; the rest are the
    absence of one. Collapsing a timeout into "fail" would report a design as
    wrong when the honest answer is that the solver ran out of time -- and
    collapsing it into "pass" would be far worse.

    THE VERDICT IS NOT THE EXIT STATUS. `lhd lec` deliberately exits 0 on an
    INCONCLUSIVE comparison -- one engine giving up must not fail a pair -- so
    `status == "pass"` covers both a real proof and "the solver could not
    decide". Reading only `status` recorded lgyosys's INCONCLUSIVE on
    br_tracker_linked_list_ctrl as `proven`, i.e. exactly the "far worse"
    collapse above, and let an unverified design into a headline comparison.
    The envelope now carries the proof itself in `lec.verdict`; prefer it, and
    fall back to `status` only for an lhd old enough not to emit it.
    """
    if result is None:
        # NO RESULT JSON IS NOT A PROOF. `rc == 0` only says the process exited
        # cleanly; treating that as `proven` turns a tool that never wrote its
        # result object -- an older lhd, a full disk, a truncated write -- into
        # an equivalence proof, and run.py then writes that "verdict" back into
        # the manifest and lets the test into the headline geomean.
        return "error"
    lec = result.get("lec") or {}
    verdict = lec.get("verdict")
    if solver == "lgyosys":
        crosscheck = lec.get("crosscheck") or {}
        err = result.get("error") or {}
        message = str(err.get("message", ""))
        if crosscheck:
            verdict = crosscheck.get("verdict", "unknown")
            if verdict == "unknown" and crosscheck.get("exit_code") != 2:
                return "error"
        elif message.startswith("lgcheck cross-check did not decide equivalence"):
            # Older current builds retain the native proof in lec.verdict even
            # when the independent lgcheck comparison gives up.
            verdict = "unknown"
        elif message.startswith("lec engine and lgcheck DISAGREE"):
            if "lgcheck=different" in message:
                return "refuted"
            if "lgcheck=equivalent" in message:
                return "proven"
            return "error"
        elif result.get("status") != "pass" and verdict == "proven":
            # Emission/setup failed before an independent answer was available.
            return "error"
    if verdict in ("proven", "refuted"):
        return verdict
    if verdict == "unknown":
        # UNKNOWN is the solver-level envelope, but a named frontend/encoder
        # refusal is more specific and must survive classification.  The old
        # order returned `inconclusive` before consulting error.class, so
        # br_amba_axi_demux's explicit `unsupported` word-level combinational
        # cycle was misleadingly reported first as a timeout and then as a
        # generic give-up. Raising the timeout cannot help a refusal.
        err = result.get("error") or {}
        cls = str(err.get("class", "")).lower()
        msg = str(err.get("message", ""))
        # LiveHD uses process error.class=unsupported for BOTH fundamentally
        # different exit-7 cases:
        #
        #   * "lec REFUSED ... encoder does not model" -- nothing was encoded;
        #     more time cannot help, so this really is unsupported.
        #   * "lec could not decide equivalence" -- the miter ran but an
        #     incomplete state correspondence or solver budget prevented a
        #     verdict. A changed hierarchy commonly lands here after collapse;
        #     it is inconclusive/timeout, never a semantic unsupported feature.
        #
        # Consult that explicit message before the coarse process class. This
        # preserves the refusal distinction while preventing a hierarchy-only
        # mismatch from being rendered as a failed/unsupported design.
        undecided = msg.startswith((
            "lec could not decide equivalence", "lgcheck cross-check did not decide equivalence",
        ))
        budget_ms = None
        if timeout_s is not None:
            budget_ms = timeout_s * 1000 * (2 if solver == "lgyosys" else 1)
        if undecided:
            if budget_ms is not None and elapsed_ms is not None and elapsed_ms >= budget_ms * 0.98:
                return "timeout"
            return "inconclusive"
        if cls in _CLASS_TO_VERDICT:
            return _CLASS_TO_VERDICT[cls]
        # UNKNOWN does not itself mean TIMEOUT. In particular, lgcheck can run
        # all of its structural/inductive/BMC strategies and return
        # INCONCLUSIVE in one second. The old classifier called every such run
        # a timeout, producing rows such as "timeout, 1.10 s". Only call an
        # internally returned UNKNOWN a timeout when its elapsed time reaches
        # the solver budget. lgyosys currently gives its subprocess twice the
        # configured in-process budget; the outer watchdog adds 60 s of
        # emission/termination headroom.
        if budget_ms is not None and elapsed_ms is not None and elapsed_ms >= budget_ms * 0.98:
            return "timeout"
        return "inconclusive"
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
    wall = timeout_s * 2 + 60

    def elaboration_timeout(measured, side: str) -> dict:
        return {
            "lec": {
                "verdict": "timeout",
                "bounded": False,
                "solver": solver,
                "obligation": "pyrope-vs-verilog",
                "declared": ctx.test.lec_status,
                "ms": measured.ms,
                "timeout_s": timeout_s,
                "wall_limit_s": wall,
                "reason": f"{side} elaboration timed out before equivalence checking",
            },
            "lec_drift": False,
        }

    # Both sides are elaborated the same way for both backends, so the timing
    # difference below is the SOLVER's, not the front end's.
    ref = ctx.run(
        "elab_ref",
        [lhd, "compile", "verilog", "--top", ctx.top, "--emit-dir", "lg:ref",
         "--workdir", "rw", "--result-json", str(ctx.work / "elab_ref.json"),
         "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS",
         "-DBR_PPA_SYNTHESIS", *params],
        check=False,
        timeout=wall,
    )
    if ref.timed_out:
        return elaboration_timeout(ref, "Verilog")
    if not ref.ok:
        check_elaboration_internal_error(ctx.work / "elab_ref.json")
        # Unsupported inputs retain the reader's explicit capability reason.
        raise FlowSkip(f"lhd cannot elaborate the Verilog reference: {_why(ref)}")
    impl = ctx.run(
        "elab_impl",
        [lhd, "compile", str(ctx.test.pyrope_top), "--top", ctx.top,
         "--emit-dir", "lg:impl", "--workdir", "iw"],
        check=False,
        timeout=wall,
    )
    if impl.timed_out:
        return elaboration_timeout(impl, "Pyrope")
    if not impl.ok:
        from lhdtrack.context import FlowError

        raise FlowError(f"elab_impl exited {impl.rc}\n{impl.tail()}")

    result_json = ctx.work / f"lec_{solver}.json"
    # TWO BUDGETS, because one of them is not enforced. `formal.timeout` is the
    # SOLVER's, and the lgyosys backend does not honour it: it emits both sides
    # as Verilog, shells out to yosys, and waits -- measured still running at
    # 400s against a 300s setting on the shared-FIFO blocks, where cvc5 proves
    # in under a second. Without a wall-clock watchdog the nightly has no bound
    # at all, which is the one thing lhdtrack.toml's `lec_timeout_s` comment
    # says must not happen. The headroom covers the emission and the process
    # start; a solver inside its own budget never reaches it.
    m = ctx.run(
        "lec",
        [
            lhd, "lec", "--impl", "lg:impl", "--ref", "lg:ref",
            "--top", f"{ctx.top}.{ctx.top}", "--workdir", "LW",
            *([] if solver == "cvc5" else ["--set", f"formal.solver={solver}"]),
            "--set", f"formal.timeout={timeout_s}",
            "--result-json", str(result_json),
        ],
        check=False,
        timeout=wall,
    )
    result = None
    if result_json.exists():
        try:
            result = json.loads(result_json.read_text())
        except (OSError, json.JSONDecodeError):
            result = None
    # A KILLED SOLVER IS A TIMEOUT, NOT AN ERROR. It wrote no result object, and
    # `classify` reads that -- correctly -- as "no proof"; but "error" says the
    # run was broken, when what happened is that the backend was still working
    # when the watchdog fired.
    verdict = "timeout" if m.timed_out else classify(
        result,
        m.rc,
        elapsed_ms=m.ms,
        timeout_s=timeout_s,
        solver=solver,
    )

    # A BOUNDED pass is not an unconditional proof. `lhd lec` answers PASS(n) --
    # "equivalent for n cycles from reset, exhaustive over inputs, deeper cycles
    # not checked" -- and that is a real, complete answer to a SCOPED question,
    # so `verdict` stays `proven`. But it is NOT the same claim as an inductive
    # PROVEN, and the difference is not academic: measured 2026-08-28 on
    # br_fifo_shared_pop_ctrl, cvc5 answered PASS(6) on a pair that lgyosys
    # REFUTED with a counterexample deeper than cycle 6. Recording only the word
    # `proven` hid a genuine refutation. Carry the depth so the report -- and
    # anyone reading a headline geomean -- can see which proofs are bounded.
    lec_block = (result or {}).get("lec") or {}
    if solver == "lgyosys":
        lec_block = lec_block.get("crosscheck") or lec_block
    block = {
        "verdict": verdict,
        "bounded": bool(lec_block.get("bounded")),
        "bound": lec_block.get("bound"),
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
        "wall_limit_s": wall,
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
