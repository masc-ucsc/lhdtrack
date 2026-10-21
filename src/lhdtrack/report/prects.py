"""Render the corpus pre-CTS run from the normal per-host append-only ledger."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path

from lhdtrack.ledger import Ledger
from .html import CSS, SORT_CSS, SORT_JS

INCREMENTAL_FLOW = "place_lhd_verilog_aegraph_incremental"

FLOWS = (
    ("syn_lhd_verilog_prects_aegraph", "LiveHD ABC synthesis · aegraph"),
    ("syn_hightide", "HighTide / Yosys synthesis"),
    ("place_lhd_verilog_aegraph", "LiveHD synthesis + placement · aegraph"),
    (INCREMENTAL_FLOW, "LiveHD synthesis + placement · incremental"),
    ("place_openroad_prects", "HighTide / OpenROAD placement · pre-CTS"),
)

# Keep cold/repeat LiveHD jobs adjacent, independent of the display grouping.
RUN_FLOWS = (FLOWS[0], FLOWS[2], FLOWS[3], FLOWS[1], FLOWS[4])


def native_breakdown(data: dict) -> dict:
    """Keep nested callback times separate from their enclosing physical stages."""
    phases = data.get("phases", [])
    values: dict[str, float] = {}
    synthesis = 0.0
    physical_started = False
    for phase in phases:
        name, seconds = phase.get("name", ""), phase.get("ms", 0) / 1000
        values[name] = values.get(name, 0) + seconds
        if name.startswith(("pass.place", "pass.opentimer")):
            physical_started = True
        if not physical_started:
            synthesis += seconds
    io = data.get("io_resynth", {})
    abc = data.get("incremental", {}).get("abc", {})
    stats = data.get("placement_stats", {})
    if stats.get("reused_final_state"):
        for name in ("pass.place.io_resynth.before", "pass.place.io_resynth",
                     "pass.place.post_synth_timing", "pass.place", "pass.place.timing_driven",
                     "pass.place.resize", "pass.place.repair"):
            values.setdefault(name, 0.0)
        values["pass.place"] += values.get("pass.place.restore", 0.0)
    measurement = data.get("measurement", {})
    rss = measurement.get("max_process_rss_kb")
    return {
        "wall": measurement.get("command_wall_s"), "cpu": measurement.get("cpu_s"),
        "rss": rss / 1024 if rss is not None else None,
        "synthesis": synthesis if phases else None,
        "abc": values.get("pass.abc"),
        "resynth_pre": values.get("pass.place.io_resynth.before"),
        "resynth_stage": values.get("pass.place.io_resynth"),
        "post_synth": values.get("pass.place.post_synth_timing"),
        "place": values.get("pass.place"),
        "timing_place": values.get("pass.place.timing_driven"),
        "resize": values.get("pass.place.resize"),
        "repair": values.get("pass.place.repair"),
        "sta": values.get("pass.opentimer"),
        "hits": abc.get("hits"), "misses": abc.get("misses"),
        "fired": io.get("fired"), "accepted": io.get("accepted"),
        "region_work": io.get("seconds"),
        "restarts": io.get("restarts"), "overflow": stats.get("gp_overflow"),
        "converged": stats.get("gp_converged"),
        "cells": data.get("score", {}).get("logical_cells"),
        "wns": data.get("score", {}).get("setup_wns_ps"),
        "tns": data.get("score", {}).get("setup_tns_ps"),
    }


def render(root: Path, output: Path, *, host: str | None = None, run: str | None = None) -> None:
    rows = [r for r in Ledger(root).load(host) if r.get("placement_run")]
    if rows:
        # Named smoke/corrective runs need not sort lexically by chronology.
        run = run or rows[-1]["run_id"]
        rows = [r for r in rows if r["run_id"] == run]
    identities = {(r["host"], r["host_class"], r["liberty_sha256"],
                   json.dumps(r["versions"], sort_keys=True)) for r in rows}
    if len(identities) > 1:
        raise ValueError("pre-CTS comparison mixes host, tool revisions or Liberty")
    current = {(r["test"], r["config"], r["flow"]): r for r in rows}
    esc = html.escape

    def link(path: Path, label: str) -> str:
        relative = os.path.relpath(path, output.parent)
        return f'<a href="{esc(relative, quote=True)}">{esc(label)}</a>'

    def number(value, digits=2) -> str:
        if isinstance(value, bool):
            return "yes" if value else "no"
        return "—" if value is None else f"{value:,.{digits}f}"

    # The recipes remain five independent measurements. Pair the two LiveHD
    # placement invocations within each metric for direct cold/repeat comparison.
    cold_flow = "place_lhd_verilog_aegraph"
    modes = (cold_flow, INCREMENTAL_FLOW)
    groups = tuple((title, modes if flow == cold_flow else (flow,))
                   for flow, title in FLOWS if flow != INCREMENTAL_FLOW)

    def table_header(groups, labels) -> str:
        parts = ['<div class="scroll"><table><thead><tr>'
                 '<th rowspan="3" class="l">Test / configuration</th>']
        for title, flows in groups:
            parts.append(f'<th class="g" colspan="{len(labels) * len(flows)}">'
                         f'{esc(title)}</th>')
        parts.append('</tr><tr>')
        for _, flows in groups:
            for index, label in enumerate(labels):
                border = ' class="g"' if index == 0 else ''
                span = 'colspan="2"' if len(flows) == 2 else 'rowspan="2"'
                parts.append(f'<th{border} {span}>{esc(label)}</th>')
        parts.append('</tr><tr>')
        for _, flows in groups:
            if len(flows) == 2:
                for index, label in enumerate(labels):
                    for mode in ("Cold", "Incr"):
                        border = ' class="g"' if index == 0 and mode == "Cold" else ''
                        parts.append(f'<th{border} aria-label="{esc(label)} · {mode}">'
                                     f'{mode}</th>')
        parts.append('</tr></thead><tbody>')
        return ''.join(parts)

    def table_row(test, config, groups, labels, values, statuses) -> str:
        parts = [f'<tr><td class="l">{esc(test)}<br>'
                 f'<small class="muted">{esc(config)}</small></td>']
        for _, flows in groups:
            if not any(flow in values for flow in flows):
                parts.append(f'<td colspan="{len(labels) * len(flows)}">—</td>')
                continue
            for index in range(len(labels)):
                for mode_index, flow in enumerate(flows):
                    classes = ['g'] if index == 0 and mode_index == 0 else []
                    if index == 0 and flow in statuses:
                        classes.append(statuses[flow])
                    attr = f' class="{esc(" ".join(classes))}"' if classes else ''
                    value = values[flow][index] if flow in values else '—'
                    parts.append(f'<td{attr}>{value}</td>')
        parts.append('</tr>')
        return ''.join(parts)

    def status_value(row) -> str:
        note = f' title="{esc(row["note"], quote=True)}"' if row.get("note") else ''
        value = f'<span{note}>{esc(row["status"])}</span>'
        image = Path(row["placement_work"]) / "physical/post-place.png"
        if row["flow"].startswith("place_") and image.is_file():
            value += ' · ' + link(image, "PNG")
        return value

    out = ['<!doctype html><html lang="en"><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>ASAP7 synthesis and pre-CTS placement · llmHDL</title>',
           f'<style>{CSS}{SORT_CSS}.failed{{color:var(--bad)}}'
           '.skipped{color:var(--warn)}.stage-times td{white-space:normal;'
           'overflow-wrap:anywhere}</style>',
           '<body><div class="wrap">',
           '<h1>ASAP7 synthesis and pre-CTS placement</h1>',
           f'<p>lhdtrack corpus · host {esc(host or (rows[0]["host"] if rows else "unknown"))}'
           f' · run <code>{esc(run or "unmeasured")}</code> · RVT / TT NLDM.<br>'
           f'<small>{esc(rows[0]["versions"].get("lhd", "")) if rows else ""}</small></p>',
           '<p>Every column reads the same frozen Verilog, merged Liberty and corpus SDC. '
           'LiveHD follows the ABC / compile SAT disabled profile in '
           'results-syn-satsuma.html: default coloring and arithmetic selection, SDC-derived '
           'mapping delay, native memory preservation. These are fresh measurements on this host; '
           'no timing or memory numbers are imported from satsuma. All LiveHD invocations use '
           'aegraph folding; placement sets <code>synth.place.fp=false</code> for these small '
           'designs. Baseline-versus-aegraph comparisons '
           'remain in the synthesis/simulation reports.</p>',
           '<p>Netlist and requested DEF/LEF exports are included in wall time, CPU and memory. '
           'LiveHD internal timing is included in its invocation; the older synthesis report '
           'uses compiler stage times. Area here uses common LEF instance geometry. '
           'Independent OpenSTA preparation, timing and area collection are excluded. '
           'Preparation only elaborates wiring and hierarchy; it never optimizes, maps, prunes '
           'dangling cells or lowers native state. Synthesis uses zero-wire timing; placement '
           'uses estimated interconnect RC with ideal clocks. No CTS or routing is run.</p>',
           '<p>CPU includes waited-for descendants. Peak memory is the largest individual '
           'process peak RSS. Measurements are serial. The incremental column reuses its cold '
           'placement workdir; the other columns start fresh. '
           'Each LiveHD placement metric pairs Cold | Incr values side by side. '
           'These few cases have no headline geomean.</p>',
           '<p class="muted">Click a metric header to sort ascending, descending, then restore '
           'the original order. Missing values sort last.</p>']
    metrics = (("Wall s", "command_wall_s", 2), ("CPU s", "cpu_s", 2),
               ("Peak mem MiB", "max_process_rss_kb", 1),
               ("Cells", "logical_cells", 0), ("Macros", "macros", 0),
               ("Area µm²", "instance_area_um2", 2),
               ("Setup WNS ps", "setup_wns_ps", 2), ("Setup TNS ps", "setup_tns_ps", 2),
               ("Hold WNS ps", "hold_wns_ps", 2))
    labels = ("Status", *(label for label, _, _ in metrics))
    out.append(table_header(groups, labels))
    for test, config in sorted({(r["test"], r["config"]) for r in rows}):
        values, statuses = {}, {}
        inputs = set()
        for flow, _ in FLOWS:
            row = current.get((test, config, flow))
            if row is None:
                continue
            data = row.get("qor", {}).get("placement_comparison", {})
            work = Path(row["placement_work"])
            measurement = data.get("measurement", {})
            resource = work / "measurement/measurement.json"
            if not measurement and resource.exists():
                measurement = json.loads(resource.read_text())
            score = data.get("score", {})
            if data:
                inputs.add((data["sdc_sha256"], json.dumps(data["source_sha256"], sort_keys=True)))
            statuses[flow] = row["status"]
            values[flow] = [status_value(row)]
            for _, key, digits in metrics:
                value = measurement.get(key) if key in measurement else score.get(key)
                if key.endswith("rss_kb") and value is not None:
                    value /= 1024
                values[flow].append(number(value, digits))
        if len(inputs) > 1:
            raise ValueError(f"{test}/{config}: source bytes or SDC differ between columns")
        out.append(table_row(test, config, groups, labels, values, statuses))
    out.append('</tbody></table></div>')
    out.append('<p>OpenROAD’s placement column starts from its retained synthesized netlist: '
               'add its synthesis column for RTL-to-placement wall/CPU. LiveHD placement includes '
               'synthesis. HighTide uses 8 threads, 50% core utilization and 0.60 target density; '
               'LiveHD disables floorplan planning and otherwise keeps compiler defaults, '
               'including density. '
               'These settings make this a whole-flow comparison '
               'rather than an isolated placer test.</p>')
    native_rows = [(key, row) for key, row in sorted(current.items())
                   if key[2] in ("place_lhd_verilog_aegraph", INCREMENTAL_FLOW)]
    if native_rows:
        replayed = any(row.get("qor", {}).get("placement_comparison", {}).get(
            "placement_stats", {}).get("reused_final_state") for _, row in native_rows)
        reuse_note = (
            'An unchanged repeat restores the completed repaired netlist, placement/RC '
            'and final boundary timing together; no new IO resynthesis or repair is needed. '
            'Place time includes validation and restoration of that saved state. '
            'GP statistics on replay describe the retained placement. ' if replayed else '')
        out.append('<h2>LiveHD resynthesis and incremental repeat</h2>'
                   '<p>The repeat changes no RTL or options and reuses the cold run’s exact '
                   '<code>--workdir</code>; exports and independent timing use separate outputs. '
                   'CPU, memory, perf stat and export accounting are the same as for the cold run. '
                   'Speedup is cold wall ÷ measured wall. OpenROAD is not repeated. '
                   f'{reuse_note}'
                   'A missing resynthesis value means this compiler did not record that stage.</p>'
                   '<p>Synthesis covers recorded compiler phases before the first physical/timing '
                   'stage; ABC is included in synthesis. Resynth pre runs before placement; '
                   'resynth stage callbacks are nested within physical stages. '
                   'Place, TD place, resize and repair include their timing callbacks. '
                   'Region work is the reported per-color ABC check/remap/CEC time within those '
                   'callbacks. These inclusive times must not be summed. Fired counts include '
                   'diagnostics collected during the invocation; accepted and restarts record '
                   'work applied here.</p>')
        breakdown_metrics = (("Wall s", "wall", 2), ("Speedup", "speedup", 2),
            ("CPU s", "cpu", 2), ("Peak mem MiB", "rss", 1), ("Cells", "cells", 0),
            ("Synthesis s", "synthesis", 3), ("ABC s", "abc", 3),
            ("Resynth pre s", "resynth_pre", 3), ("Resynth stage s", "resynth_stage", 3),
            ("Region work s", "region_work", 3),
            ("Post-synth timing s", "post_synth", 3),
            ("Place s", "place", 3), ("TD place s", "timing_place", 3),
            ("Resize s", "resize", 3), ("Repair s", "repair", 3),
            ("Final STA s", "sta", 3), ("ABC hits", "hits", 0), ("ABC misses", "misses", 0),
            ("Resynth fired", "fired", 0), ("Accepted", "accepted", 0),
            ("Restarts", "restarts", 0), ("GP converged", "converged", 0),
            ("GP overflow", "overflow", 3), ("Setup WNS ps", "wns", 2),
            ("Setup TNS ps", "tns", 2))
        breakdown_labels = ("Status", *(label for label, _, _ in breakdown_metrics))
        native_groups = (("LiveHD placement · aegraph", modes),)
        out.append(table_header(native_groups, breakdown_labels))
        for test, config in sorted({key[:2] for key, _ in native_rows}):
            values, statuses = {}, {}
            cold = current.get((test, config, cold_flow), {})
            cold_wall = cold.get("qor", {}).get("placement_comparison", {}).get(
                "measurement", {}).get("command_wall_s")
            for flow in modes:
                row = current.get((test, config, flow))
                if row is None:
                    continue
                data = row.get("qor", {}).get("placement_comparison", {})
                measured = native_breakdown(data)
                measured["speedup"] = (cold_wall / measured["wall"]
                                       if cold_wall and measured["wall"] else None)
                statuses[flow] = row["status"]
                values[flow] = [status_value(row)]
                values[flow].extend(number(measured.get(key), digits)
                                    for _, key, digits in breakdown_metrics)
            out.append(table_row(test, config, native_groups, breakdown_labels, values, statuses))
        out.append('</tbody></table></div>')
    out.append('<details><summary>Compiler and physical stage times</summary>'
               '<table class="stage-times">'
               '<tr><th>Test / flow</th><th>Recorded stages</th></tr>')
    for (test, config, flow), row in sorted(current.items()):
        data = row.get("qor", {}).get("placement_comparison", {})
        phases = data.get("phases", [])
        text = ', '.join(f'{p.get("name", p.get("pass", "stage"))}: '
                         f'{p.get("ms", 0) / 1000:.3f} s' for p in phases)
        out.append(f'<tr><td>{esc(test + "/" + config + "/" + flow)}</td>'
                   f'<td>{esc(text)}</td></tr>')
    out.append('</table><p>LiveHD timing callbacks are nested in placement; '
               'overlapping compiler phase times must not be summed.</p></details>')
    out.append(f'</div><script>{SORT_JS}</script></body></html>')
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix('.html.tmp')
    temporary.write_text('\n'.join(out) + '\n')
    temporary.replace(output)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--out", type=Path)
    parser.add_argument("--host")
    parser.add_argument("--run")
    args = parser.parse_args()
    render(args.root, args.out or args.root / "target/results-placement-llmhdl.html",
           host=args.host, run=args.run)
