"""Render the corpus pre-CTS run from the normal per-host append-only ledger."""
from __future__ import annotations

import argparse
import html
import json
import os
from pathlib import Path

from lhdtrack.ledger import Ledger

FLOWS = (
    ("syn_lhd_verilog_prects_baseline", "LiveHD ABC synthesis · baseline profile"),
    ("syn_lhd_verilog_prects_aegraph", "LiveHD ABC synthesis · aegraph"),
    ("place_lhd_verilog", "LiveHD synthesis + placement · default engine"),
    ("syn_hightide", "HighTide / Yosys synthesis"),
    ("place_openroad_prects", "HighTide / OpenROAD placement · pre-CTS"),
)


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
        return "—" if value is None else f"{value:,.{digits}f}"

    out = ['<!doctype html><html lang="en"><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width,initial-scale=1">',
           '<title>ASAP7 synthesis and pre-CTS placement · llmHDL</title>',
           '<style>body{font:15px system-ui;margin:2rem;color:#172536;background:#f9fafc}'
           'h1{font-size:1.7rem}table{border-collapse:collapse;background:white}th,td{border:1px solid '
           '#ccd3df;padding:.7rem;text-align:left;vertical-align:top}th{background:#e9eff7}'
           'small{color:#526171}.scroll{overflow:auto}'
           '.failed{color:#a21a21}.skipped{color:#936000}'
           'a{color:#12529d}code{overflow-wrap:anywhere}details{margin:1rem 0}</style>',
           '<h1>ASAP7 synthesis and pre-CTS placement</h1>',
           f'<p>lhdtrack corpus · host {esc(host or (rows[0]["host"] if rows else "unknown"))}'
           f' · run <code>{esc(run or "unmeasured")}</code> · RVT / TT NLDM.</p>',
           '<p>Every column reads the same frozen Verilog, merged Liberty and corpus SDC. '
           'The LiveHD baseline follows the ABC / compile SAT disabled profile in '
           'results-syn-satsuma.html: default coloring and arithmetic selection, SDC-derived '
           'mapping delay, native memory preservation. These are fresh measurements on this host; '
           'no timing or memory numbers are imported from satsuma. Aegraph is an explicit experiment. '
           'The placement column keeps LiveHD’s default folding engine '
           'and physical options.</p>',
           '<p>Netlist and requested DEF/LEF exports are included in wall time, CPU and memory. '
           'LiveHD internal timing is included in its invocation; the older synthesis report '
           'uses compiler stage times. Area here uses common LEF instance geometry. '
           'Independent OpenSTA preparation, timing and area collection are excluded. '
           'Preparation only elaborates wiring and hierarchy; it never optimizes, maps, prunes '
           'dangling cells or lowers native state. Synthesis uses zero-wire timing; placement '
           'uses estimated interconnect RC with ideal clocks. No CTS or routing is run.</p>',
           '<p>CPU includes waited-for descendants. RSS is the largest individual process peak; '
           'tree RSS is a 100 ms sampled process-group sum and can double-count shared pages. '
           'Measurements are serial and cold. These few cases have no headline geomean.</p>',
           '<div class="scroll"><table><thead><tr><th>Test / configuration</th>']
    out.extend(f'<th>{esc(title)}</th>' for _, title in FLOWS)
    out.append('</tr></thead><tbody>')
    for test, config in sorted({(r["test"], r["config"]) for r in rows}):
        out.append(f'<tr><th>{esc(test)}<br><small>{esc(config)}</small></th>')
        inputs = set()
        for flow, _ in FLOWS:
            row = current.get((test, config, flow))
            if row is None:
                out.append('<td>—</td>')
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
            rss = measurement.get("max_process_rss_kb")
            tree = measurement.get("tree_peak_rss_kb")
            cells = score.get("logical_cells")
            cell = [f'<td class="{esc(status)}"><b>{esc(status)}</b>',
                    f'Wall {number(measurement.get("command_wall_s"))} s',
                    f'CPU {number(measurement.get("cpu_s"))} s',
                    f'Peak RSS {number(rss / 1024 if rss is not None else None, 1)} MiB',
                    f'Tree RSS {number(tree / 1024 if tree is not None else None, 1)} MiB',
                    f'Cells {number(cells, 0)}',
                    f'Macros {number(score.get("macros"), 0)}',
                    f'Area {number(score.get("instance_area_um2"))} µm²',
                    f'OpenSTA WNS {number(score.get("setup_wns_ps"))} ps',
                    f'OpenSTA TNS {number(score.get("setup_tns_ps"))} ps',
                    f'Hold WNS {number(score.get("hold_wns_ps"))} ps']
            counters = measurement.get("perf", {})
            cycles = counters.get("cycles", {}).get("value")
            instructions = counters.get("instructions", {}).get("value")
            if cycles and instructions:
                cell.append(f'IPC {instructions / cycles:.2f} · cycles {cycles / 1e6:.1f} M')
            if row.get("note"):
                cell.append(esc(row["note"]))
            if resource.exists():
                cell.append(link(resource, "resources") + ' · '
                            + link(work / "measurement/perf.csv", "perf stat") + ' · '
                            + link(work / "measurement/run.log", "log"))
            physical = work / "physical"
            if (physical / "out.def").exists():
                cell.append(link(physical / "out.def", "DEF") + ' · '
                            + link(physical / "lefs.json", "LEFs"))
            if (work / "score/setup_paths.rpt").exists():
                cell.append(link(work / "score/setup_paths.rpt", "OpenSTA paths") + ' · '
                            + link(work / "score/score.json", "QoR"))
            cell.append(link(work / "row.json", "ledger row"))
            out.append('<br>'.join(cell) + '</td>')
        if len(inputs) > 1:
            raise ValueError(f"{test}/{config}: source bytes or SDC differ between columns")
        out.append('</tr>')
    out.append('</tbody></table></div>')
    out.append('<p>OpenROAD’s placement column starts from its retained synthesized netlist: '
               'add its synthesis column for RTL-to-placement wall/CPU. LiveHD placement includes '
               'synthesis. HighTide uses 8 threads, 50% core utilization and 0.60 target density; '
               'LiveHD keeps compiler defaults, including its floorplan/density choices. '
               'These settings make this a whole-flow comparison '
               'rather than an isolated placer test.</p>')
    out.append('<details><summary>Compiler and physical stage times</summary><table>'
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
    if rows:
        spec = root / 'data' / f'placement-eval-{rows[0]["host"]}.json'
        if spec.exists():
            evidence = json.loads(spec.read_text())
            if evidence.get('run_id') == run:
                out.append('<details open><summary>Profiling and technology validation</summary>')
                for item in evidence.get('evidence', []):
                    out.append('<p>' + esc(item['description']) + ' '
                               + link(Path(item['path']), item['label']) + '</p>')
                out.append('</details>')
    if rows:
        identity = Path(rows[0]["placement_identity"])
        out.append('<p>' + link(identity, "Frozen tools, inputs and harness identity") + ' · '
                   + link(Ledger(root).path_for(rows[0]["host"]), "Append-only ledger") + '</p>')
    out.append('</html>')
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
