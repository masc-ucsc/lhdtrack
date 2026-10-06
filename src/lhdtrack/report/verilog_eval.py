"""A reproducible Verilog-only mapper evaluation, retaining named baselines."""
from collections import Counter, defaultdict
import json
import math
import re
from pathlib import Path

from ..ledger import Ledger, slug
from .html import (
    _auxiliary_report_name, _chart_block, _coverage_plot, _coverage_verdict,
    _display_lec_verdict, _e, _fmt,
    _language_lec_section, _netlist_lec_section, _page, _results_nav, _sim_table,
    _synth_display_values, _synth_scope_tag,
    _VERDICT_CLASS, RESULT_KINDS,
)

MAPPERS = ("abc", "usyn")


def synth_flow(mapper, satopt=True):
    return "syn_lhd_verilog" + ("_usyn" if mapper == "usyn" else "") + ("" if satopt else "_no_satopt")


def synth_flows(spec):
    return {synth_flow(m, e) for m in MAPPERS for e in spec.get("satopt_profiles", [True])}


def lec_flow(mapper, satopt=True):
    return "lec_netlist_verilog" + ("_usyn" if mapper == "usyn" else "") + ("" if satopt else "_no_satopt")


def select_rows(history, spec):
    """Do not let old LiveHD slots leak into a partial or failed rerun."""
    baselines = {(r["test"], r["config"], r["run_id"]) for r in spec["baselines"]}
    overrides = {(r["test"], r["config"], r["flow"]): r["run_id"]
                 for r in spec.get("proof_overrides", [])}
    selected = {}
    for row in history:
        if row.get("host") != spec["host"] or row.get("tech") != spec["tech"]:
            continue
        key = row["test"], row.get("config", "default"), row["flow"]
        if row["flow"] == "syn_yosys_abc":
            keep = (*key[:2], row["run_id"]) in baselines
        else:
            wanted_run = (spec.get("synth_run_id", spec["run_id"])
                          if row["flow"] in synth_flows(spec) else overrides.get(key, spec["run_id"]))
            keep = row["run_id"] == wanted_run and row["flow"] in synth_flows(spec) | {
                lec_flow(m, enabled) for m in MAPPERS for enabled in spec.get("satopt_profiles", [True])}
        if keep and key in overrides and row["flow"] not in synth_flows(spec):
            keep = row.get("versions") == spec["versions"]
        if keep:
            selected[key] = row
    return selected


def bounded_yosys_evidence(block, summary, sat_log, errors):
    """Accept only a completed bounded proof; preserve every counterexample."""
    if block.get("verdict") not in ("inconclusive", "timeout", "unknown"):
        return block
    if ("TIMEOUT: bounded miter exhausted the shared equivalence budget" in summary
            and "model found: FAIL" not in sat_log and "ERROR" not in errors
            and "FAIL: circuits are not equivalent" not in summary):
        return {**block, "verdict": "timeout", "bounded": False, "bound": None,
                "reason": "Independent Yosys exhausted its shared equivalence budget."}
    match = re.search(r"BMC: found no counterexample within (\d+) steps", summary)
    if (not match or "model found: FAIL" in sat_log or "ERROR" in errors
            or "FAIL: circuits are not equivalent" in summary):
        return block
    bound = int(match[1])
    if bound <= 0 or sat_log.count("no model found: SUCCESS") != bound:
        return block
    return {**block, "verdict": "proven", "bounded": True, "bound": bound,
            "reason": f"Independent Yosys SAT completed all {bound} bounded checks."}


def apply_logged_evidence(root, rows):
    """Project retained proof evidence without rewriting historical measurements."""
    for key, row in list(rows.items()):
        if row["flow"] not in {lec_flow(m) for m in MAPPERS}:
            continue
        work = (root / "var/work" / row["run_id"] / row["test"]
                / row.get("config", "default") / row["tech"] / row["flow"])
        oracle = work / "LW-lec_lgyosys_verilog_netlist"
        try:
            summary = "\n".join(p.read_text() for p in (oracle / "logs").glob("*lgcheck*"))
            updated = bounded_yosys_evidence(
                row.get("lec_aux_result", {}), summary,
                (oracle / "lgcheck_bmc.log").read_text(),
                (oracle / "lgcheck_bmc.err").read_text())
        except OSError:
            continue
        if updated != row.get("lec_aux_result", {}):
            rows[key] = {**row, "lec_aux_result": updated}


def verify_transparent_instance_renaming(original, emitted):
    """Only cgen's anonymous-instance spelling may change; reject logic edits."""
    declarations = re.findall(r"(?m)^\s*([A-Za-z_$][\w$]*)[ \t]+(u_[\w$]+)[ \t]*\(", original)
    fresh_declarations = set(re.findall(r"(?m)^\s*([A-Za-z_$][\w$]*)[ \t]+(__flat___[\w$]+)[ \t]*\(", emitted))
    renames = {name: "__flat___" + name[2:] for module, name in declarations
               if (module, "__flat___" + name[2:]) in fresh_declarations}
    # An already-updated emission needs no migration. Avoid an empty regex.
    if not renames:
        if original != emitted:
            raise ValueError("re-emission changed more than transparent instance names")
        return 0
    pattern = r"(?<![\w$])(?:" + "|".join(re.escape(k) for k in sorted(renames, key=len, reverse=True)) + r")(?![\w$])"
    expected = re.sub(pattern, lambda m: renames[m[0]], original)
    if expected != emitted:
        raise ValueError("re-emission changed more than transparent instance names; retained QoR cannot be transferred")
    return sum(name in renames for _, name in declarations)


def proof_covers_digest(block, digest):
    if block.get("netlist_sha256") == digest:
        return True
    emission = block.get("emission", {})
    return (emission.get("kind") == "transparent_instance_renaming"
            and emission.get("source_netlist_sha256") == digest
            and bool(emission.get("netlist_sha256"))
            and emission["netlist_sha256"] == block.get("netlist_sha256"))


def proof_ok(synth, proof):
    """A proof, including bounded proof, must cover these exact emitted bytes."""
    digest = synth.get("qor", {}).get("netlist_sha256")
    blocks = [proof.get(f, {}) for f in ("lec_verilog_result", "lec_aux_result")]
    if any(b.get("verdict") in ("refuted", "error") for b in blocks):
        return False
    return bool(digest) and any(
        b.get("verdict") == "proven"
        and proof_covers_digest(b, digest) for b in blocks
    )


def metric_values(row):
    q, sta = row.get("qor", {}), row.get("sta", {})
    ms = row.get("time_ms", {}).get("total")
    kb = row.get("peak_rss_kb", {}).get("max")
    return (sta.get("opensta_ns"), q.get("area_um2"), q.get("cells"),
            q.get("logic_depth"), ms / 1000 if ms is not None else None,
            kb / 1024 if kb is not None else None)


def synth_proof(rows, key, mapper, satopt):
    """Use the proof of this profile's exact emitted netlist."""
    return rows.get((*key, lec_flow(mapper, satopt)), {})


def geomean_ratios(rows, slots, mapper, satopt=True, baseline=None):
    """Paired baseline / measured ratios; retain unverified, exclude refuted."""
    logs = [[] for _ in range(6)]
    for slot in slots:
        key = slot["test"], slot["config"]
        base = rows.get((*key, baseline or "syn_yosys_abc"), {})
        measured = rows.get((*key, synth_flow(mapper, satopt)), {})
        proof = synth_proof(rows, key, mapper, satopt)
        if base.get("status") != "ok" or measured.get("status") != "ok":
            continue
        if any(proof.get(f, {}).get("verdict") == "refuted"
               for f in ("lec_verilog_result", "lec_aux_result")):
            continue
        for samples, b, m in zip(logs, metric_values(base), metric_values(measured)):
            if all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (b, m)):
                samples.append(math.log(b) - math.log(m))
    return [(math.exp(math.fsum(v) / len(v)) if v else None, len(v)) for v in logs]


def lec_time_geomean(rows, slots, mapper, *, exclude_timeouts=False, satopt=True):
    """Paired Yosys / cvc5 elapsed times for the same mapped netlist."""
    logs = []
    for slot in slots:
        row = rows.get((slot["test"], slot["config"], lec_flow(mapper, satopt)), {})
        native = row.get("lec_verilog_result", {})
        yosys = row.get("lec_aux_result", {})
        if any(b.get("verdict") == "refuted" for b in (native, yosys)):
            continue
        if exclude_timeouts and any(b.get("verdict") == "timeout" for b in (native, yosys)):
            continue
        n, y = native.get("ms"), yosys.get("ms")
        if all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (n, y)):
            logs.append(math.log(y) - math.log(n))
    return (math.exp(math.fsum(logs) / len(logs)) if logs else None, len(logs))


def satopt_lec_effect(rows, slots, mapper):
    """Paired cvc5 time with satopt=false / satopt=true on the same netlist, both proven."""
    logs, proven = [], {True: 0, False: 0}
    for slot in slots:
        key = slot["test"], slot["config"]
        blocks = {e: rows.get((*key, lec_flow(mapper, e)), {}).get("lec_verilog_result", {})
                  for e in (True, False)}
        for e, block in blocks.items():
            proven[e] += block.get("verdict") == "proven"
        if all(b.get("verdict") == "proven" for b in blocks.values()):
            off, on = blocks[False].get("ms"), blocks[True].get("ms")
            if all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (off, on)):
                logs.append(math.log(off) - math.log(on))
    ratio = math.exp(math.fsum(logs) / len(logs)) if logs else None
    return ratio, len(logs), proven


def _synth_row(rows, test, config, label, satopt, details):
    cells = [label]
    for mapper, flow in ((None, "syn_yosys_abc"), *[(m, synth_flow(m, satopt)) for m in MAPPERS]):
        row = rows.get((test, config, flow), {})
        if not row:
            cells.append('<td class="g muted" colspan="6">—</td>')
            continue
        qor, sta = row.get("qor", {}), row.get("sta", {})
        proof = synth_proof(rows, (test, config), mapper, satopt) if mapper else {}
        refuted = any(proof.get(f, {}).get("verdict") == "refuted"
                      for f in ("lec_verilog_result", "lec_aux_result"))
        has_measurement = (sta.get("opensta_ns") is not None
                           or any(qor.get(f) is not None
                                  for f in ("area_um2", "cells", "lhd_area_um2", "lhd_cells")))
        if refuted:
            cells.append('<td class="g bad" colspan="6">LEC refuted · results excluded</td>')
        elif row["status"] != "ok" and not has_measurement:
            cells.append(f'<td class="g {"bad" if row["status"] == "failed" else "muted"}" '
                         f'colspan="6" title="{_e(row.get("note", ""))}">'
                         f'{_e(row["status"])}</td>')
        else:
            note = ''
            if row["status"] != "ok":
                note += f' <span class="tag">{_e(row["status"])}</span>'
            if mapper and not proof_ok(row, proof):
                note += ' <span class="tag">LEC unverified</span>'
            date = f' title="measured {_e(row.get("measured", ""))}"'
            values, scopes = _synth_display_values(row)
            cells.extend(f'<td{date}>{_fmt(value)}{_synth_scope_tag(scope)}'
                         f'{note if i == 1 else ""}</td>'
                         for i, (value, scope) in enumerate(zip(values, scopes)))
        if row.get("note") or sta.get("opensta_note"):
            details.append((test, config, flow, row.get("note") or sta["opensta_note"]))
    return '<tr>' + ''.join(cells) + '</tr>'


def _satopt_effect(rows, spec, metrics, kind=None):
    """pass.satopt=false ÷ pass.satopt=true for the same mapper: &gt;1 means satopt helps."""
    trs = []
    for mapper in MAPPERS:
        cells = '' if kind == "lec" else ''.join(
            f'<td>{f"{r:.3f}×" if r is not None else "—"} <small class="muted">(n={n})</small></td>'
            for r, n in geomean_ratios(rows, spec["slots"], mapper, True,
                                       baseline=synth_flow(mapper, False)))
        ratio, n, proven = satopt_lec_effect(rows, spec["slots"], mapper)
        lec = (f'<td>{f"{ratio:.3f}×" if ratio is not None else "—"} <small class="muted">(n={n})</small></td>'
               f'<td>{proven[True]} / {proven[False]}</td>')
        trs.append(f'<tr><td class="l">{_e(mapper)}</td>{cells}'
                   f'{lec if kind != "synth" else ""}</tr>')
    return ('<h2>SAT optimization effect</h2><p>Each ratio is pass.satopt=false ÷ pass.satopt=true '
            'for the same mapper and design, so &gt;1 means satopt improves that metric. '
            'Synthesis columns pair successful rows of both profiles (time and memory are the '
            'whole <code>lhd synth</code>, including satopt itself). LEC time pairs the native cvc5 '
            'check of the same netlist where both profiles proved it; the last column counts '
            'native proofs with / without satopt.</p>'
            '<div class="scroll"><table><thead><tr><th class="l">Mapper</th>'
            + (''.join(f'<th>{m}</th>' for m in metrics) if kind != "lec" else '')
            + ('<th>LEC cvc5 time</th><th>proven on / off</th>' if kind != "synth" else '')
            + '</tr></thead><tbody>'
            + ''.join(trs) + '</tbody></table></div>')


def simulation_section(history: list[dict], host: str, report_cfg: dict) -> str:
    """Show latest host-local simulation gates without mixing them into synthesis."""
    latest = {}
    for row in sorted(history, key=lambda r: r.get("run_id", "")):
        if (row.get("host") == host and row.get("kind") == "sim"
                and not row.get("flow", "").startswith("sim_retained_usyn_netlist")):
            latest[row["test"], row.get("config", "default"), row["flow"]] = row
    if not latest:
        return ""
    index = defaultdict(dict)
    for row in latest.values():
        key = row["suite"], row["test"], row.get("config", "default"), None
        index[key][row["flow"]] = row
    keys = sorted(index, key=lambda k: (k[1], k[2]))
    runs = sorted({r["run_id"] for r in latest.values()})
    counts = Counter(r.get("status", "unknown") for r in latest.values())
    intro = ('<h2>Simulation · latest results on this host</h2><p class="sub">'
             f'Host {_e(host)} · runs {_e(runs[0])}–{_e(runs[-1])} · '
             f'{len(latest)} observations: {_e(json.dumps(dict(counts), sort_keys=True))}. '
             'These are separately recorded source-simulation measurements, not simulations '
             'of the mapped netlists above. Checksum failures remain visible and are excluded '
             'from speed geomeans. Cached baselines retain their recorded measurement dates.</p>')
    return intro + _sim_table(None, keys, index,
                             report_cfg.get("baseline_sim_flow", "sim_verilator"),
                             set(report_cfg.get("headline_pyrope_status", ["idiomatic"])))



def retained_usyn_validation_section(history, spec, kind=None):
    """Only checksum/proof observations tied to this exact synthesis run."""
    source = spec.get("synth_run_id", spec["run_id"])
    latest = {}
    for row in history:
        if row.get("flow") not in {"lec_retained_usyn_pyrope", "sim_retained_usyn_netlist",
                                    "sim_retained_usyn_netlist_verilator"}:
            continue
        if kind is not None and row.get("kind") != kind:
            continue
        evidence = row.get("lec_result") or row.get("sim") or {}
        if (row.get("host") != spec["host"] or row.get("tech") != spec["tech"]
                or row.get("source_usyn_run", evidence.get("source_run")) != source):
            continue
        latest[row["test"], row.get("config", "default"), row["flow"]] = row
    label = {"sim": "simulation", "lec": "Pyrope equivalence"}.get(
        kind, "Pyrope and simulation")
    title = f'<h2>USYN exact-netlist {label} checks</h2>'
    if not latest:
        return (title + '<p>No checks recorded yet for the retained USYN netlists '
                'in this synthesis run.</p>')
    summaries = []
    for flow in sorted({r["flow"] for r in latest.values()}):
        group = [r for r in latest.values() if r["flow"] == flow]
        outcomes = Counter((r.get("lec_result", {}).get("verdict", r.get("status", "unknown"))
                            if flow == "lec_retained_usyn_pyrope" else r.get("status", "unknown"))
                           for r in group)
        bounded = sum(r.get("lec_result", {}).get("verdict") == "proven"
                      and bool(r.get("lec_result", {}).get("bounded")) for r in group)
        summaries.append(f'{_e(flow)}: {_e(dict(outcomes))}'
                         + (f' ({bounded} proofs bounded)' if bounded else ''))
    body = []
    for (test, config, flow), row in sorted(latest.items()):
        if flow == "lec_retained_usyn_pyrope":
            block = row.get("lec_result", {})
            verdict = block.get("verdict", row.get("status", "unknown"))
            detail = (f'bounded at {block.get("bound")} steps' if block.get("bounded") else '')
            detail += f' {block.get("ms", 0) / 1000:.3f}s'
        else:
            sim = row.get("sim", {})
            verdict = row.get("status", "unknown")
            detail = f'{sim.get("cycles", "—")} cycles; checksum {sim.get("checksum", "—")}'
        detail += ' ' + row.get("note", "")
        body.append(f'<tr><td class="l">{_e(test)} / {_e(config)}</td><td>{_e(flow)}</td>'
                    f'<td>{_e(verdict)}</td><td class="note">{_e(detail)}</td></tr>')
    return (title + f'<p>Exact retained emissions from {_e(source)}; ' + "; ".join(summaries) + ". "
            'Simulation uses the existing manifest cycle counts and recorded checksums. '
            'These checks consume the measured netlist bytes; they do not resynthesize. '
            'Skips, refutations, timeouts and checksum failures remain visible.</p>'
            '<div class="scroll"><table><thead><tr><th>Test / config</th><th>Check</th>'
            '<th>Result</th><th>Evidence</th></tr></thead><tbody>'
            + ''.join(body) + '</tbody></table></div>')


def mapper_chart_data(rows, spec, satopt):
    """Plot the same full-metric pairs as the synthesis geomean table."""
    metrics = [("delay", "Delay", spec["time_unit"]), ("area", "Area", "µm²"),
               ("cells", "Cells", ""), ("depth", "Logic depth", "cells"),
               ("time", "Tool runtime", "s"), ("mem", "Peak memory", "MiB")]
    groups = []
    for slot in spec["slots"]:
        key = slot["test"], slot["config"]
        base = rows.get((*key, "syn_yosys_abc"), {})
        if base.get("status") != "ok":
            continue
        values, verified = {}, True
        for mapper in MAPPERS:
            flow = synth_flow(mapper, satopt)
            measured = rows.get((*key, flow), {})
            proof = synth_proof(rows, key, mapper, satopt)
            if (measured.get("status") != "ok" or
                    any(proof.get(f, {}).get("verdict") == "refuted"
                        for f in ("lec_verilog_result", "lec_aux_result"))):
                continue
            verified = verified and proof_ok(measured, proof)
            for (metric, _, _), b, m in zip(metrics, metric_values(base), metric_values(measured)):
                if all(isinstance(v, (int, float)) and math.isfinite(v) and v > 0 for v in (b, m)):
                    values.setdefault(metric, {})[flow] = b / m
        if values:
            groups.append({"test": key[0], "config": key[1], "solid": verified, "values": values})
    if not groups:
        return None
    return {
        "baseline": "Yosys+Slang+ABC",
        "flows": [{"key": synth_flow(m, satopt), "label": m.upper(), "series": c}
                  for m, c in zip(MAPPERS, ("a", "c"))],
        "metrics": [{"key": k, "label": label, "unit": unit} for k, label, unit in metrics],
        "sortFlow": synth_flow("usyn", satopt), "sortLabel": "USYN",
        "solidNote": "faded = exact-netlist proof incomplete",
        "groups": groups,
    }


def logic_gate_section(rows, spec, history=()):
    """Compare physical combinational cells with the selected LiveHD ABC run."""
    source = spec.get("synth_run_id", spec["run_id"])
    pyrope_proofs = {}
    for row in history:
        if (row.get("flow") == "lec_retained_usyn_pyrope"
                and row.get("source_usyn_run") == source
                and row.get("host") == spec["host"] and row.get("tech") == spec["tech"]):
            pyrope_proofs[row["test"], row.get("config", "default")] = row
    sections = []
    for satopt in spec.get("satopt_profiles", [True]):
        body, headline, diagnostic, measured_samples = [], [], [], []
        for slot in spec["slots"]:
            key = slot["test"], slot["config"]
            base = rows.get((*key, synth_flow("abc", satopt)), {})
            measured = rows.get((*key, synth_flow("usyn", satopt)), {})
            b = base.get("qor", {}).get("lhd_cells")
            m = measured.get("qor", {}).get("lhd_cells")
            comparable = (base.get("status") == measured.get("status") == "ok"
                          and base.get("host_class") == measured.get("host_class")
                          and base.get("liberty_sha256") == measured.get("liberty_sha256")
                          and all(isinstance(v, (int, float)) and math.isfinite(v)
                                  and v > 0 for v in (b, m)))
            proofs = [synth_proof(rows, key, mapper, satopt) for mapper in MAPPERS]
            refuted = any(p.get(f, {}).get("verdict") == "refuted" for p in proofs
                          for f in ("lec_verilog_result", "lec_aux_result"))
            ratio = b / m if comparable and not refuted else None
            if ratio is not None:
                measured_samples.append(math.log(ratio))
            proven = comparable and not refuted and all(proof_ok(s, p)
                                        for s, p in zip((base, measured), proofs))
            if proven:
                diagnostic.append(math.log(ratio))
                pyrope = pyrope_proofs.get(key, {})
                block = pyrope.get("lec_result", {})
                digest = measured.get("qor", {}).get("netlist_sha256")
                if (measured.get("pyrope_status") == "idiomatic"
                        and pyrope.get("status") == "ok" and block.get("verdict") == "proven"
                        and digest and proof_covers_digest(block, digest)):
                    headline.append(math.log(ratio))
            evidence = measured.get("qor", {}).get("usyn_evidence", {})
            trials = evidence.get("mapping_trials", [])
            trial_text = "; ".join(
                f'{"selected " if t.get("selected") else ""}'
                f'{"choices" if t.get("multi_rep") else "incumbent"}: '
                f'{t.get("physical_logic_gates", "—")} gates'
                for t in trials)
            status = "both exact netlists proven" if proven else "proof coverage incomplete"
            if refuted:
                status = "LEC refuted · results excluded"
                b = m = None
            elif not comparable:
                status = "; ".join(f'{name}: {row.get("status", "pending")} '
                                   f'{row.get("note", "")}'
                                   for name, row in (("abc", base), ("usyn", measured)))
            body.append(f'<tr><td class="l">{_e(key[0])} / {_e(key[1])}</td>'
                        f'<td>{_fmt(b)}</td><td>{_fmt(m)}</td>'
                        f'<td>{_fmt(ratio)}×</td><td class="note">{_e(status)}</td>'
                        f'<td class="note">{_e(trial_text)}</td></tr>')
        def aggregate(samples):
            return (f'{math.exp(math.fsum(samples) / len(samples)):.3f}× '
                    f'(n={len(samples)})') if samples else 'no eligible pairs'
        profile = str(satopt).lower()
        sections.append(f'<h2>LiveHD ABC / USYN logic gates · pass.satopt={profile}</h2>'
                        '<p>Physical combinational library cells, including buffers and inverters; '
                        'register cells are excluded. Ratios above one favor USYN. '
                        'Each comparison uses this host and the identical Liberty hash. '
                        'All measured non-refuted pairs, including timeouts and unverified results: '
                        f'{aggregate(measured_samples)}. '
                        'Headline, idiomatic Pyrope proved against the exact USYN emission '
                        'and both '
                        'Verilog emissions proven: ' f'{aggregate(headline)}. Diagnostic, '
                        'all source-language statuses with both exact emissions proven: '
                        f'{aggregate(diagnostic)}. '
                        'Unproven pairs enter the all-results aggregate; proof-covered aggregates '
                        'remain separate. Refuted results are excluded.</p>'
                        '<div class="scroll"><table><thead><tr><th class="l">Test / config</th>'
                        '<th>ABC gates</th><th>USYN gates</th><th>ABC / USYN</th>'
                        '<th>Coverage</th><th>Bounded mapped trials</th></tr></thead><tbody>'
                        + ''.join(body) + '</tbody></table></div>')
    return ''.join(sections)


def usyn2_progress_section(history, rows, spec):
    """Retain the explicitly selected pre-USYN2 cohort and remaining gate gaps."""
    run = spec.get("usyn2_baseline_run")
    if not run:
        return ""
    baseline = {(r["test"], r.get("config", "default")): r for r in history
                if r.get("run_id") == run and r.get("host") == spec["host"]
                and r.get("tech") == spec["tech"]
                and r.get("flow") == synth_flow("usyn", False)}
    body = []
    for key, old in sorted(baseline.items()):
        current = rows.get((*key, synth_flow("usyn", False)), {})
        if (old.get("status") != "ok" or current.get("status") != "ok"
                or old.get("host_class") != current.get("host_class")
                or old.get("liberty_sha256") != current.get("liberty_sha256")):
            continue
        before, after = (r.get("qor", {}).get("lhd_cells") for r in (old, current))
        if not all(isinstance(n, (int, float)) and math.isfinite(n) and n > 0
                   for n in (before, after)):
            continue
        body.append(f'<tr><td class="l">{_e(key[0])} / {_e(key[1])}</td>'
                    f'<td>{_fmt(before)}</td><td>{_fmt(after)}</td>'
                    f'<td>{100 * (1 - after / before):.1f}%</td></tr>')
    gaps = []
    for slot in spec["slots"]:
        key = slot["test"], slot["config"]
        abc = rows.get((*key, synth_flow("abc", False)), {})
        usyn = rows.get((*key, synth_flow("usyn", False)), {})
        if (abc.get("status") != "ok" or usyn.get("status") != "ok"
                or abc.get("host_class") != usyn.get("host_class")
                or abc.get("liberty_sha256") != usyn.get("liberty_sha256")):
            continue
        a, u = (r.get("qor", {}).get("lhd_cells") for r in (abc, usyn))
        if not all(isinstance(n, (int, float)) and math.isfinite(n) and n > 0
                   for n in (a, u)) or a / u >= .8:
            continue
        evidence = usyn.get("qor", {}).get("usyn_evidence", {})
        eligible = evidence.get("totals", {}).get("eligible_endpoints", "—")
        limits = evidence.get("search_exhausted_regions", "—")
        if key[0] == "br_multi_xfer_reg_fwd":
            diagnosis = ("The mapped choice trial is larger, so the incumbent is retained. "
                         "Investigate bus-wide enable/data sharing and physical proxy ranking.")
        elif key[0] in {"comparator", "icmp"}:
            diagnosis = ("Several related signed/unsigned order and equality outputs. "
                         "Hypothesis: common wide roots escape local choice windows; "
                         "investigate canonical relation sharing.")
        elif key[0] in {"br_arb_rr", "br_flow_arb_rr"}:
            diagnosis = ("Choice cleanup reduces the incumbent, but bounded state/predicate "
                         "factoring still leaves a gap. Library-cell ranking alone did not "
                         "improve the arbiter ablation; investigate larger shared predicates.")
        else:
            diagnosis = ("Combinational control remains under local output/cut optimization. "
                         "Investigate shared prefix/decode factoring beyond individual roots.")
        gaps.append((a / u, '<tr><td class="l">' + _e(key[0] + '/' + key[1])
                     + f'</td><td>{a / u:.3f}×</td><td>{_e(eligible)}</td>'
                     + f'<td>{_e(limits)}</td><td class="note">{_e(diagnosis)}</td></tr>'))
    return (f'<h2>USYN2 progress from retained cohort {_e(run)}</h2>'
            '<p>Matched host, technology and Liberty; physical combinational gates. '
            'These are individual diagnostic cases, not a language headline aggregate. '
            'The baseline measurements and all rejected ablations remain in the ledger.</p>'
            '<div class="scroll"><table><thead><tr><th class="l">Case</th>'
            '<th>Previous USYN</th><th>Current USYN</th><th>Gate reduction</th>'
            '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>'
            '<h3>Every remaining measured ABC / USYN gate ratio below 0.8</h3>'
            '<p>Unknown proof outcomes remain visible in the coverage tables. '
            'Search limits are reported bounds, not proof failures. Suggestions below '
            'are bounded follow-up hypotheses, not claims of optimality.</p>'
            '<div class="scroll"><table><thead><tr><th class="l">Case</th><th>Ratio</th>'
            '<th>Eligible state endpoints</th><th>Bounded regions</th><th>Diagnosis / next step</th>'
            '</tr></thead><tbody>' + ''.join(row for _, row in sorted(gaps))
            + '</tbody></table></div>')


def write_evaluation(
    root: Path, path: Path, out: Path | None = None, kind: str | None = None,
) -> Path:
    if kind not in (None, "synth", "lec"):
        raise ValueError(f"unknown evaluation kind: {kind}")
    spec = json.loads(path.read_text())
    legacy_out = root / "target" / f'report-{slug(spec["host"])}.html'
    if kind is None and (out is None or out == legacy_out):
        from ..cli import load_config
        from .html import write_report

        cfg = load_config(root)
        primary = write_evaluation(root, path, kind="synth")
        write_evaluation(root, path, kind="lec")
        write_report(root, cfg=cfg, host=spec["host"], kind="sim")
        if auxiliary := spec.get("auxiliary_report"):
            name = _auxiliary_report_name(spec["host"], auxiliary)
            write_report(root, out=root / "target" / name, cfg=cfg, host=spec["host"],
                         prefer_evaluation=False, kind="synth")
        return primary
    show_syn, show_lec = kind in (None, "synth"), kind in (None, "lec")
    history = Ledger(root).load(spec["host"])
    rows = select_rows(history, spec)
    apply_logged_evidence(root, rows)
    page_label = RESULT_KINDS[kind][1] if kind else "Verilog mapper evaluation"
    body = [_results_nav(spec["host"], kind),
            f'<h1>{page_label} · LiveHD Verilog · ABC and USYN · {_e(spec["tech"])}</h1>',
            f'<p class="sub">Host {_e(spec["host"])} · run {_e(spec["run_id"])} · '
            f'{_e(spec.get("phase", "complete"))}. '
            'Yosys+Slang+ABC synthesis baselines are retained from their recorded dates. '
            'Only the selected LiveHD runs appear below; a blank means not measured. '
            'The synthesis comparison uses Verilog for this technology. Source-simulation '
            'results have their own page; Pyrope and other-technology synthesis '
            'remain in the linked full report.</p>',
            f'<p>LiveHD: <code>{_e(spec["versions"]["lhd"])}</code><br>'
            f'Liberty hash: <code>{_e(spec["liberty_sha256"])}</code>. '
            'LEC checks the emitted Verilog netlist against the original Verilog source. '
            'Timeouts and inconclusive answers are coverage outcomes, not refutations.</p>']
    if spec.get("auxiliary_report"):
        auxiliary = _auxiliary_report_name(spec["host"], spec["auxiliary_report"])
        body.append(f'<p><a href="{_e(auxiliary)}">'
                    'Other technologies and Pyrope: full report</a></p>')
    if spec.get("reemit_logical_hierarchy"):
        body.append('<p>LEC rerun: mapped graphs were re-emitted using transparent wrapper names. '
                    'Each emission was checked byte-for-byte after instance renaming only; synthesis/QoR '
                    'measurements remain from the retained run. Both checkers consume the new emission.</p>')
    if spec.get("diagnostic_report"):
        body.append(f'<p><a href="{_e(spec["diagnostic_report"])}">LEC performance investigation: names, hierarchy, and satopt</a></p>')
    if spec.get("synth_run_id"):
        body.append(f'<p>Synthesis artifact source: {_e(spec.get("synth_host", spec["host"]))} / {_e(spec["synth_run_id"])} · '
                    f'<code>{_e(spec["synth_versions"]["lhd"])}</code>. '
                    'LEC uses the binary above; proof records link the checked emission hash to the retained synthesis artifact. '
                    'Synthesis timings from other hosts are not included.</p>')
    if spec.get("proof_worker_epochs"):
        epochs = spec["proof_worker_epochs"]
        before = epochs[0].get("completed_rows", 0)
        first, last = epochs[0]["workers"], epochs[-1]["workers"]
        body.append(f'<p>Proof concurrency: {first} workers for {before} recorded rows, '
                    f'then {last} workers for the remaining checks. '
                    'Interrupted, unrecorded attempts are archived and rerun in fresh workdirs. '
                    'All solver and wall limits are unchanged. Gate counts are independent '
                    'of this scheduling change; timing comparisons retain the recorded context.</p>')
    if spec.get("proof_overrides"):
        corrections = "; ".join(
            f'{r["test"]}/{r["config"]}: {r["flow"]} from {r["run_id"]}'
            for r in spec["proof_overrides"])
        body.append(f'<p>Focused proof corrections: {_e(corrections)}. '
                    'They use the checker versions above and retain the measured netlist hashes.</p>')
    counts = Counter((r["flow"], r["status"]) for r in rows.values()
                     if r["flow"] != "syn_yosys_abc" and
                     (kind is None or r.get("kind") == kind))
    body.append('<p>' + '; '.join(
        f'{_e(flow)}: {sum(v for (f, _), v in counts.items() if f == flow)} measured, '
        f'{counts[flow, "failed"]} failed, {counts[flow, "skipped"]} skipped'
        for m in MAPPERS for enabled in spec.get("satopt_profiles", [True])
        for flow in (synth_flow(m, enabled), lec_flow(m, enabled))
        if kind is None or (flow.startswith("syn_") if kind == "synth" else flow.startswith("lec_"))
    ) + '</p>')

    if show_lec:
        for mapper in MAPPERS:
            for enabled in spec.get("satopt_profiles", [True]):
                group = [r for r in rows.values() if r["flow"] == lec_flow(mapper, enabled)]
                summaries = []
                for name, field in (("native CVC5", "lec_verilog_result"),
                                    ("independent lgcheck", "lec_aux_result")):
                    outcomes = Counter(r.get(field, {}).get("verdict", r.get("status", "unknown"))
                                       for r in group)
                    bounded = sum(r.get(field, {}).get("verdict") == "proven"
                                  and bool(r.get(field, {}).get("bounded")) for r in group)
                    summaries.append(f'{name}: {_e(dict(outcomes))}'
                                     + (f' ({bounded} proofs bounded)' if bounded else ''))
                body.append(f'<p>{_e(mapper)} · pass.satopt={str(enabled).lower()} · '
                            + '; '.join(summaries) + '.</p>')

    head = ('<tr><th class="l" rowspan="3">Test / config</th>'
            '<th colspan="6" rowspan="2">yosys+slang+abc (retained)</th>'
            '<th class="g" colspan="12">lhd-verilog</th></tr>'
            '<tr><th class="g" colspan="6">abc</th>'
            f'<th class="g" colspan="6">usyn · {_e(spec.get("usyn_tmap", spec.get("usyn_abc", "unknown")))}</th></tr><tr>')
    metrics = (f'delay {_e(spec["time_unit"])}', 'area µm²', 'cells', 'depth',
               'time s', 'mem MiB')
    head += ''.join('<th>' + m + '</th>' for _ in range(3) for m in metrics) + '</tr>'
    profiles = spec.get("satopt_profiles", [True])
    details = []
    synth_rows = {enabled: [] for enabled in profiles}
    lec_rows = {enabled: [] for enabled in profiles}
    for slot in spec["slots"]:
        test, config = slot["test"], slot["config"]
        label = f'<td class="l">{_e(test)} <small class="muted">{_e(config)}</small></td>'
        for satopt in profiles if show_syn else []:
            synth_rows[satopt].append(_synth_row(rows, test, config, label, satopt, details))

        for satopt in profiles if show_lec else []:
            cells = [label]
            for mapper in MAPPERS:
                row = rows.get((test, config, lec_flow(mapper, satopt)), {})
                for field in ("lec_verilog_result", "lec_aux_result"):
                    block = row.get(field, {})
                    verdict = _display_lec_verdict(block) or row.get("status", "—")
                    reason = block.get("counterexample") or block.get("reason") or row.get("note", "")
                    ms = block.get("ms")
                    phase_ms = block.get("phase_ms", {})
                    timing = "; ".join(f"{name}: {value / 1000:.3f}s" for name, value in phase_ms.items())
                    verdict_class = _VERDICT_CLASS.get(block.get("verdict", verdict), "muted")
                    cells.append(f'<td class="g {verdict_class}" '
                                 f'title="{_e(reason)}">{_e(verdict)}</td>'
                                 f'<td title="{_e(timing)}">{_fmt(ms / 1000) if ms is not None else "—"}</td>')
                    if reason:
                        details.append((test, config, f'{mapper}/satopt={str(satopt).lower()}/{block.get("solver", field)}', reason))
            lec_rows[satopt].append('<tr>' + ''.join(cells) + '</tr>')
    if show_syn and any(row["flow"] in {"syn_yosys_abc", *synth_flows(spec)}
                        for row in rows.values()):
        body.append('<h2>Synthesis</h2><p>Geomeans use Yosys+Slang+ABC ÷ LiveHD: '
                    '&gt;1 is better, &lt;1 is worse for every metric. '
                    'Unverified results are included; refuted netlists are excluded. '
                    'Metrics tagged logic show mapped combinational area or cells; region tags show '
                    'the maximum mapped region delay. Preserved state/memory and whole-design STA '
                    'are not included in these partial metrics. All recorded values are displayed; '
                    'whole-design geomeans use compatible full measurements and show their sample counts. '
                    'Each selected compile SAT profile checks its own exact emitted netlist.</p>')
        for satopt in profiles:
            chart = mapper_chart_data(rows, spec, satopt)
            if chart:
                body.append(_chart_block(f"chart-eval-syn-{str(satopt).lower()}", chart))
            footer = ['<tfoot><tr><th class="l">Geomean vs Yosys+Slang+ABC</th>',
                      '<td colspan="6">1.000× baseline</td>']
            for mapper in MAPPERS:
                for ratio, count in geomean_ratios(rows, spec["slots"], mapper, satopt):
                    value = f'{ratio:.3f}×' if ratio is not None else '—'
                    footer.append(f'<td>{value} <small class="muted">(n={count})</small></td>')
            footer.append('</tr></tfoot>')
            body.append(f'<h3>pass.satopt={str(satopt).lower()}</h3>'
                        '<div class="scroll"><table><thead>' + head +
                        '</thead><tbody>' + ''.join(synth_rows[satopt]) + '</tbody>'
                        + ''.join(footer) + '</table></div>')
    if set(profiles) == {True, False}:
        body.append(_satopt_effect(rows, spec, metrics, kind=kind))
    for satopt in profiles if show_lec else []:
        groups = []
        for mapper in MAPPERS:
            for name, field in (("cvc5", "lec_verilog_result"), ("Yosys", "lec_aux_result")):
                outcomes = Counter()
                for slot in spec["slots"]:
                    row = rows.get((slot["test"], slot["config"], lec_flow(mapper, satopt)), {})
                    outcomes[_coverage_verdict(row.get(field, {}),
                                               row.get("status", "not-measured"))] += 1
                groups.append((f"{mapper} · satopt={str(satopt).lower()} · {name}", outcomes))
        body.append('<h2>Verdict coverage · pass.satopt=' + str(satopt).lower() + '</h2>'
                    '<p class="sub">Share of selected test/config slots per checker. '
                    'Hover for counts; bounded proofs are shown separately. '
                    'Missing and skipped checks remain in the denominator.</p>'
                    + _coverage_plot(groups))
        lec_footer = ['<tfoot>']
        for exclude_timeouts, label in ((False, "including timeouts"), (True, "excluding timeouts")):
            lec_footer.append(f'<tr><th class="l">Time geomean vs Yosys · {label}</th>')
            for mapper in MAPPERS:
                ratio, count = lec_time_geomean(rows, spec["slots"], mapper,
                                                exclude_timeouts=exclude_timeouts, satopt=satopt)
                value = f'{ratio:.3f}×' if ratio is not None else '—'
                baseline = '1.000×' if count else '—'
                lec_footer.append(
                    f'<td>—</td><td>{value} <small class="muted">(n={count})</small></td>'
                    f'<td>—</td><td>{baseline} <small class="muted">(n={count})</small></td>')
            lec_footer.append('</tr>')
        lec_footer.append('</tfoot>')
        body.append(f'<h2>LEC · emitted netlist vs original Verilog · pass.satopt={str(satopt).lower()}</h2>'
                    + ('<p>Both times start with prepared Verilog inputs and include loading, compilation and proof. '
                       'Shared Liberty model generation and Verilog export are setup, excluded from both times. '
                       if spec.get("timing_scope") == "prepared-verilog-inputs" else
                       '<p>Legacy timing includes library-model export in native LEC, but not in the independent oracle. ')
                    + 'Hover over a native time for phase timings. '
                    'The switch controls native input compilation; lgcheck checks the same netlist independently. '
                    'Each mapper has independent cvc5 and Yosys results. '
                    'Direct Yosys checks report standalone lgcheck time; older cross-driver results include its native precheck. '
                    'A bounded proof counts as proven and is shown with its checked bound. '
                    'Refutations remain visible, including informational multi-clock checks. '
                    'Time geomeans use paired Yosys ÷ cvc5 elapsed times for each mapper: '
                    '&gt;1 means cvc5 is faster, &lt;1 means slower; Yosys is 1.000×. '
                    'Timeout and unverified runs are included using their recorded elapsed times, '
                    'not estimated completion times; refuted cases are excluded. '
                    'The second row excludes any pair where either checker timed out. '
                    'Only matched, finite, positive times contribute, with the sample count shown.</p>'
                    '<div class="scroll"><table><thead><tr><th rowspan="3" class="l">Test / config</th>'
                    '<th colspan="8">lhd-verilog</th></tr><tr><th colspan="4">abc</th>'
                    '<th colspan="4">usyn</th></tr><tr>' +
                    ''.join('<th>cvc5</th><th>time s</th><th>Yosys</th><th>time s</th>' for _ in MAPPERS) +
                    '</tr></thead><tbody>' + ''.join(lec_rows[satopt]) + '</tbody>' + ''.join(lec_footer) + '</table></div>')
    if show_syn:
        body.append(logic_gate_section(rows, spec, history))
        body.append(usyn2_progress_section(history, rows, spec))
    if show_lec:
        body.append(retained_usyn_validation_section(history, spec, kind=kind))
    if kind == "lec":
        latest = Ledger(root).latest_rows(spec["host"])
        body.append(_language_lec_section(latest))
        body.append(_netlist_lec_section(latest))
    details = list(dict.fromkeys(details))
    if details:
        body.append('<h2>Diagnostics</h2><div class="scroll"><table><tbody>' + ''.join(
            f'<tr><td class="l">{_e(t)} / {_e(c)}</td><td>{_e(f)}</td>'
            f'<td class="note">{_e(n)}</td></tr>' for t, c, f, n in details
        ) + '</tbody></table></div>')
    prefix = f"results-{RESULT_KINDS[kind][0]}" if kind else "report"
    out = out or root / "target" / f"{prefix}-{slug(spec["host"])}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page(f"lhdtrack — {page_label} — {spec['host']}", '\n'.join(body)))
    return out
