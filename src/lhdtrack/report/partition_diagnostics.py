"""Color experiments: actual region granularity and area against a matched flat run."""
from __future__ import annotations

import json
from pathlib import Path

from ..ledger import Ledger


def partition_rows(history: list[dict], name: str) -> list[dict]:
    selected = {}
    for row in history:
        if row.get("partition_diagnostic") and row["test"] == name and row["kind"] == "synth":
            selected[(row["run_id"], row["flow"])] = row
    return list(selected.values())


def flat_reference(row: dict, history: list[dict]) -> dict | None:
    """Compiler, input, Liberty and constraint identities must all match."""
    refs = []
    for candidate in history:
        if (candidate.get("flow") != "syn_lhd_verilog_abc_sn_flat"
                or candidate.get("status") != "ok"
                or any(row.get(k) != candidate.get(k)
                       for k in ("host_class", "test", "config", "tech"))
                or row.get("versions", {}).get("lhd") != candidate.get("versions", {}).get("lhd")):
            continue
        q, cq = row.get("qor", {}), candidate.get("qor", {})
        if all(q.get(k) and q[k] == cq.get(k)
               for k in ("source_sha256", "liberty_sha256", "sdc_sha256")):
            refs.append(candidate)
    return max(refs, key=lambda r: r["run_id"]) if refs else None



def cell_budget_status(rows: list[dict], history: list[dict]) -> str:
    """A small size target cannot override the measured area constraint."""
    if not rows:
        return "No partition measurements yet."
    latest = max(rows, key=lambda row: row["run_id"])
    version = latest.get("versions", {}).get("lhd")
    current = [row for row in rows if row.get("versions", {}).get("lhd") == version
               and row.get("status") == "ok"]
    paired = [(row, flat_reference(row, history)) for row in current]
    if not any(ref for _, ref in paired):
        return "Matched flat reference pending for the latest measured compiler."
    accepted = [row for row, ref in paired
                if ref and row.get("flow") != "syn_lhd_verilog_abc_sn_flat"
                and row["qor"].get("cells") is not None
                and row["qor"]["cells"] <= ref["qor"]["cells"]]
    if accepted:
        return "A partitioned candidate meets the matched flat cell limit; see coverage below."
    return ("No partitioned candidate meets the matched flat cell limit for the latest "
            "measured compiler. Flat remains the only measured policy within that limit.")


def write_partition_diagnostics(root: Path, host: str, name: str) -> Path:
    from .html import _e, _page

    history = Ledger(root).load(host)
    rows = partition_rows(history, name)
    body = [f'<h1>{_e(name)} · partition policy experiments</h1>',
            '<p><strong>' + _e(cell_budget_status(rows, history)) + '</strong></p>',
            '<p>ASAP7, fixed Brent–Kung/SN arithmetic and full ABC recipe. '
            'Flat references match compiler, source bytes, Liberty and SDC. '
            'More partitions are useful only while mapped cell count stays at or below '
            'the matched flat result. 1,000 cells is a soft size goal; predicted AIG '
            'nodes and word-level nodes are different units. '
            'External diagnostics are excluded from corpus headline averages.</p>',
            '<table><tr><th>Run / policy</th><th>Max / min predicted AIG</th><th>Status</th>'
            '<th>Synthesis (s)</th><th>Cells</th><th>Critical path (ps)</th>'
            '<th>Useful bodies / ≥1k / &lt;1k</th><th>Cell change vs flat</th>'
            '<th>Program</th></tr>']
    for row in rows:
        q, t = row.get("qor", {}), row.get("time_ms", {})
        settings = row.get("partition_settings", {})
        regions = q.get("timing_breakdown", {}).get("regions", [])
        sizes = [r["gates"] for r in regions if "gates" in r and r["gates"] > 0]
        counts = (f'{len(sizes)} / {sum(g >= 1000 for g in sizes)} / '
                  f'{sum(g < 1000 for g in sizes)}' if regions and
                  all("gates" in r for r in regions) else "unrecorded")
        ref = flat_reference(row, history)
        cell_change = "no matched flat reference"
        if row["status"] == "ok" and ref and q.get("cells") is not None:
            delta = 100 * (q["cells"] / ref["qor"]["cells"] - 1)
            cell_change = f'{delta:+.2f}% · ' + ("within cell limit" if delta <= 0 else "reject")
        program = q.get("program_check", {})
        checked = ("passed" if program.get("reference_rc") == 0
                   and program.get("netlist_rc") == 0 else "unchecked")
        if program.get("reference_rc") == 0 and program.get("netlist_rc") not in (None, 0):
            checked = "failed"
        delay = row.get("sta", {}).get("opensta_ns")
        if delay is not None and row.get("sta", {}).get("time_unit") == "ns":
            delay *= 1000
        valid = row["status"] == "ok"
        values = [row["run_id"] + ' / ' + row.get("partition_variant", "")
                  + (" + wiring context"
                     if settings.get("pass.color.synth.boundary_wiring") == "true" else "")
                  + (" + native interfaces"
                     if settings.get("pass.color.synth.native_owners") == "true" else "")
                  + (" + complete register cones"
                     if settings.get("pass.color.synth.flop_to_flop") == "true" else "")
                  + (" + forward off"
                     if settings.get("pass.color.synth.forward") == "false" else ""),
                  settings.get("pass.color.synth.max_gate", "—") + ' / '
                  + settings.get("pass.color.synth.min_gate", "0"), row["status"],
                  f'{sum(t.get(k, 0) for k in ("compile", "color", "map"))/1000:.3f}'
                  + (" · timing limited" if row.get("timing_note") else ""),
                  q.get("cells", "—") if valid else "—",
                  f'{delay:.2f}' if valid and delay is not None else "—",
                  counts, cell_change, checked]
        body.append('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in values) + '</tr>')
    body.append('</table><p>Useful bodies have nonzero mapped gates; zero-gate boundary '
                'scaffolding is excluded. These are backend region sizes before shared '
                'normalization; reused bodies carry physical instance counts in the evidence. '
                'Program checks provide coverage, not equivalence proofs.</p>')
    for row in rows:
        analysis = row.get("qor", {}).get("partition_cut_analysis")
        if not analysis:
            continue
        body.append('<h2>Boundary investigation · ' + _e(row["run_id"]) + '</h2>'
                    '<p>' + _e(analysis["finding"]) + '</p>'
                    '<table><tr><th>Observation</th><th>Cells</th><th>Flops</th>'
                    '<th>Critical path (ps)</th></tr>')
        for observation in analysis["observations"]:
            values = [observation["label"], observation["cells"], observation["flops"],
                      f'{observation["critical_path_ps"]:.2f}']
            body.append('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in values) + '</tr>')
        body.append('</table><p>' + _e(analysis["scope"]) + '</p>'
                    '<p>' + _e(analysis["sharing_finding"]) + '</p>')
        cones = analysis.get("cone_comparison")
        if cones:
            body.append('<h3>Post-mapping cone traversal</h3><p>' + _e(cones["finding"])
                        + '</p><table><tr><th>Register/output group</th><th>Matched bits</th>'
                        '<th>Flat cone cells</th><th>Two-color cone cells</th><th>Change</th>'
                        '<th>Flat / two-color levels</th><th>Same source bits</th></tr>')
            for group in cones["groups"]:
                b, m = group["baseline"], group["measured"]
                values = [group["group"], group["matched_endpoints"], b["cells"], m["cells"],
                          f'{group["cell_delta"]:+d}', f'{b["levels"]} / {m["levels"]}',
                          "yes" if group["same_source_bits"] else "no"]
                body.append('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in values) + '</tr>')
            body.append('</table><p>' + _e(cones["scope"]) + '</p>')
    body.append('<details><summary>Structured measurements, region sizes and provenance'
                '</summary><pre>'
                + _e(json.dumps(rows, indent=2)) + '</pre></details>')
    out = root / 'target' / f'results-syn-{name}-partitions-{host}.html'
    out.parent.mkdir(exist_ok=True)
    out.write_text(_page(f'{name}: partition experiments', ''.join(body)))
    out.with_suffix('.json').write_text(json.dumps(rows, indent=2) + '\n')
    return out
