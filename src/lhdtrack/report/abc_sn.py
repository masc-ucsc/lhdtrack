"""Outliers from one complete, explicitly named ABC/SN comparison run."""
from __future__ import annotations

from collections import Counter
import json
import math
from pathlib import Path

from ..ledger import Ledger, slug
from .verilog_eval import SN_LHD_FLOW, SN_LHD_LEC, sn_compatible, synthesis_time_ms

FLOWS = (SN_LHD_FLOW, "syn_abc_sn")
METRICS = ("synthesis_s", "path_ps", "cells")


def latest_full_run(history: list[dict], expected: set[tuple[str, str]]) -> str | None:
    """A focused rerun must not replace the full-regression outlier population."""
    runs = {}
    for row in history:
        if row.get("tech") == "asap7" and row.get("flow") in FLOWS:
            slot = row["test"], row.get("config", "default")
            runs.setdefault(row["run_id"], set()).add((*slot, row["flow"]))
    required = {(*slot, flow) for slot in expected for flow in FLOWS}
    complete = [run for run, slots in runs.items() if required and required <= slots]
    return max(complete) if complete else None


def proof_verdict(row: dict, history: list[dict]) -> str:
    """Borrow SN proof only for identical netlist, library and source bytes."""
    q = row.get("qor", {})
    digest = q.get("netlist_sha256")
    if not digest:
        return "unverified"
    verdicts = []
    for candidate in history:
        if any(candidate.get(k) != row.get(k) for k in ("host", "tech", "test", "config")):
            continue
        if row["flow"] == "syn_abc_sn":
            cq = candidate.get("qor", {})
            if candidate.get("flow") != row["flow"] or any(
                    cq.get(k) != q.get(k) for k in
                    ("netlist_sha256", "liberty_sha256", "source_sha256")):
                continue
            if candidate.get("versions", {}).get("lhd") != row.get("versions", {}).get("lhd"):
                continue
        elif (candidate.get("flow") != SN_LHD_LEC
              or candidate.get("run_id") != row.get("run_id")):
            continue
        for field in ("lec_verilog_result", "lec_aux_result"):
            block = candidate.get(field, {})
            if (block.get("netlist_sha256") == digest
                    and block.get("liberty_sha256") == q.get("liberty_sha256")):
                verdicts.append(block)
    for verdict in ("refuted", "error"):
        if any(b.get("verdict") == verdict for b in verdicts):
            return verdict
    proven = [b for b in verdicts if b.get("verdict") == "proven"]
    if proven:
        if any(not b.get("bounded") for b in proven):
            return "proven"
        bounds = [b["bound"] for b in proven if isinstance(b.get("bound"), int)]
        return f"bounded({max(bounds)})" if bounds else "bounded"
    for verdict in ("timeout", "inconclusive"):
        if any(b.get("verdict") == verdict for b in verdicts):
            return verdict
    return "unverified"


def analyze(history: list[dict], host: str, run: str) -> dict:
    rows = [r for r in history if r.get("host") == host and r.get("run_id") == run
            and r.get("tech") == "asap7" and r.get("flow") in FLOWS]
    slots = {}
    for row in rows:
        slots.setdefault((row["test"], row.get("config", "default")), {})[row["flow"]] = row
    pairs = []
    for (test, config), entries in sorted(slots.items()):
        pair = {"test": test, "config": config, "reason": "", "ratios": {}}
        measured = [entries.get(flow, {}) for flow in FLOWS]
        for name, row in zip(("lhd", "sn"), measured):
            q = row.get("qor", {})
            ms = synthesis_time_ms(row)
            pair[name] = {
                "status": row.get("status", "not measured"), "note": row.get("note", ""),
                "proof": proof_verdict(row, history) if row else "unverified",
                "synthesis_s": ms / 1000 if ms is not None else None,
                "path_ps": row.get("sta", {}).get("opensta_ns"), "cells": q.get("cells"),
                "total_s": row.get("time_ms", {}).get("total", 0) / 1000,
                "flops": q.get("flops"),
                "arithmetic_trials": q.get("arithmetic_trials"),
                "timing_breakdown": q.get("timing_breakdown", {}),
                "netlist_sha256": q.get("netlist_sha256"),
            }
        if any(p["status"] != "ok" for p in (pair["lhd"], pair["sn"])):
            pair["reason"] = "failed, skipped or missing synthesis"
        elif any(p["proof"] in ("refuted", "error") for p in (pair["lhd"], pair["sn"])):
            pair["reason"] = "netlist proof refuted or errored"
        elif not sn_compatible(measured[1], measured[0]):
            pair["reason"] = "host class or Liberty mismatch"
        elif (measured[0].get("qor", {}).get("sdc_sha256")
              != measured[1].get("qor", {}).get("sdc_sha256") or any(
                  measured[0].get("qor", {}).get("source_sha256", {}).get(path) != digest
                  for path, digest in measured[1].get("qor", {}).get("source_sha256", {}).items())):
            pair["reason"] = "SDC or source mismatch"
        else:
            for metric in METRICS:
                a, b = pair["lhd"][metric], pair["sn"][metric]
                if all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0
                       for v in (a, b)):
                    pair["ratios"][metric] = b / a
        pairs.append(pair)
    statistics = {}
    for metric in METRICS:
        ratios = [p["ratios"][metric] for p in pairs if metric in p["ratios"]]
        statistics[metric] = {
            "n": len(ratios),
            "sn_over_lhd_geomean": math.exp(math.fsum(map(math.log, ratios)) / len(ratios))
            if ratios else None,
            "lhd_wins_5pct": sum(r >= 1.05 for r in ratios),
            "sn_wins_5pct": sum(r <= 1 / 1.05 for r in ratios),
            "within_5pct": sum(1 / 1.05 < r < 1.05 for r in ratios),
        }
    return {"host": host, "run_id": run, "tech": "asap7", "slots": len(slots),
            "versions": rows[0].get("versions", {}) if rows else {},
            "synth_policies": [json.loads(p) for p in sorted({
                json.dumps(r.get("qor", {}).get("synth_policy", {}), sort_keys=True)
                for r in rows if r["flow"] == SN_LHD_FLOW})],
            "counts": {flow: dict(Counter(r["status"] for r in rows if r["flow"] == flow))
                       for flow in FLOWS},
            "proof_counts": {name: dict(Counter(p[name]["proof"] for p in pairs))
                             for name in ("lhd", "sn")},
            "exclusions": dict(Counter(p["reason"] for p in pairs if p["reason"])),
            "statistics": statistics, "pairs": pairs}


def write_outliers(root: Path, host: str, run: str) -> tuple[Path, Path]:
    from .html import _e, _page

    history = Ledger(root).load(host)
    report = analyze(history, host, run)
    if not report["slots"]:
        raise ValueError(f"no ASAP7 comparison measurements in {run}")
    body = [f'<h1>ABC-SN / fixed Brent-Kung LHD ABC outliers</h1><p>Run: {_e(run)}; '
            f'{report["slots"]} test/config slots. '
            f'<a href="results-syn-{slug(host)}.html">Synthesis results</a></p>',
            '<p>Ratios are ABC-SN ÷ LHD: above 1 favors LHD for every metric. '
            'Synthesis excludes emission and QoR collection; path is the OpenSTA period '
            'in ASAP7 ps; cells include flops. Full sweeps may run jobs in parallel; '
            'timing outliers require sequential confirmation. Fixed arithmetic choices '
            'are recorded below for this run, with zero trials. Most pairs are unverified. '
            'Known counterexamples are excluded, including proofs of identical retained bytes '
            'from earlier runs. These all-Verilog statistics are diagnostic, separate from '
            'the idiomatic, proven Pyrope headline.</p>',
            '<details><summary>Versions / coverage / paired statistics</summary><pre>' +
            _e(json.dumps({k: report[k] for k in
                           ("versions", "synth_policies", "counts", "proof_counts", "exclusions", "statistics")},
                          indent=2)) + '</pre></details>']

    def table(pairs: list[dict]) -> str:
        out = ['<div class="scroll"><table><thead><tr><th class="l">Test/config</th>']
        for title in ("Synthesis s", "Path ps", "Cells"):
            out.extend(f'<th>{title} · {label}</th>' for label in ("LHD", "SN", "SN/LHD"))
        out.append('<th>Flops · LHD / SN</th><th>LEC · LHD / SN</th></tr></thead><tbody>')
        for p in pairs:
            out.append(f'<tr><td class="l">{_e(p["test"] + "/" + p["config"])}</td>')
            for metric in METRICS:
                for name in ("lhd", "sn"):
                    value = p[name][metric]
                    out.append(f'<td>{value:.3f}</td>' if value is not None else '<td>—</td>')
                ratio = p["ratios"].get(metric)
                out.append(f'<td>{ratio:.3f}×</td>' if ratio else '<td>—</td>')
            out.append(f'<td>{p["lhd"].get("flops", "—")} / '
                       f'{p["sn"].get("flops", "—")}</td>')
            out.append(f'<td>{_e(p["lhd"]["proof"])} / {_e(p["sn"]["proof"])}</td></tr>')
        return ''.join(out) + '</tbody></table></div>'

    for metric, label in zip(METRICS, ("Synthesis time", "Critical path", "Mapped cells")):
        eligible = [p for p in report["pairs"] if metric in p["ratios"]]
        ordered = sorted(eligible, key=lambda p: p["ratios"][metric])
        body.append(f'<h2>{label} · largest SN advantages</h2>' + table(ordered[:10]))
        body.append(f'<h2>{label} · largest LHD advantages</h2>' + table(ordered[::-1][:10]))
    from .synthesis_timing import profile_pairs

    by_slot = {(p["test"], p["config"]): p for p in report["pairs"]}
    confirmations = []
    for lhd, sn in profile_pairs(history, "brent"):
        base = by_slot.get((lhd["test"], lhd["config"]))
        if (not base or lhd["run_id"] <= run or lhd.get("status") != "ok"
                or sn.get("status") != "ok" or not sn_compatible(sn, lhd)
                or lhd.get("versions") != report["versions"]):
            continue
        a, b = synthesis_time_ms(lhd), synthesis_time_ms(sn)
        if not a or not b or "synthesis_s" not in base["ratios"]:
            continue
        same = (lhd["qor"].get("netlist_sha256") == base["lhd"]["netlist_sha256"]
                and sn["qor"].get("netlist_sha256") == base["sn"]["netlist_sha256"])
        confirmations.append({"test": lhd["test"], "config": lhd["config"],
                              "run_id": lhd["run_id"], "lhd_s": a / 1000, "sn_s": b / 1000,
                              "sn_over_lhd": b / a, "identical_netlists": same})
    report["sequential_confirmations"] = confirmations
    if confirmations:
        body.append('<h2>Sequential timing confirmations</h2><p>One measurement at a time, '
                    'with the full sweep’s tool versions. Proofs and full-run rankings above '
                    'remain attached to their measured bytes.</p><div class="scroll"><table>'
                    '<thead><tr><th class="l">Test/config</th><th>LHD s</th><th>SN s</th>'
                    '<th>SN/LHD</th><th>Identical netlists</th></tr></thead><tbody>')
        for c in confirmations:
            body.append(f'<tr><td class="l">{_e(c["test"] + "/" + c["config"])}</td>'
                        f'<td>{c["lhd_s"]:.3f}</td><td>{c["sn_s"]:.3f}</td>'
                        f'<td>{c["sn_over_lhd"]:.3f}×</td>'
                        f'<td>{"yes" if c["identical_netlists"] else "no"}</td></tr>')
        body.append('</tbody></table></div>')
    body.append('<h2>Failures, skips and excluded pairs</h2><div class="scroll"><table>'
                '<thead><tr><th class="l">Test/config</th><th class="l">Reason</th>'
                '<th class="l">LHD</th><th class="l">SN</th></tr></thead><tbody>')
    for p in report["pairs"]:
        if not p["reason"]:
            continue
        body.append('<tr><td class="l">' + _e(p['test'] + '/' + p['config']) + '</td><td>' +
                    _e(p['reason']) + '</td>')
        for name in ("lhd", "sn"):
            status = p[name]['status'] + ' · LEC ' + p[name]['proof']
            body.append(f'<td class="note">{_e(status)}<br>{_e(p[name]["note"])}</td>')
        body.append('</tr>')
    body.append('</tbody></table></div><h2>All eligible pairs</h2>' +
                table([p for p in report["pairs"] if p["ratios"]]))
    target = root / "target"
    target.mkdir(exist_ok=True)
    out = target / f"results-syn-outliers-{slug(host)}.html"
    data = out.with_suffix(".json")
    data.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    out.write_text(_page("ABC-SN / LHD ABC outliers", ''.join(body)))
    return out, data


def write_latest_outliers(root: Path, host: str) -> tuple[Path, Path] | None:
    from ..corpus import discover

    expected = {(test.name, config.id) for test in discover(root) for config in test.configs}
    run = latest_full_run(Ledger(root).load(host), expected)
    return write_outliers(root, host, run) if run else None
