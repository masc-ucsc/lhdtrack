#!/usr/bin/env python3
"""Retain and render a USYN timing investigation with independent proof outcomes."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import _e, _fmt
from lhdtrack.report.usyn_ablation import frequency_ratio, render, timing_ratio, unbounded_proof
from lhdtrack.report.verilog_eval import lec_flow, synth_flow
from lhdtrack.toolchain import Toolchain


def path_summary(log: Path) -> dict:
    """Read OpenSTA's full path, retaining drive, load and cell-delay evidence."""
    gates = []
    text = log.read_text()
    pattern = (r"\s*(\d+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)"
               r"\s+[\^v]\s+(.*)\(([^)]+)\)\s*$")
    for line in text.splitlines():
        match = re.match(pattern, line)
        if match and match[7] != "in":
            gates.append(dict(fanout=int(match[1]), cap=float(match[2]), slew=float(match[3]),
                              delay=float(match[4]), arrival=float(match[5]),
                              pin=match[6].strip(), cell=match[7]))
    names = {k: re.search(rf"(?m)^{k}: (.*)$", text) for k in ("Startpoint", "Endpoint")}
    if not all(names.values()):
        raise ValueError(f"OpenSTA did not report a complete critical path: {log}")
    return dict(log=str(log), log_sha256=hashlib.sha256(log.read_bytes()).hexdigest(),
                startpoint=names["Startpoint"][1] if names["Startpoint"] else None,
                endpoint=names["Endpoint"][1] if names["Endpoint"] else None,
                cells=len(gates), gates=gates,
                largest_delays=sorted(gates, key=lambda g: g["delay"], reverse=True)[:3])


def collect_path(row: dict, variant: str, dest: Path, tc: Toolchain) -> dict:
    synth = row["variants"][variant]["synthesis"]
    work = (ROOT / "var/work" / synth["run_id"] / row["test"] / row["config"]
            / synth["tech"] / synth["flow"])
    dest = dest.resolve()
    dest.mkdir(parents=True, exist_ok=True)
    script = (work / "opensta.tcl").read_text()
    script = script.replace("-format short", "-format full_clock_expanded")
    script = script.replace("-digits 4", "-digits 4 -fields {slew cap fanout input_pin net}")
    command_file = dest / "path.tcl"
    command_file.write_text(script)
    log = dest / "path.log"
    with log.open("w") as stream:
        subprocess.run([str(tc.bin("sta")), "-no_init", "-exit", str(command_file)],
                       cwd=dest, env={**os.environ, **tc.env_for(tc.bin("sta"))},
                       stdout=stream, stderr=subprocess.STDOUT, check=True, timeout=60)
    return path_summary(log)


def diagnose(row: dict, paths: dict) -> dict:
    """Describe measured paths; architecture explanations remain explicit hypotheses."""
    test = row["test"]
    arithmetic = ("add_mul", "mul", "fma", "dot_product", "alpha_blend", "sqr", "carry_save")
    if test.startswith(arithmetic):
        category = "partial-product arithmetic"
        cause = ("Serial partial-product carry propagation in the previous native lowering; "
                 "the candidate compresses rows with carry-save rounds and one prefix addition.")
        next_step = "If still slow, inspect fused arithmetic and signed partial-product sharing."
    elif test in {"comparator", "icmp", "sub"}:
        category = "comparison/subtraction"
        cause = ("The previous native lowering propagates carry through a subtraction. "
                 "Automatic comparisons of at least 8 bits now use a balanced order tree; "
                 "narrow subtraction retains ripple carry.")
        next_step = "Compare explicit adder=prefix against its larger area for narrow subtraction."
    elif any(word in test for word in ("mux", "demux", "shift")):
        category = "decode/mux fanout"
        cause = ("Decode/select distribution and mapped drive strengths contribute to this path; "
                 "mapping-only USYN lacks ABC's global Boolean restructuring.")
        next_step = "Try native phase sharing and fanout-aware decode/mux factoring."
    else:
        category = "control/predicate logic"
        cause = ("Native predicate/state factoring and mapped cell drive contribute to this path. "
                 "The candidate repairs mapping timing misses using cell sizing only.")
        next_step = "Try larger native associative windows and arrival-aware endpoint factoring."
    observations = []
    for name, path in paths.items():
        observations.append(f"{name}: {path['cells']} mapped cells on the critical path")
        if path["largest_delays"]:
            g = path["largest_delays"][0]
            observations.append(f"{g['cell']} contributes {g['delay']:.1f} library time units "
                                f"at fanout {g['fanout']} and load {g['cap']:.3f}")
    evidence = row["variants"]["candidate"]["synthesis"].get("qor", {}).get("usyn_evidence", {})
    eligible = evidence.get("totals", {}).get("eligible_endpoints")
    observations.append(f"{eligible} eligible register-rooted endpoints in definition regions")
    if eligible == 0:
        cause += (" Register-rooted selection has no eligible endpoints in this design; "
                  "combinational-output optimization is limited to the residual stage.")
        next_step += " Explore native combinational-output optimization beyond local residual cuts."
    elif evidence.get("search_exhausted_regions"):
        observations.append("Native search exhausted at least one configured region/window limit")
    return dict(category=category, explanation=cause, observations=observations,
                next_step=next_step, native_evidence=evidence, paths=paths)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--baseline", type=Path)
    parser.add_argument("--from-summary", type=Path,
                        help="render committed evidence without local state")
    parser.add_argument("--candidate", help="completed retained run ID")
    parser.add_argument("--implementation-revision", help="commit matching frozen producer code")
    parser.add_argument("--audit", type=Path, action="append", default=[])
    parser.add_argument("--extra-run", action="append", default=[])
    parser.add_argument("--normalization-audit", type=Path)
    parser.add_argument("--paths", type=Path)
    parser.add_argument("--collect-paths", action="store_true")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.from_summary:
        write_report(json.loads(args.from_summary.read_text()), args.output or args.from_summary)
        return
    if not all((args.baseline, args.candidate, args.paths, args.output)):
        parser.error("provide --baseline, --candidate, --paths and --output, or --from-summary")
    base = json.loads(args.baseline.read_text())
    spec = json.loads((ROOT / "var/runs" / args.candidate / "evaluation.json").read_text())
    history = Ledger(ROOT).load(spec["host"])
    current = {(r["test"], r["config"], r["flow"]): r for r in history
               if r["run_id"] == args.candidate}
    matrix = []
    for row in base["rows"]:
        key = row["test"], row["config"]
        entry = dict(test=key[0], config=key[1], variants={})
        abc = row["variants"]["abc"]
        for name in ("abc", "previous", "candidate"):
            if name == "candidate":
                synth = current.get((*key, synth_flow("usyn", False)), {})
                proof = current.get((*key, lec_flow("usyn", False)), {})
            else:
                old = row["variants"]["abc" if name == "abc" else "improved"]
                synth, proof = old["synthesis"], old["proof"]
            entry["variants"][name] = dict(
                synthesis=synth, proof=proof, unbounded_proven=unbounded_proof(synth, proof),
                frequency_ratio=frequency_ratio(abc["synthesis"], synth, abc["proof"], proof),
                measured_frequency_ratio=timing_ratio(abc["synthesis"], synth))
        entry["investigate"] = any(
            v["measured_frequency_ratio"] is not None and v["measured_frequency_ratio"] < .8
            for n, v in entry["variants"].items() if n != "abc")
        matrix.append(entry)
    counts, headline, common = {}, {}, {}
    common_rows = [r for r in matrix if all(v["frequency_ratio"] is not None
                   and v["synthesis"].get("comparable") for v in r["variants"].values())]
    for name in ("abc", "previous", "candidate"):
        counts[name] = dict(Counter(r["variants"][name]["synthesis"].get("status", "pending")
                                   for r in matrix))
        for dest, rows in ((headline, matrix), (common, common_rows)):
            samples = [math.log(r["variants"][name]["frequency_ratio"]) for r in rows
                       if r["variants"][name]["frequency_ratio"] is not None
                       and r["variants"][name]["synthesis"].get("comparable")]
            dest[name] = dict(count=len(samples), frequency_ratio=
                              math.exp(math.fsum(samples)/len(samples)) if samples else None)
    tc = Toolchain.load(ROOT)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        for row in matrix:
            if not row["investigate"]:
                continue
            jobs = {}
            for name, v in row["variants"].items():
                if not v["synthesis"].get("sta", {}).get("opensta_ns"):
                    continue
                dest = args.paths / row["test"] / row["config"] / name
                if args.collect_paths:
                    jobs[name] = pool.submit(collect_path, row, name, dest, tc)
                elif (dest / "path.log").exists():
                    jobs[name] = pool.submit(path_summary, dest / "path.log")
            row["diagnosis"] = diagnose(row, {n: j.result() for n, j in jobs.items()})
    audits = []
    for p in args.audit:
        rows = json.loads(p.read_text())
        audits.append(dict(path=str(p), sha256=hashlib.sha256(p.read_bytes()).hexdigest(),
                           counts=dict(Counter(b["verdict"] + ("-bounded" if b.get("bounded")
                                               else "") for b in rows)), rows=rows))
    extra = {run: [r for r in history if r["run_id"] == run] for run in args.extra_run}
    comparisons = {}
    for run, observations in extra.items():
        selected = {(r["test"], r["config"], r["flow"]): r for r in observations}
        paired = []
        for row in matrix:
            key = row["test"], row["config"]
            synth = selected.get((*key, synth_flow("usyn", False)))
            if not synth:
                continue
            proof = selected.get((*key, lec_flow("usyn", False)), {})
            abc, current = row["variants"]["abc"], row["variants"]["candidate"]
            areas = (current["synthesis"].get("qor", {}).get("area_um2"),
                     synth.get("qor", {}).get("area_um2"))
            paired.append(dict(test=key[0], config=key[1],
                               measured_frequency_ratio=timing_ratio(abc["synthesis"], synth),
                               frequency_ratio=frequency_ratio(abc["synthesis"], synth,
                                                               abc["proof"], proof),
                               area_ratio_to_candidate=areas[1]/areas[0] if all(areas) else None,
                               unchanged_netlist=bool(synth.get("qor", {}).get("netlist_sha256"))
                               and synth["qor"]["netlist_sha256"]
                               == current["synthesis"].get("qor", {}).get("netlist_sha256"),
                               proof=proof))
        extra_spec = json.loads((ROOT / "var/runs" / run / "evaluation.json").read_text())
        comparisons[run] = dict(spec=extra_spec,
                                rows=paired)
    for row in matrix:
        if not row["investigate"]:
            continue
        for run, experiment in comparisons.items():
            settings = experiment["spec"].get("lhd_settings", "")
            item = next((r for r in experiment["rows"]
                         if (r["test"], r["config"]) == (row["test"], row["config"])), None)
            if not item or item["measured_frequency_ratio"] is None:
                continue
            notes = row["diagnosis"]["observations"]
            if "pass.usyn.flatten=true" in settings and item["unchanged_netlist"]:
                notes.append("Explicit flattening produced the identical emitted netlist.")
            if "pass.usyn.adder=prefix" in settings and row["test"] == "sub":
                area = item["area_ratio_to_candidate"]
                notes.append(f"Explicit prefix subtraction reaches an ABC frequency ratio of "
                             f"{item['measured_frequency_ratio']:.3f} at "
                             f"{(area - 1) * 100:.1f}% more area.")
                row["diagnosis"]["next_step"] = (
                    "Choose explicit prefix for this area/timing tradeoff, or explore a "
                    "smaller native prefix network before changing the narrow default.")
            if "pass.usyn.delay=50" in settings:
                default = row["variants"]["candidate"]["measured_frequency_ratio"]
                if default is None:
                    continue
                gain = item["measured_frequency_ratio"] / default
                area = item["area_ratio_to_candidate"]
                if item["unchanged_netlist"]:
                    notes.append("The tighter 50-unit physical target produced identical cells; "
                                 "this trial exposes no additional sizing headroom.")
                elif gain > 1.1 and area is not None:
                    notes.append(f"The separate 50-unit physical target improves frequency by "
                                 f"{(gain - 1) * 100:.1f}% at {(area - 1) * 100:.1f}% more area; "
                                 "physical cell drive is a demonstrated contributor to this gap.")
                else:
                    notes.append(f"The separate 50-unit target changes frequency by "
                                 f"{(gain - 1) * 100:.1f}%; native logic depth remains "
                                 "a hypothesis for further investigation.")
    summary = dict(schema_version=1, implementation_revision=args.implementation_revision,
                   runs=dict(abc=base["runs"]["abc"],
                   previous=base["runs"]["improved"], candidate=spec), rows=matrix,
                   counts=counts, headline=headline, common_headline=common,
                   independent_audits=audits, additional_experiments=extra,
                   normalization_audit=json.loads(args.normalization_audit.read_text())
                   if args.normalization_audit else [],
                   additional_comparisons=comparisons,
                   baseline_sha256=hashlib.sha256(args.baseline.read_bytes()).hexdigest(),
                   baseline_artifact=str(args.baseline),
                   candidate_toolchain=json.loads((ROOT / "var/runs" / args.candidate
                                                   / "toolchain.json").read_text()))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + "\n")
    write_report(summary, args.output)


def write_report(summary: dict, output: Path) -> None:
    target = ROOT / "target" / (output.stem + ".html")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_followup(summary))
    print(target)
    print(json.dumps(summary["common_headline"], sort_keys=True))


def render_followup(summary: dict) -> str:
    body = '<h2>Every measured ratio below 0.8</h2><p>Measured ratios without an unbounded '
    body += 'exact-netlist proof are provisional diagnostics, excluded from headline geomeans.</p>'
    body += '<table><tr><th>Case</th><th>Previous / candidate measured ratio</th>'
    body += '<th>Candidate lgcheck</th><th>Critical-path evidence and next step</th></tr>'
    for row in summary["rows"]:
        if not row["investigate"]:
            continue
        vs, diagnosis = row["variants"], row["diagnosis"]
        oracle = vs["candidate"]["proof"].get("lec_aux_result", {})
        evidence = diagnosis["explanation"] + " " + "; ".join(diagnosis["observations"])
        evidence += " " + diagnosis["next_step"]
        body += f'<tr><td>{_e(row["test"] + "/" + row["config"])}</td><td>'
        body += _e(_fmt(vs["previous"]["measured_frequency_ratio"], 3) + " / "
                   + _fmt(vs["candidate"]["measured_frequency_ratio"], 3))
        verdict = oracle.get("verdict", "pending") + (" (bounded)" if oracle.get("bounded") else "")
        body += f'</td><td>{_e(verdict)}</td><td class="note">{_e(evidence)}</td></tr>'
    body += '</table><h2>Fresh independent lgcheck sweeps</h2>'
    for audit in summary["independent_audits"]:
        body += f'<p>{_e(audit["path"])}: {_e(json.dumps(audit["counts"], sort_keys=True))}</p>'
    normalization = summary.get("normalization_audit", [])
    if normalization:
        extracted = sum(sum(r["gate_extractions"]) for r in normalization)
        body += (f'<p>Structural normalization: {len(normalization)} retained logs, '
                 f'{extracted} ABC gates extracted. Log digests and every extraction count '
                 'remain in the evidence artifact.</p>')
    body += '<h2>Additional experiments (separate recipes)</h2>'
    for run, experiment in summary.get("additional_comparisons", {}).items():
        body += f'<h3>{_e(run)}: {_e(experiment["spec"].get("lhd_settings", ""))}</h3>'
        body += ('<table><tr><th>Case</th><th>Measured / proven ratio vs ABC</th>'
                 '<th>Area vs candidate</th><th>Same netlist</th></tr>')
        for row in experiment["rows"]:
            body += f'<tr><td>{_e(row["test"] + "/" + row["config"])}</td><td>'
            body += _e(_fmt(row["measured_frequency_ratio"], 3) + " / "
                       + _fmt(row["frequency_ratio"], 3))
            body += f'</td><td>{_e(_fmt(row["area_ratio_to_candidate"], 3))}</td>'
            body += f'<td>{_e(row["unchanged_netlist"])}</td></tr>'
        body += '</table>'
    page = render(summary).replace('</body>', body + '</body>')
    return page


if __name__ == "__main__":
    main()
