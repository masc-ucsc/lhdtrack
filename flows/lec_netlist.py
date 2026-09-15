"""Three netlist-preservation checks over one LiveHD-mapped design.

A HARDER AND DIFFERENT OBLIGATION from `lec_lhd`/`lec_lgyosys`. Those compare
two SOURCES that a human wrote to describe the same design, so the two sides
look alike and a structural match often settles it. This compares behavioural
RTL against a flat sea of mapped standard cells: no shared module boundaries,
no shared signal names, registers and memories turned into DFF cells
(`dfxtp_1` / `DFFHQx4`) plus mux logic, and combinational logic rewritten by
ABC into a completely different gate structure.

The netlist proved here is the SAME kind the synthesis rows measure: `lhd synth`
runs with the compiler defaults, the selected SAT profile, and the SDC-derived `delay`, so a verdict covers the netlist
whose area and delay the report quotes rather than a differently mapped sibling.

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
from lib.lec import check_elaboration_internal_error, classify, counterexample
from lib.lhd_synth_policy import abc_settings

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
    resource_json = result_json.with_suffix(".resource.json")
    resource_json.unlink(missing_ok=True)
    wall = ctx.lec_timeout_s * 2 + 60
    m = ctx.run(
        label,
        [lhd, "lec", "--impl", impl, "--ref", ref, "--lib", models,
         "--top", f"{ctx.top}.{ctx.top}", "--workdir", f"LW-{label}",
         *([] if solver == "cvc5" else ["--set", f"formal.solver={solver}"]),
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
    resource = None
    if m.rc in (-9, 137) and resource_json.exists():
        try:
            event = json.loads(resource_json.read_text())
            if (isinstance(event, dict) and event.get("schema_version") == 1
                    and event.get("origin") == "lhdtrack-run-supervisor"
                    and event.get("kind") == "memory_limit" and event.get("signal") == 9
                    and isinstance(event.get("reason"), str) and event["reason"]
                    and event.get("result_json") == str(result_json.resolve())):
                resource = event
        except (OSError, json.JSONDecodeError):
            pass
    verdict = "timeout" if m.timed_out or resource else classify(
        result,
        m.rc,
        elapsed_ms=m.ms,
        timeout_s=ctx.lec_timeout_s,
        solver=solver,
    )
    lec_block = (result or {}).get("lec") or {}
    if solver == "lgyosys":
        lec_block = lec_block.get("crosscheck") or lec_block
    block = {
        "verdict": verdict,
        "bounded": bool(lec_block.get("bounded")),
        "bound": lec_block.get("bound"),
        "solver": solver,
        "obligation": obligation,
        "declared": "none",
        "ms": m.ms,
        "timeout_s": ctx.lec_timeout_s,
        "wall_limit_s": wall,
    }
    if resource:
        block["reason"] = resource["reason"]
        block["resource_limit"] = resource
    elif verdict == "refuted":
        block["counterexample"] = counterexample(result)
    elif verdict in ("unsupported", "inconclusive", "error"):
        err = (result or {}).get("error") or {}
        reason = err.get("hint") or err.get("message")
        if reason:
            block["reason"] = str(reason)[:500]
    return block


def run(ctx: FlowContext) -> dict:
    # The proof must cover the same SDC-derived mapping policy as synthesis.
    # Without constraints the synthesis flows skip, so no measured mapping exists.
    ctx.require_sdc()
    lhd = ctx.tool("lhd")
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"lhd takes a single Liberty file, but {ctx.tech.name} ships "
            f"{len(ctx.liberty)} cell families"
        )
    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]
    top = f"{ctx.top}.{ctx.top}"

    # In a complete matrix, prove the exact retained synthesis artifacts.
    # Sibling workdirs belong to this run ID, so an older profile cannot leak in.
    verilog_work = ctx.work.parent / "syn_lhd_verilog"
    verilog_net = verilog_work / "netlist"
    verilog_ref = verilog_work / "W/synth/lg"
    if verilog_net.is_dir() and verilog_ref.is_dir():
        impl_input = f"lg:{verilog_net}"
        ref_input = f"lg:{verilog_ref}"
    else:
        # 1. The reference: the RTL as written.
        ref = ctx.run(
            "elab_ref",
            [lhd, "compile", "verilog", "--top", ctx.top, "--emit-dir", "lg:ref",
             "--workdir", "rw", "--result-json", str(ctx.work / "elab_ref.json"),
             "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS",
             "-DBR_PPA_SYNTHESIS", *params],
            check=False,
        )
        if not ref.ok:
            from lib.lec import _why

            check_elaboration_internal_error(ctx.work / "elab_ref.json")
            raise FlowSkip(f"lhd cannot elaborate the Verilog reference: {_why(ref)}")

        # Use the same fused synthesis defaults and profile as both QoR flows.
        knobs = [
            "--set", f"synth.liberty={ctx.liberty[0]}",
            "--set", f"pass.abc.delay={ctx.abc_delay_ps()}",
            *abc_settings(ctx.tech.name),
        ]
        map_wall = ctx.lec_timeout_s * 2 + 60
        mapped = ctx.run(
            "map",
            [lhd, "synth", "--top", top, "lg:ref", "--emit-dir", "lg:netlist",
             "--workdir", "W", *knobs],
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

        impl_input = "lg:netlist"
        ref_input = "lg:ref"

    # 3. Behavioural models for the mapped cells. Without them every standard
    #    cell is an opaque Sub and the proof degrades to UNKNOWN -- which would
    #    look like a solver limitation rather than a missing input.
    ctx.run(
        "models",
        [lhd, "pass", "liberty", "gensim", str(ctx.liberty[0]),
         "--emit-dir", "lg:models", "--workdir", "Wm"],
    )

    # The same Verilog-vs-netlist obligation through LiveHD's in-process cvc5
    # engine. This is the discriminator for a Yosys/lgcheck refutation: if cvc5
    # also refutes, synthesis is wrong; if cvc5 proves, the disagreement belongs
    # to the checker/backend rather than to the mapped circuit.
    verilog_cvc5_block = _check(
        ctx,
        impl=impl_input,
        ref=ref_input,
        models="lg:models",
        solver="cvc5",
        obligation="verilog-vs-netlist",
        label="lec_lhd_verilog_netlist",
    )

    # Yosys/lgcheck checks the original Verilog against the mapped netlist.
    yosys_block = _check(
        ctx,
        impl=impl_input,
        ref=ref_input,
        models="lg:models",
        solver="lgyosys",
        obligation="verilog-vs-netlist",
        label="lec_yosys_verilog_netlist",
    )

    # LiveHD's in-process engine checks the Pyrope synthesis against its
    # own compiled source graph.  Keep an explicit unsupported result when the corpus entry
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
        pyrope_work = ctx.work.parent / "syn_lhd_pyrope"
        pyrope_net = pyrope_work / "netlist"
        pyrope_ref = pyrope_work / "W/synth/lg"
        if not (pyrope_net.is_dir() and pyrope_ref.is_dir()):
            pyrope_work = ctx.work / "pyrope-synth"
            pyrope_net = pyrope_work / "netlist"
            pyrope_ref = pyrope_work / "W/synth/lg"
            pyrope_knobs = ["--set", f"synth.liberty={ctx.liberty[0]}",
                            *abc_settings(ctx.tech.name)]
            if ctx.sdc.exists():
                pyrope_knobs += ["--set", f"pass.abc.delay={ctx.abc_delay_ps()}"]
            if ctx.test.pyrope_binding == "set":
                pyrope_knobs += [a for k, v in sorted(ctx.chparams().items())
                                for a in ("--set", f"compile.{k}={v}")]
            ctx.run(
                "synth_pyrope",
                [lhd, "synth", str(ctx.test.pyrope_top), "--top", ctx.top,
                 "--emit-dir", f"lg:{pyrope_net}", "--workdir", str(pyrope_work / "W"),
                 *pyrope_knobs],
                timeout=ctx.lec_timeout_s * 2 + 60,
            )
        pyrope_block = _check(
            ctx,
            impl=f"lg:{pyrope_net}",
            ref=f"lg:{pyrope_ref}",
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
