"""A reproducible Verilog-only mapper evaluation, retaining named baselines."""
from collections import Counter
import json
import math
import re
from pathlib import Path

from ..ledger import Ledger
from .html import _display_lec_verdict, _e, _fmt, _page, _VERDICT_CLASS

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
    selected = {}
    for row in history:
        if row.get("host") != spec["host"] or row.get("tech") != spec["tech"]:
            continue
        key = row["test"], row.get("config", "default"), row["flow"]
        if row["flow"] == "syn_yosys_abc":
            keep = (*key[:2], row["run_id"]) in baselines
        else:
            wanted_run = (spec.get("synth_run_id", spec["run_id"])
                          if row["flow"] in synth_flows(spec) else spec["run_id"])
            keep = row["run_id"] == wanted_run and row["flow"] in synth_flows(spec) | {
                lec_flow(m, enabled) for m in MAPPERS for enabled in spec.get("satopt_profiles", [True])}
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
        q, sta = row.get("qor", {}), row.get("sta", {})
        if row["status"] != "ok":
            cells.append(f'<td class="g {"bad" if row["status"] == "failed" else "muted"}" '
                         f'colspan="6" title="{_e(row.get("note", ""))}">'
                         f'{_e(row["status"])}</td>')
        else:
            note = (' <span class="tag">native state</span>' if q.get("native_state") else '')
            if mapper:
                proof = synth_proof(rows, (test, config), mapper, satopt)
                refuted = any(proof.get(f, {}).get("verdict") == "refuted"
                              for f in ("lec_verilog_result", "lec_aux_result"))
                if refuted:
                    note += ' <span class="tag bad">LEC refuted</span>'
                elif not proof_ok(row, proof):
                    note += ' <span class="tag">LEC unverified</span>'
            date = f' title="measured {_e(row.get("measured", ""))}"'
            vals = metric_values(row)
            cells.extend(f'<td{date}>{_fmt(v)}{note if i == 1 else ""}</td>'
                         for i, v in enumerate(vals))
        if row.get("note") or sta.get("opensta_note"):
            details.append((test, config, flow, row.get("note") or sta["opensta_note"]))
    return '<tr>' + ''.join(cells) + '</tr>'


def _satopt_effect(rows, spec, metrics):
    """pass.satopt=false ÷ pass.satopt=true for the same mapper: &gt;1 means satopt helps."""
    trs = []
    for mapper in MAPPERS:
        cells = ''.join(
            f'<td>{f"{r:.3f}×" if r is not None else "—"} <small class="muted">(n={n})</small></td>'
            for r, n in geomean_ratios(rows, spec["slots"], mapper, True,
                                       baseline=synth_flow(mapper, False)))
        ratio, n, proven = satopt_lec_effect(rows, spec["slots"], mapper)
        lec = (f'<td>{f"{ratio:.3f}×" if ratio is not None else "—"} <small class="muted">(n={n})</small></td>'
               f'<td>{proven[True]} / {proven[False]}</td>')
        trs.append(f'<tr><td class="l">{_e(mapper)}</td>{cells}{lec}</tr>')
    return ('<h2>SAT optimization effect</h2><p>Each ratio is pass.satopt=false ÷ pass.satopt=true '
            'for the same mapper and design, so &gt;1 means satopt improves that metric. '
            'Synthesis columns pair successful rows of both profiles (time and memory are the '
            'whole <code>lhd synth</code>, including satopt itself). LEC time pairs the native cvc5 '
            'check of the same netlist where both profiles proved it; the last column counts '
            'native proofs with / without satopt.</p>'
            '<div class="scroll"><table><thead><tr><th class="l">Mapper</th>'
            + ''.join(f'<th>{m}</th>' for m in metrics)
            + '<th>LEC cvc5 time</th><th>proven on / off</th></tr></thead><tbody>'
            + ''.join(trs) + '</tbody></table></div>')


def write_evaluation(root: Path, path: Path, out: Path | None = None) -> Path:
    spec = json.loads(path.read_text())
    rows = select_rows(Ledger(root).load(spec["host"]), spec)
    apply_logged_evidence(root, rows)
    body = [f'<h1>LiveHD Verilog · ABC and USYN · {_e(spec["tech"])}</h1>',
            f'<p class="sub">Host {_e(spec["host"])} · run {_e(spec["run_id"])} · '
            f'{_e(spec.get("phase", "complete"))}. '
            'Yosys+Slang+ABC synthesis baselines are retained from their recorded dates. '
            'Only the selected LiveHD runs appear below; a blank means not measured. '
            'Pyrope, simulation and other technologies are excluded.</p>',
            f'<p>LiveHD: <code>{_e(spec["versions"]["lhd"])}</code><br>'
            f'Liberty hash: <code>{_e(spec["liberty_sha256"])}</code>. '
            'LEC checks the emitted Verilog netlist against the original Verilog source. '
            'Timeouts and inconclusive answers are coverage outcomes, not refutations.</p>']
    if spec.get("auxiliary_report"):
        body.append(f'<p><a href="{_e(spec["auxiliary_report"])}">Simulation and Sky130 synthesis on this host</a></p>')
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
    counts = Counter((r["flow"], r["status"]) for r in rows.values()
                     if r["flow"] != "syn_yosys_abc")
    body.append('<p>' + '; '.join(
        f'{_e(flow)}: {sum(v for (f, _), v in counts.items() if f == flow)} measured, '
        f'{counts[flow, "failed"]} failed, {counts[flow, "skipped"]} skipped'
        for m in MAPPERS for enabled in spec.get("satopt_profiles", [True])
        for flow in (synth_flow(m, enabled), lec_flow(m, enabled))
    ) + '</p>')

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
        for satopt in profiles:
            synth_rows[satopt].append(_synth_row(rows, test, config, label, satopt, details))

        for satopt in profiles:
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
    if any(row["flow"] in {"syn_yosys_abc", *synth_flows(spec)} for row in rows.values()):
        body.append('<h2>Synthesis</h2><p>Geomeans use Yosys+Slang+ABC ÷ LiveHD: '
                    '&gt;1 is better, &lt;1 is worse for every metric. '
                    'Unverified results are included; refuted netlists are excluded. '
                    'Each metric uses matched, finite, positive measurements and shows its sample count. '
                    'LEC checks the pass.satopt=true emission; pass.satopt=false netlists are unverified.</p>')
        for satopt in profiles:
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
        body.append(_satopt_effect(rows, spec, metrics))
    for satopt in profiles:
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
    details = list(dict.fromkeys(details))
    if details:
        body.append('<h2>Diagnostics</h2><div class="scroll"><table><tbody>' + ''.join(
            f'<tr><td class="l">{_e(t)} / {_e(c)}</td><td>{_e(f)}</td>'
            f'<td class="note">{_e(n)}</td></tr>' for t, c, f, n in details
        ) + '</tbody></table></div>')
    out = out or root / 'target' / f'report-{spec["host"]}.html'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page('lhdtrack — Verilog mapper evaluation', '\n'.join(body)))
    return out
