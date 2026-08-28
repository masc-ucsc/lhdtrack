"""LiveHD synthesis, PYROPE in -- the flow the whole tracker exists to measure.

Uses the ONE-SHOT `lhd synth`: compile, color, abc and opentimer over one
in-memory design, which is what a Pyrope user actually runs. Its per-phase
timings come from lhd's own `phases` account in --result-json, so the row splits
the same way the Verilog row does instead of collapsing into one opaque total.

READ THE `pyrope_status` OF THE TEST BEFORE READING THIS ROW. On a test whose
pyrope/ is still an `auto` seed from `lhd compile verilog --emit-dir pyrope:`,
both LiveHD flows enter the same LGraph and this measures the two front ends,
not the two languages. The report keeps those rows in a separate population and
the headline geomean excludes them; see corpus.Test.comparable().
"""

from __future__ import annotations

import json

from lhdtrack.context import FlowContext, FlowError, FlowSkip

NAME = "syn_lhd_pyrope"
KIND = "synth"
NEEDS = ("lhd", "yosys")
OPTIONAL = ("sta",)
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    lhd = ctx.tool("lhd")
    if not ctx.test.pyrope_top.exists():
        raise FlowSkip(f"no pyrope source: {ctx.test.pyrope_top.name}")

    # A `monomorphic` Pyrope pins its parameters as comptime constants, so
    # there is nothing to pass and passing something would be a lie. Only a
    # test that declares `[pyrope] param_binding = "set"` gets --set flags --
    # and `lhdtrack check` refuses a monomorphic test with more than one config,
    # which is the case where the mismatch would otherwise go unnoticed.
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
    # The ONE-SHOT `lhd synth` has its own key: it sets pass.abc AND
    # pass.opentimer from a single Liberty, and rejects pass.abc.library
    # outright ("synth takes ONE Liberty ... --set synth.liberty=PATH").
    # The manual `pass abc` path in syn_lhd_verilog.py uses the other spelling.
    lib_args = ["--set", f"synth.liberty={ctx.liberty[0]}"]

    sets = [*lib_args, "--set", f"abc.delay={ctx.abc_delay_ps()}"]
    if ctx.test.pyrope_binding == "set":
        sets += [a for k, v in sorted(ctx.chparams().items()) for a in ("--set", f"compile.{k}={v}")]

    m = ctx.run(
        "synth",
        [
            lhd, "synth", str(ctx.test.pyrope_top), "--top", ctx.top,
            "--workdir", "W", "--emit-dir", "lg:netlist",
            "--result-json", "synth.json", "--stats", *sets,
        ],
    )

    # Replace the single wall-clock with lhd's own phase account, so this row
    # is comparable stage-for-stage with the yosys and lhd-verilog rows.
    phases = _phases(ctx)
    if phases:
        del ctx.stage.time_ms["synth"]
        rss = ctx.stage.peak_rss_kb.pop("synth", 0)
        ctx.stage.time_ms.update(phases)
        # lhd runs the phases in one process, so the peak belongs to whichever
        # phase was largest. Attributing it to the map stage is the honest
        # approximation: on every design measured so far, ABC is that phase.
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
        [lhd, "compile", "lg:netlist", "--top", f"{ctx.top}.{ctx.top}",
         "--emit-dir", "verilog:netv", "--workdir", "Wemit"],
    )
    from qor_endpoint import emitted_verilog, evaluate

    try:
        netlist = emitted_verilog(ctx, ctx.work / "netv")
    except FlowError as error:
        raise FlowError("lhd emitted no gate-level Verilog for the Pyrope design") from error

    out = {"netlist": netlist, **evaluate(ctx, netlist, lgraph=ctx.work / "netlist",
                     # The ONE-SHOT `lhd synth` nests its report under <workdir>/synth/,
                     # unlike the manual `pass abc` path which writes it at the top.
                     qor_json=ctx.work / "W" / "synth" / "qor.json")}
    out["pyrope_status"] = ctx.test.pyrope_status
    return out


def _phases(ctx: FlowContext) -> dict[str, int]:
    """compile / color / map, from lhd's `phases` block."""
    path = ctx.work / "synth.json"
    if not path.exists():
        return {}
    try:
        doc = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return {}
    phases = doc.get("phases", {})
    if not isinstance(phases, dict):
        return {}

    out = {"compile": 0, "color": 0, "map": 0}
    for name, ms in phases.items():
        try:
            ms = int(float(ms))
        except (TypeError, ValueError):
            continue
        low = name.lower()
        if "color" in low:
            out["color"] += ms
        elif "abc" in low or "map" in low:
            out["map"] += ms
        elif "opentimer" in low or "sta" in low:
            continue  # qor_endpoint times STA itself, on both engines
        else:
            out["compile"] += ms
    return {k: v for k, v in out.items() if v}
