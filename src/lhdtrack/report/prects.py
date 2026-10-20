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
           'no timing or memory numbers are imported from satsuma. Both LiveHD columns use '
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
           'These few cases have no headline geomean.</p>',
           '<p class="muted">Click a metric header to sort ascending, descending, then restore '
           'the original order. Missing values sort last.</p>',
           '<div class="scroll"><table><thead><tr>'
           '<th rowspan="2" class="l">Test / configuration</th>']
    metrics = (("Wall s", "command_wall_s", 2), ("CPU s", "cpu_s", 2),
               ("Peak mem MiB", "max_process_rss_kb", 1),
               ("Cells", "logical_cells", 0), ("Macros", "macros", 0),
               ("Area µm²", "instance_area_um2", 2),
               ("Setup WNS ps", "setup_wns_ps", 2), ("Setup TNS ps", "setup_tns_ps", 2),
               ("Hold WNS ps", "hold_wns_ps", 2))
    width = len(metrics) + 1
    out.extend(f'<th class="g" colspan="{width}">{esc(title)}</th>' for _, title in FLOWS)
    out.append('</tr><tr>')
    for _ in FLOWS:
        out.append('<th class="g">Status</th>')
        out.extend(f'<th>{label}</th>' for label, _, _ in metrics)
    out.append('</tr></thead><tbody>')
    for test, config in sorted({(r["test"], r["config"]) for r in rows}):
        out.append(f'<tr><td class="l">{esc(test)}<br>'
                   f'<small class="muted">{esc(config)}</small></td>')
        inputs = set()
        for flow, _ in FLOWS:
            row = current.get((test, config, flow))
            if row is None:
                out.append(f'<td colspan="{width}">—</td>')
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
            status = row["status"]
            note = f' title="{esc(row["note"], quote=True)}"' if row.get("note") else ""
            status_cell = f'<td class="g {esc(status)}"{note}>{esc(status)}'
            image = work / "physical/post-place.png"
            if flow.startswith("place_") and image.is_file():
                status_cell += ' · ' + link(image, "PNG")
            out.append(status_cell + '</td>')
            for _, key, digits in metrics:
                value = measurement.get(key) if key in measurement else score.get(key)
                if key.endswith("rss_kb") and value is not None:
                    value /= 1024
                out.append(f'<td>{number(value, digits)}</td>')
        if len(inputs) > 1:
            raise ValueError(f"{test}/{config}: source bytes or SDC differ between columns")
        out.append('</tr>')
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
        out.append('<h2>LiveHD resynthesis and incremental repeat</h2>'
                   '<p>The repeat changes no RTL or options and reuses the cold run’s exact '
                   '<code>--workdir</code>; exports and independent timing use separate outputs. '
                   'CPU, memory, perf stat and export accounting are the same as for the cold run. '
                   'Speedup is cold wall ÷ measured wall. OpenROAD is not repeated. '
                   'A missing resynthesis value means this compiler did not record that stage.</p>'
                   '<p>Synthesis covers recorded compiler phases before the first physical/timing '
                   'stage; ABC is included in synthesis. Resynth pre runs before placement; '
                   'resynth stage callbacks are nested within physical stages. '
                   'Place, TD place, resize and repair include their timing callbacks. '
                   'Region work is the reported per-color ABC check/remap/CEC time within those '
                   'callbacks. These inclusive times must not be summed. Fired counts include '
                   'final feedback that can request another run; accepted and restarts record '
                   'work applied here.</p>'
                   '<div class="scroll"><table><thead><tr>'
                   '<th class="l">Test / configuration</th><th>Invocation</th><th>Status</th>')
        breakdown_metrics = (("Wall s", "wall", 2), ("Speedup", "speedup", 2),
            ("CPU s", "cpu", 2), ("Peak mem MiB", "rss", 1),
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
        out.extend(f'<th>{label}</th>' for label, _, _ in breakdown_metrics)
        out.append('</tr></thead><tbody>')
        for (test, config, flow), row in native_rows:
            data = row.get("qor", {}).get("placement_comparison", {})
            values = native_breakdown(data)
            cold = current.get((test, config, "place_lhd_verilog_aegraph"), {})
            cold_wall = cold.get("qor", {}).get("placement_comparison", {}).get(
                "measurement", {}).get("command_wall_s")
            values["speedup"] = cold_wall / values["wall"] if cold_wall and values["wall"] else None
            mode = "incremental" if flow == INCREMENTAL_FLOW else "cold"
            image = Path(row["placement_work"]) / "physical/post-place.png"
            status = esc(row["status"])
            if image.is_file():
                status += ' · ' + link(image, "PNG")
            out.append(f'<tr><td class="l">{esc(test + "/" + config)}</td>'
                       f'<td>{mode}</td><td>{status}</td>')
            out.extend(f'<td>{number(values.get(key), digits)}</td>'
                       for _, key, digits in breakdown_metrics)
            out.append('</tr>')
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
