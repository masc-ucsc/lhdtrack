"""Large external RTL diagnostics, kept separate from corpus averages."""
from __future__ import annotations

import json
from pathlib import Path

from ..ledger import Ledger
from .synthesis_timing import lhd_steps
from .verilog_eval import SN_LHD_FLOW, SN_LHD_LEC, sn_fixed_table


def write_external_run(root: Path, host: str, run: str, name: str) -> Path:
    from .html import _e, _page

    all_history = Ledger(root).load(host)
    history = [r for r in all_history if r.get("run_id") == run]
    rows = {(r["test"], r["config"], r["flow"]): r for r in history}
    spec = {"slots": [{"test": name, "config": "default"}]}
    body = [f"<h1>{_e(name)} · ASAP7 LHD ABC versus ABC/SN</h1>",
            f"<p>Run {_e(run)}. Sequential measurements on {_e(host)}. "
            "External Verilog diagnostic; excluded from corpus headline averages. "
            "Brent–Kung adder, SN multiplier, logarithmic shifts, no arithmetic trials. "
            "Proof time and emission/QoR collection are outside synthesis time.</p>",
            sn_fixed_table(rows, spec)]
    if any(r.get("external_color_diagnostic") and r.get("source_run") == run
           and r["test"] == name for r in all_history):
        body.append(f'<p><a href="results-syn-{_e(name)}-colors-{_e(host)}.html">'
                    'Partitioned versus single core color: time, QoR and coverage</a></p>')
    diagnostic = rows.get((name, "default", "syn_abc_sn"), {}).get("qor", {})
    analysis = diagnostic.get("frontend_analysis", {})
    if analysis:
        body.append('<h2>Frontend findings</h2><ul>' + ''.join(
            f'<li>{_e(note)}</li>' for note in analysis.get("observations", [])) + '</ul>')
        body.append('<p>' + _e(analysis.get("full_design_lec", "")) + '</p>')
        reproducer = analysis.get("reproducer", {})
        body.append('<details><summary>Minimal frontend counterexample</summary><pre>'
                    + _e(reproducer.get("rtl", "")) + '</pre></details>')
    for flow in (SN_LHD_FLOW, "syn_abc_sn", SN_LHD_LEC, "sim_verilator"):
        row = rows.get((name, "default", flow))
        if not row:
            continue
        body.append(f"<h2>{_e(flow)} · {_e(row['status'])}</h2><p>{_e(row.get('note', ''))}</p>")
        program = row.get("qor", {}).get("program_check", {})
        if program:
            passed = program.get("netlist_rc") == 0
            body.append('<p>Architectural program: ' + ('passed' if passed else 'failed')
                        + f' ({program["cycles"]} cycles). A passing program is coverage, '
                        'not an equivalence proof.</p>')
        proofs = [f'{field}: {row[field]["verdict"]}' for field in
                  ("lec_verilog_result", "lec_aux_result")
                  if row.get(field, {}).get("verdict")]
        if proofs:
            body.append('<p>Formal coverage: ' + _e('; '.join(proofs)) + '</p>')
        timing = row.get("qor", {}).get("timing_breakdown", {})
        groups = {"LHD phases": lhd_steps(row)} if flow == SN_LHD_FLOW else {
            "SN frontend": timing.get("frontend_ms", {}),
            "SN mapper": timing.get("mapper_ms", {})}
        body.append('<table><tr><th>Group</th><th>Step</th><th>Seconds</th></tr>')
        for group, steps in groups.items():
            for step, ms in steps.items():
                body.append(f"<tr><td>{_e(group)}</td><td>{_e(step)}</td>"
                            f"<td>{ms/1000:.4f}</td></tr>")
        body.append(
            '</table><details><summary>Structured evidence and proof coverage</summary><pre>'
            + _e(json.dumps(row, indent=2)) + '</pre></details>')
    out = root / "target" / f"results-syn-{name}-{host}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(_page(f"{name}: LHD ABC / SN", "".join(body)))
    out.with_suffix(".json").write_text(json.dumps(history, indent=2) + "\n")
    return out


def write_external_diagnostics(root: Path, host: str) -> list[Path]:
    latest = {}
    colors = {}
    partitions = set()
    for row in Ledger(root).load(host):
        if row.get("large_design_diagnostic"):
            key = row["test"]
            latest[key] = max(latest.get(key, ""), row["run_id"])
        if row.get("external_color_diagnostic"):
            colors[row["test"]] = max(colors.get(row["test"], ""), row["run_id"])
        if row.get("partition_diagnostic"):
            partitions.add(row["test"])
    from .partition_diagnostics import write_partition_diagnostics

    return ([write_external_run(root, host, run, name) for name, run in sorted(latest.items())]
            + [write_external_color_run(root, host, run, name)
               for name, run in sorted(colors.items())]
            + [write_partition_diagnostics(root, host, name) for name in sorted(partitions)])


def write_external_color_run(root: Path, host: str, run: str, name: str) -> Path:
    """Render the separate flat experiment and its exact recorded partitioned baseline."""
    from .html import _e, _page

    history = Ledger(root).load(host)
    current = {r["flow"]: r for r in history
               if r.get("run_id") == run and r["test"] == name}
    source_run = next(iter(current.values()))["source_run"]
    baseline = {r["flow"]: r for r in history
                if r.get("run_id") == source_run and r["test"] == name}
    flat = current.get("syn_lhd_verilog_abc_sn_flat", {})
    partitioned = baseline[SN_LHD_FLOW]
    if flat and (flat["host_class"] != partitioned["host_class"]
                 or flat.get("versions", {}).get("lhd") !=
                 partitioned.get("versions", {}).get("lhd")):
        raise ValueError("flat comparison has incompatible host or compiler")
    if flat.get("status") == "ok" and (
                 flat.get("qor", {}).get("liberty_sha256") !=
                 partitioned.get("qor", {}).get("liberty_sha256")
                 or flat.get("qor", {}).get("source_sha256") !=
                 partitioned.get("qor", {}).get("source_sha256")
                 or flat.get("qor", {}).get("sdc_sha256") !=
                 partitioned.get("qor", {}).get("sdc_sha256")):
        raise ValueError("flat comparison has incompatible Liberty, sources, or constraints")
    period = partitioned.get("sta", {}).get("sdc_period_ns")
    if period is not None and partitioned.get("sta", {}).get("time_unit") == "ns":
        period *= 1000
    full_recipe = flat.get("qor", {}).get("synth_policy", {}).get("pass.abc.large_ge") == "0"
    recipe_note = ('The flat run disables the automatic large-region fast tier so its core '
                   'uses the full ABC recipe, as in the partitioned baseline. '
                   if full_recipe else 'The automatic large-region fast tier remains enabled. ')
    rows = [partitioned, *([flat] if flat else [])]
    body = [f'<h1>{_e(name)} · LHD ABC partitioned versus single core color</h1>',
            f'<p>Baseline {_e(source_run)}; flat run {_e(run)} on {_e(host)}. '
            f'Identical frozen compiler, source bytes, ASAP7 library and {_e(period)} ps constraint. '
            'Fixed Brent–Kung/SN arithmetic, logarithmic shifts, zero arithmetic trials. '
            + recipe_note +
            'Flat coloring includes arithmetic; default memory implementation boundaries '
            'are preserved. Region counts below include these helpers. '
            'External diagnostic, excluded from corpus averages.</p>',
            '<table><tr><th>Flow</th><th>Status</th><th>Synthesis (s)</th>'
            '<th>Reported total (s)</th><th>Pipeline wall (s)</th><th>Critical path (ps)</th>'
            '<th>Cells</th>'
            '<th>Flops</th><th>Regions</th><th>Program</th></tr>']
    for row in rows:
        times, qor, sta = row.get("time_ms", {}), row.get("qor", {}), row.get("sta", {})
        valid = row["status"] == "ok"
        synthesis = sum(times.get(k, 0) for k in ("compile", "color", "map"))
        invocation = qor.get("timing_breakdown", {}).get("synthesis_invocation", {})
        wall = (invocation.get("wall_ms", synthesis) + times.get("total", 0) - synthesis)
        program = qor.get("program_check", {})
        program_label = "unchecked"
        if program.get("reference_rc") == 0 and program.get("netlist_rc") is not None:
            program_label = "passed" if program["netlist_rc"] == 0 else "failed"
        delay = sta.get("opensta_ns")
        if delay is not None and sta.get("time_unit") == "ns":
            delay *= 1000
        cells, flops = qor.get("cells"), qor.get("flops")
        values = [row["flow"], row["status"], f'{synthesis/1000:.3f}',
                  f'{times.get("total", 0)/1000:.3f}',
                  f'{wall/1000:.3f}',
                  f'{delay:.2f}' if valid and delay is not None else "—",
                  cells if valid else "—", flops if valid else "—",
                  len(qor.get("timing_breakdown", {}).get("regions", [])), program_label]
        body.append('<tr>' + ''.join(f'<td>{_e(v)}</td>' for v in values) + '</tr>')
    body.append('</table><p>Synthesis is compile + color + pass.abc, including memory lowering. '
                'Reported total adds emission, normalization, area collection and OpenSTA; '
                'internal OpenTimer and equivalence/program checks are outside it. '
                'Pipeline wall restores internal OpenTimer and process overhead. '
                'Passing the architectural program supplies coverage, not a formal proof.</p>')
    proof_row = current.get("lec_netlist_verilog_abc_sn_flat", {})
    proofs = [f'{field}: {proof_row[field]["verdict"]}'
              for field in ("lec_verilog_result", "lec_aux_result")
              if proof_row.get(field, {}).get("verdict")]
    if proofs:
        body.append('<p>Flat netlist formal coverage: ' + _e('; '.join(proofs)) + '</p>')
    for row in rows:
        body.append(f'<h2>{_e(row["flow"])}</h2><table><tr><th>Compiler phase</th>'
                    '<th>Seconds</th></tr>')
        for step, ms in lhd_steps(row).items():
            body.append(f'<tr><td>{_e(step)}</td><td>{ms/1000:.4f}</td></tr>')
        body.append('</table>')
    evidence = {"baseline_run": source_run, "flat_run": run,
                "baseline": list(baseline.values()), "flat": list(current.values())}
    body.append('<details><summary>Exact measurement and proof evidence</summary><pre>'
                + _e(json.dumps(evidence, indent=2)) + '</pre></details>')
    out = root / "target" / f"results-syn-{name}-colors-{host}.html"
    out.parent.mkdir(exist_ok=True)
    out.write_text(_page(f"{name}: LHD color comparison", ''.join(body)))
    out.with_suffix(".json").write_text(json.dumps(evidence, indent=2) + "\n")
    return out
