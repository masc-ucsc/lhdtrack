"""The shared endpoint every synthesis flow lands on.

Whoever produced the mapped netlist -- yosys+slang+abc, lhd from Verilog, or lhd from
Pyrope -- it is evaluated here, against the SAME Liberty and the SAME SDC, by
the SAME two timing engines. Area and delay are therefore comparable by
construction rather than by hope.

TIMING IS MEASURED TWICE, ON PURPOSE:

  OpenSTA          the independent reference
  lhd OpenTimer    the engine LiveHD's own decisions are made with

and `delta_pct` between them is reported per test as a first-class column. It is
independent of which flow produced the netlist, so a LiveHD timing bug shows up
as a column that reddens across many tests rather than as a QoR number that is
quietly wrong. run.py fails the test past gates.sta_delta_pct_max.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError

NEEDS = ("yosys",)
OPTIONAL = ("sta",)


def evaluate(
    ctx: FlowContext,
    netlist: Path | None = None,
    lgraph: Path | None = None,
    qor_json: Path | None = None,
) -> dict:
    """Area + cells, then both timers. Returns the `qor` and `sta` blocks.

    AREA COMES FROM WHOEVER MAPPED THE DESIGN. The original plan ran
    `yosys stat -liberty` over every flow's netlist so one counter served all
    three. That does not survive contact with LiveHD: its Verilog emission is a
    HYBRID -- combinational logic mapped to sky130 cells, but registers still
    written as `always @(posedge clk)` -- so yosys sees behavioural RTL, counts
    no flops, and reports an area for a design it has not actually mapped.

    So each flow's area comes from its own mapper, and every row records
    `area_source`. Both read the SAME Liberty, which is the part that has to
    match; what differs is which program sums it, and the report says which.
    """
    # NORMALIZE FIRST. LiveHD instantiates real cells for combinational logic
    # but leaves registers as `always @(posedge clk)`. yosys `stat` therefore
    # counts no flops, and OpenSTA cannot link the design at all.
    #
    # So a LiveHD netlist is passed through yosys `proc` + `dfflibmap` + `abc`,
    # which maps ONLY what LiveHD left behavioural: cells already read from the
    # Liberty are blackboxes, so the combinational logic LiveHD mapped is a
    # boundary abc does not re-optimize. What gets added is the register
    # implementation LiveHD did not map itself -- which is exactly what the
    # yosys baseline already counts, so this is what makes the two comparable
    # rather than what makes them differ.
    structural, normalized, raw_flops = netlist, False, 0
    if netlist is not None and qor_json is not None:
        # Counted BEFORE normalization; on the yosys netlist normalization never
        # runs, so reading the whole file there was pure work for a number the
        # comparison below short-circuits past.
        raw_flops = _count_flops(netlist)
        structural = _normalize(ctx, netlist)
        normalized = True

    qor = _area(ctx, structural) if structural is not None else {}
    qor["normalized"] = normalized
    if qor_json is not None and qor_json.exists():
        # LiveHD's own accounting, kept beside the normalized number rather
        # than instead of it: the gap between them IS the register logic.
        qor.update(_lhd_qor(qor_json))
    out: dict = {"qor": qor, "sta": {}}

    # OpenSTA is the independent reference, not a prerequisite. Without it the
    # row still carries area and LiveHD's own timing -- it just cannot carry the
    # correlation, and says so rather than leaving a blank that reads like the
    # two timers agreed.
    # OpenSTA needs a STRUCTURAL netlist. LiveHD's hybrid emission has
    # behavioural flops, which OpenSTA cannot link, so the independent
    # reference is only available on the yosys netlist today. Recorded as a
    # note rather than a blank -- an absent correlation must not read like an
    # agreement.
    if ctx.tc.has("sta") and structural is not None:
        out["sta"].update(_opensta(ctx, structural))
    else:
        out["sta"]["opensta_note"] = "OpenSTA not staged -- no independent timing reference"

    # The LiveHD side only exists when a LiveHD flow produced an lgraph. A yosys
    # netlist has no lgraph, so its row carries OpenSTA only -- and that is
    # fine: the baseline's job is the reference number, not the correlation.
    if lgraph is not None:
        out["sta"].update(_opentimer(ctx, lgraph))

    ot, st = out["sta"].get("opentimer_ns"), out["sta"].get("opensta_ns")
    if ot and st:
        # THE TWO TIMERS MUST HAVE SEEN THE SAME CIRCUIT. OpenTimer runs as an
        # lhd pass over the RAW lg: netlist, whose registers are still
        # behavioural and therefore invisible to it; OpenSTA runs over the
        # NORMALIZED one, where those registers are real cells. On a design
        # that is mostly registers, the raw netlist's longest path is a single
        # clock buffer -- br_delay measured 0.052 ns that way, against 1.545 ns
        # for the same design with its flops present.
        #
        # That is a netlist difference, not a timer error, and reporting it as
        # a 97% timer disagreement blames the wrong thing. So the correlation
        # is only computed when normalization changed nothing.
        # BOTH counted the same way. Comparing this counter against yosys's
        # `num_cells` would differ by one on a design where nothing changed --
        # the question is whether normalization altered the circuit, and only
        # one counter applied to both files can answer it.
        norm_flops = _count_flops(structural) if structural is not None else 0
        if out["sta"].get("opentimer_unit_mismatch"):
            out["sta"]["delta_note"] = (
                "not comparable: " + out["sta"]["opentimer_unit_mismatch"]
            )
        elif not normalized or raw_flops == norm_flops:
            out["sta"]["delta_pct"] = round(abs(ot - st) / max(st, 1e-9) * 100.0, 2)
        else:
            out["sta"]["delta_note"] = (
                f"not comparable: OpenTimer saw {raw_flops} registers in lhd's raw "
                f"netlist, OpenSTA saw {norm_flops} after normalization -- lhd's "
                f"registers are behavioural, so its timer never sees them"
            )
            out["sta"]["opentimer_flops"] = raw_flops
            out["sta"]["opensta_flops"] = norm_flops
    return out


# ---------------------------------------------------------------- area ------
def _lhd_qor(path: Path) -> dict:
    """LiveHD's own mapping report -- `pass abc` writes it against the same .lib."""
    try:
        total = json.loads(path.read_text()).get("total", {})
    except (OSError, json.JSONDecodeError):
        return {"cells": 0, "area_um2": 0.0, "area_source": "lhd-qor(unreadable)"}
    return {
        "lhd_cells": int(total.get("gates", 0)),
        "lhd_area_um2": round(float(total.get("area", 0.0)), 3),
        "regions": int(total.get("regions", 0)),
        # lhd's ABC-reported critical path, distinct from the OpenTimer pass.
        "abc_max_delay_ns": round(float(total.get("max_delay", 0.0)), 4),
    }


_CELL_INST = re.compile(r"^\s*([A-Za-z_][\w$]*)\s+[A-Za-z_\\][\w$\\.\[\]]*\s*\(", re.M)
_FLOP_CELL = re.compile(r"(dff|dfxtp|_df|latch|\bdl[a-z]*)", re.I)
_NOT_A_CELL = {"module", "endmodule", "function", "task", "always", "assign"}


def _count_flops(netlist: Path) -> int:
    """Flip-flop INSTANCES in a netlist.

    This, not the total cell count, is what decides whether two timers saw the
    same circuit. Normalization legitimately drops the odd buffer even on a
    purely combinational design, so an exact cell-count match is too strict --
    but if one netlist has no registers and the other does, they are different
    circuits and their timings mean different things.
    """
    try:
        text = netlist.read_text(errors="replace")
    except OSError:
        return 0
    return sum(
        1 for m in _CELL_INST.finditer(text)
        if m.group(1) not in _NOT_A_CELL and _FLOP_CELL.search(m.group(1))
    )


def _normalize(ctx: FlowContext, netlist: Path) -> Path:
    """Map whatever the producer left behavioural, and nothing else.

    `read_liberty -lib` makes every already-instantiated cell an opaque
    blackbox, so `abc` cannot reach through one; it only maps the `$_`-gates
    that `proc` created from the behavioural register block. The result is a
    fully structural netlist that `stat` can count and OpenSTA can link.
    """
    out = ctx.work / "structural.v"
    libs = "\n".join(f"read_liberty -lib {lib}" for lib in ctx.liberty)
    # All families: ASAP7's flops live in SEQ, its inverters in INVBUF.
    map_args = " ".join(f"-liberty {lib}" for lib in ctx.liberty)
    script = f"""
{libs}
read_verilog -sv {netlist}
hierarchy -check -top {ctx.top}
proc
opt_clean
memory_map
# `techmap` before `dfflibmap`: lhd's register block lowers to a WIDE $dff,
# and dfflibmap only handles 1-bit $_DFF_ cells ("Wide register cell type
# $dff is not supported"). This is the same order yosys's own `synth` uses.
techmap
opt -fast
dfflibmap {map_args}
abc {map_args}
setundef -zero
splitnets
opt_clean -purge
write_verilog -noattr -noexpr {out}
"""
    ys = ctx.write("normalize.ys", script)
    m = ctx.run("normalize", [ctx.tool("yosys"), "-s", ys], check=False)
    if not m.ok or not out.exists():
        raise FlowError(f"could not normalize {netlist.name} to a structural netlist\n{m.tail()}")
    return out


def _area(ctx: FlowContext, netlist: Path) -> dict:
    """`yosys stat -liberty` on the MAPPED netlist.

    yosys is used here even for LiveHD's netlist deliberately: one cell counter
    for all three flows means an area delta is a real area delta, not two tools
    disagreeing about what counts as a cell.
    """
    reads = "\n".join(f"read_liberty -lib {lib}" for lib in ctx.liberty)
    # The pinned technologies each stage one complete Liberty. Keep this loop
    # for local toolchains that deliberately provide multiple libraries.
    stats = "\n".join(f"stat -liberty {lib} -json" for lib in ctx.liberty)
    script = f"""
{reads}
# -sv: LiveHD's gate-level emission uses `always_comb`, which plain
# read_verilog rejects. The yosys netlist parses either way, so this is
# uniform across all three flows rather than conditional on which produced it.
read_verilog -sv {netlist}
hierarchy -check -top {ctx.top}
{stats}
"""
    ys = ctx.write("stat.ys", script)
    # Neither -q nor -l: `-q` suppresses the JSON `stat` prints, and `-l` sends
    # the whole log to a file instead of to the measured run's log, which is
    # where this parses it from. Both were silently producing area 0.
    m = ctx.run("area", [ctx.tool("yosys"), "-s", ys], check=False)
    if not m.ok:
        raise FlowError(f"yosys stat failed on {netlist.name}\n{m.tail()}")

    text = m.log.read_text(errors="replace")
    doc = _merge_stats(text)
    if doc:
        # yosys keys modules by their RTLIL name, so a public module arrives as
        # "\\br_delay" rather than "br_delay". `design` is the flattened
        # whole-design total and is the right fallback: after `synth -flatten`
        # it equals the top.
        # `design` FIRST, not the top module. yosys reports per-module counts,
        # and a hierarchical netlist keeps most of its cells in submodules --
        # lhd lowers a register array into a `cgen_memory_*` submodule, so the
        # top of br_delay holds 75 cells while the design holds 580. Reading
        # the top module gave a cell count 7.7x too low next to an area that
        # was already hierarchical and therefore correct: the two numbers in
        # one row disagreed about what design they described.
        mods = {k.lstrip("\\"): v for k, v in doc.get("modules", {}).items()}
        top = doc.get("design") or mods.get(ctx.top) or (
            next(iter(mods.values())) if mods else {}
        )
        by_type = top.get("num_cells_by_type", {})
        flops = sum(v for k, v in by_type.items() if re.search(r"(dff|dfxtp|_df|latch)", k, re.I))
        area = round(float(top.get("area", 0.0)), 3)
        seq = round(float(top.get("sequential_area", 0.0)), 3)
        return {
            "cells": int(top.get("num_cells", 0)),
            "flops": int(flops),
            "area_um2": area,
            # Split out because a delta in total area means something different
            # depending on whether it moved the flops or the logic -- and
            # because it is the half that is comparable against a mapper whose
            # own report may or may not count registers.
            "seq_area_um2": seq,
            "comb_area_um2": round(area - seq, 3),
            "area_source": "yosys-stat",
        }

    # Fall back to the human-readable report -- some yosys builds emit no JSON
    # for `stat -json`, and a missing area is worth degrading for, not dying on.
    cells = _grep_int(text, r"Number of cells:\s+(\d+)")
    area = _grep_float(text, r"Chip area for module '[^']*':\s+([0-9.]+)")
    return {"cells": cells or 0, "flops": 0, "area_um2": round(area or 0.0, 3),
            "area_source": "yosys-log"}


# -------------------------------------------------------------- OpenSTA -----
def _opensta(ctx: FlowContext, netlist: Path) -> dict:
    """The reference timer, run on EVERY flow's netlist.

    Reports `achieved_ns = period - worst_slack`: the shortest clock period
    this netlist would actually close at. That is the number worth comparing --
    raw slack only says how much room is left against one arbitrary target
    period, so two designs given the same SDC would look different for a reason
    that is about the SDC rather than about them.
    """
    reads = "\n".join(f"read_liberty {lib}" for lib in ctx.liberty)
    tcl = f"""
{reads}
read_verilog {netlist}
link_design {ctx.top}
read_sdc {ctx.sdc}
puts "LHDTRACK_WNS [worst_slack -max]"
puts "LHDTRACK_TNS [total_negative_slack -max]"
puts "LHDTRACK_PERIOD [get_property [lindex [all_clocks] 0] period]"
report_checks -path_delay max -format short -digits 4 -group_count 1
exit
"""
    script = ctx.write("opensta.tcl", tcl)
    m = ctx.run("opensta", [ctx.tool("sta"), "-no_init", "-exit", script], check=False)
    text = m.log.read_text(errors="replace")
    if not m.ok:
        # DEGRADE, DO NOT DIE. `sta` is declared OPTIONAL by every synthesis
        # flow, which run.py reads as "a note and a missing column", not "lose
        # the row". Raising here threw away the area and the OpenTimer figure
        # this flow had already measured because the independent reference --
        # explicitly not a prerequisite -- could not link the netlist.
        return {
            "opensta_note": (
                f"OpenSTA could not time {netlist.name}: "
                + (m.tail(3).replace("\n", " ")[:160] or f"exit {m.rc}")
            )
        }

    wns = _grep_float(text, r"LHDTRACK_WNS\s+(-?[0-9.eE+-]+)")
    tns = _grep_float(text, r"LHDTRACK_TNS\s+(-?[0-9.eE+-]+)")
    period = _grep_float(text, r"LHDTRACK_PERIOD\s+(-?[0-9.eE+-]+)")

    # Keys keep the `_ns` suffix for ledger compatibility, but the VALUES are
    # in the library's own unit -- see `time_unit`. Converting ASAP7's
    # picoseconds to nanoseconds would print 0.154 where the tool and the SDC
    # both say 154, so the unit travels with the number instead.
    out = {
        "time_unit": ctx.tech.time_unit if ctx.tech else "ns",
        "wns_ns": round(wns, 4) if wns is not None else None,
        "tns_ns": round(tns, 4) if tns is not None else None,
        "sdc_period_ns": round(period, 4) if period is not None else None,
    }
    if wns is not None and period is not None:
        achieved = period - wns
        if achieved > 1e-9:
            out["opensta_ns"] = round(achieved, 4)
        else:
            # slack == the whole period means nothing with delay was timed. That
            # is a MISSING measurement, not a 0 ns critical path, and printing
            # `-0.0` would read like an extraordinarily fast design.
            #
            # Name the path it DID find: "no timed path" on a design with 34
            # registers is a different problem from one on a design with none,
            # and the startpoint says which. A port-to-port feedthrough winning
            # means the registers are off the timing graph entirely -- usually
            # a clock that does not propagate into a submodule.
            start = _grep(text, r"Startpoint:\s*(.+)")
            end = _grep(text, r"Endpoint:\s*(.+)")
            where = f"; worst path {start} -> {end}" if start and end else ""
            out["opensta_note"] = (
                f"OpenSTA timed no path with positive delay{where}. If the design "
                "has registers, they are not on the timing graph"
            )
    return out


# ------------------------------------------------------------ OpenTimer -----
def _opentimer(ctx: FlowContext, lgraph: Path) -> dict:
    """LiveHD's own timer, on the same netlist and the same Liberty.

    Uses the FIRST Liberty only: `lhd pass opentimer` takes a single library
    positionally. For sky130 that is the whole corner; for ASAP7 the mapped
    netlist may reference cells from a sibling family, in which case lhd
    reports no whole-design max_delay and the correlation column is simply
    absent for that row rather than wrong.
    """
    wd = ctx.work / "OT"
    m = ctx.run(
        "opentimer",
        [
            ctx.tool("lhd"), "pass", "opentimer",
            "--top", f"{ctx.top}.{ctx.top}",
            f"lg:{lgraph.name}",
            str(ctx.liberty[0]),
            "--workdir", str(wd),
        ],
        check=False,
    )
    timing = wd / "timing.json"
    if not m.ok or not timing.exists():
        return {"opentimer_ns": None, "opentimer_note": "no whole-design max_delay reported"}
    try:
        doc = json.loads(timing.read_text())
    except (OSError, json.JSONDecodeError):
        return {"opentimer_ns": None, "opentimer_note": "unparseable timing.json"}
    delay = _dig(doc, "max_delay")
    out = {"opentimer_ns": round(float(delay), 4) if delay is not None else None}
    # lhd declares the unit it reports in. If that disagrees with the library's
    # own, the two timers are being compared across a 1000x scale factor and
    # the correlation below is meaningless -- say so rather than compute it.
    lhd_unit = _dig(doc, "time_unit")
    tech_unit = ctx.tech.time_unit if ctx.tech else "ns"
    if lhd_unit and tech_unit and lhd_unit != tech_unit:
        out["opentimer_unit_mismatch"] = f"lhd reports {lhd_unit}, the Liberty is {tech_unit}"
    return out


# ---------------------------------------------------------------- utils -----
def _merge_stats(text: str) -> dict | None:
    """Combine one `stat -json` object per Liberty family into one total.

    Cell COUNTS are identical in every object (same netlist), so they are taken
    from the first. AREA differs: each run prices only the cells that family
    defines and reports 0 for the rest, so the areas add up.
    """
    objs = _json_objects(text)
    if not objs:
        return None
    merged = dict(objs[0])
    for key in ("design",):
        if key in merged and isinstance(merged[key], dict):
            merged[key] = dict(merged[key])
    total_area = total_seq = 0.0
    for obj in objs:
        d = obj.get("design") or next(iter(obj.get("modules", {}).values()), {})
        total_area += float(d.get("area", 0.0))
        total_seq += float(d.get("sequential_area", 0.0))
    if "design" in merged:
        merged["design"]["area"] = total_area
        merged["design"]["sequential_area"] = total_seq
    for name, mod in list(merged.get("modules", {}).items()):
        merged["modules"][name] = {**mod, "area": total_area, "sequential_area": total_seq}
    return merged


def _json_objects(text: str) -> list[dict]:
    """Every complete `stat` JSON object embedded in a yosys log."""
    decoder = json.JSONDecoder()
    out, start = [], text.find("{")
    while start != -1:
        try:
            obj, end = decoder.raw_decode(text, start)
        except json.JSONDecodeError:
            start = text.find("{", start + 1)
            continue
        if isinstance(obj, dict) and ("modules" in obj or "design" in obj):
            out.append(obj)
            start = text.find("{", end)
        else:
            start = text.find("{", start + 1)
    return out


def _dig(doc, key):
    """Find `key` anywhere in a nested dict -- lhd nests timing per top."""
    if isinstance(doc, dict):
        if key in doc:
            return doc[key]
        for v in doc.values():
            found = _dig(v, key)
            if found is not None:
                return found
    elif isinstance(doc, list):
        for v in doc:
            found = _dig(v, key)
            if found is not None:
                return found
    return None


def _grep(text: str, pattern: str, flags=0) -> str | None:
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None


def _grep_int(text: str, pattern: str, flags=0) -> int | None:
    m = re.search(pattern, text, flags)
    return int(m.group(1)) if m else None


def _grep_float(text: str, pattern: str, flags=0) -> float | None:
    m = re.search(pattern, text, flags)
    return float(m.group(1)) if m else None
