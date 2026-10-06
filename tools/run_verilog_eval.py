#!/usr/bin/env python3
"""Run only LiveHD Verilog ABC/USYN synthesis and their emitted-netlist LEC.

The retained Yosys baselines are never executed. Every completed measurement
is gated and appended immediately; the public report refreshes during the run.
Use --test NAME for a separate smoke report, or --resume RUN to finish a run.
"""
import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import subprocess
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import write_results
from lhdtrack.report.verilog_eval import lec_flow, synth_flow, write_evaluation
from lhdtrack.run import Job, Runner, host_name, load_flows
from lhdtrack.toolchain import Toolchain


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--tech", default="asap7")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--lec-jobs", type=int, help="independent LEC concurrency (defaults to --jobs)")
    parser.add_argument("--resume")
    parser.add_argument("--lec-from", help="recheck exact retained netlists from this synthesis run")
    parser.add_argument("--reemit", action="store_true", help="re-emit retained graphs; accept instance-name changes only")
    parser.add_argument("--satopt-profiles", choices=("both", "enabled", "disabled"), default="both")
    parser.add_argument("--mapper", choices=("both", "abc", "usyn"), default="both")
    parser.add_argument("--usyn-stage", choices=("default", "selection", "residual", "feedback"),
                        default="default", help="native residual/feedback ablation")
    args = parser.parse_args()
    if args.usyn_stage != "default":
        residual = args.usyn_stage != "selection"
        feedback = args.usyn_stage == "feedback"
        settings = (f"pass.usyn.residual={str(residual).lower()} "
                    f"pass.usyn.feedback={str(feedback).lower()}")
        os.environ["LHDTRACK_LHD_SET"] = (os.environ.get("LHDTRACK_LHD_SET", "")
                                       + " " + settings).strip()
    if args.reemit and not args.lec_from:
        parser.error("--reemit requires --lec-from")
    if args.resume and args.lec_from:
        parser.error("--resume and --lec-from are mutually exclusive")
    tc = Toolchain.load(ROOT)
    cfg = load_config(ROOT)
    tests = discover(ROOT, args.test or None)
    tests = [t for t in tests if "syn_lhd_verilog" in t.synth_flows]
    flows = load_flows(ROOT)
    host = host_name()
    run = args.resume or dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    run_dir = ROOT / "var/runs" / run
    run_dir.mkdir(parents=True, exist_ok=True)
    spec_path = run_dir / "evaluation.json"
    public_spec = ROOT / "data" / f"verilog-eval-{host}.json"
    isolated_report = bool(args.test or args.usyn_stage != "default" or args.mapper != "both")
    out = ROOT / "target" / (f"eval-{run}.html" if isolated_report else f"results-syn-{host}.html")
    ledger = Ledger(ROOT)
    if args.resume:
        spec = json.loads(spec_path.read_text())
        if spec["versions"] != tc.versions or spec["liberty_sha256"] != tc.tech(args.tech).sha256:
            raise SystemExit("toolchain changed: start a fresh run instead of mixing binaries")
    else:
        # Check the actual staged bytes, not only their label in toolchain.json.
        digests = [hashlib.sha256(p.read_bytes()).hexdigest() for p in tc.tech(args.tech).liberty]
        lib_hash = hashlib.sha256("".join(digests).encode()).hexdigest()[:16]
        if lib_hash != tc.tech(args.tech).sha256:
            raise SystemExit("Liberty content no longer matches the staged baseline library")
        baseline = [r for r in ledger.latest_rows(host)
                    if r["flow"] == "syn_yosys_abc" and r.get("tech") == args.tech]
        baseline_tools = ("yosys", "yosys_slang", "abc", "sta")
        if any(r["host_class"] != tc.host_class or any(
                r["versions"].get(t) != tc.version(t) for t in baseline_tools)
                for r in baseline):
            raise SystemExit("retained baseline tool versions or host class differ")
        spec = dict(
            schema_version=1, host=host, run_id=run, tech=args.tech,
            versions=tc.versions, liberty_sha256=lib_hash, time_unit=tc.tech(args.tech).time_unit,
            baselines=[{k: r[k] for k in ("test", "config", "run_id")} for r in baseline],
            slots=[dict(test=t.name, config=c.id) for t in tests for c in t.configs],
            auxiliary_report=f"results-syn-{host}-full.html",
            timing_scope="prepared-verilog-inputs",
            satopt_profiles=([True, False] if args.satopt_profiles == "both" else
                             [args.satopt_profiles == "enabled"]),
            phase="starting", jobs=args.jobs, lec_jobs=args.lec_jobs or args.jobs, usyn_tmap="abc",
            mappers=(["abc", "usyn"] if args.mapper == "both" else [args.mapper]),
            usyn_stage=args.usyn_stage, lhd_settings=os.environ.get("LHDTRACK_LHD_SET", ""),
        )
        (run_dir / "toolchain.json").write_text((ROOT / "var/toolchain/toolchain.json").read_text())

    if args.resume:
        os.environ["LHDTRACK_LHD_SET"] = spec.get("lhd_settings", "")
        isolated_report = bool(args.test or spec.get("usyn_stage", "default") != "default"
                               or spec.get("mappers", ["abc", "usyn"]) != ["abc", "usyn"])
        name = f"eval-{run}.html" if isolated_report else f"results-syn-{host}.html"
        out = ROOT / "target" / name

    if args.lec_from:
        source = json.loads((ROOT / "var/runs" / args.lec_from / "evaluation.json").read_text())
        if (source["tech"], source["liberty_sha256"]) != (args.tech, spec["liberty_sha256"]):
            raise SystemExit("source netlists use a different technology or library")
        # Netlist bytes can travel between machines; timing measurements cannot.
        spec["satopt_profiles"] = source.get("satopt_profiles", [True])
        spec["mappers"] = source.get("mappers", ["abc", "usyn"])
        spec["synth_host"] = source.get("synth_host", source["host"])
        spec["reemit_logical_hierarchy"] = args.reemit
        spec["synth_run_id"] = source.get("synth_run_id", source["run_id"])
        spec["synth_versions"] = source.get("synth_versions", source["versions"])
        if source["host"] == host:
            spec["baselines"] = source["baselines"]
        spec["usyn_tmap"] = source.get(
            "usyn_tmap", "abc" if source.get("usyn_abc") == "tmap" else "unknown")
        source_slots = {(s["test"], s["config"]) for s in source["slots"]}
        if any((s["test"], s["config"]) not in source_slots for s in spec["slots"]):
            raise SystemExit("requested slot was not part of the source synthesis run")
        for slot in spec["slots"]:
            relative = Path(slot["test"]) / slot["config"] / args.tech
            target = ROOT / "var/work" / run / relative
            target.mkdir(parents=True, exist_ok=True)
            if args.reemit:
                (target / "reemit-logical-hierarchy.json").write_text(json.dumps({"source_run": spec["synth_run_id"]}) + "\n")
            for mapper in ("abc", "usyn"):
                for satopt in spec.get("satopt_profiles", [True]):
                    flow = synth_flow(mapper, satopt)
                    link = target / flow
                    previous = ROOT / "var/work" / spec["synth_run_id"] / relative / flow
                    if previous.exists():
                        link.symlink_to(previous, target_is_directory=True)

    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    identity = runner.identity()
    identity["liberty_sha256"] = spec["liberty_sha256"]
    (run_dir / "identity.json").write_text(json.dumps(identity, indent=2) + "\n")
    completed = {(r["test"], r["config"], r["flow"]) for r in ledger.load(host)
                 if r["run_id"] == run and r.get("tech") == args.tech}
    last_render = 0.0

    def render():
        nonlocal last_render
        spec_path.write_text(json.dumps(spec, indent=2) + "\n")
        if not isolated_report:
            public_spec.write_text(spec_path.read_text())
        if isolated_report:
            write_evaluation(ROOT, spec_path, out=out)
        else:
            write_results(ROOT, cfg=cfg, host=host)
        last_render = time.monotonic()

    done = len(completed)
    profiles = spec.get("satopt_profiles", [True])
    mappers = spec.get("mappers", ["abc", "usyn"])
    mapper_jobs = len(mappers) * len(profiles)
    synth_jobs = 0 if spec.get("synth_run_id") else mapper_jobs
    total_jobs = len(spec["slots"]) * (mapper_jobs + synth_jobs)

    def finished(row):
        nonlocal done
        # These jobs have no cross-language/simulation gates. Netlist gates
        # inspect both solvers inside this one row, so they are final now.
        runner.gate([row])
        ledger.append(identity, [row])
        done += 1
        blocks = [row.lec_verilog_result, row.lec_aux_result]
        answers = " ".join(f"{b['solver']}={b['verdict']}" for b in blocks if b)
        print(f"[{done}/{total_jobs}] {row.test}/{row.config} "
              f"{row.flow}: {row.status} {answers} {row.note[:180]}", flush=True)
        if time.monotonic() - last_render >= 30:
            render()

    print(f"run {run}: {len(spec['slots'])} design/configs, {args.jobs} synthesis workers, "
          f"{args.lec_jobs or args.jobs} LEC workers", flush=True)
    print(f"report: {out}", flush=True)
    render()
    # Complete synthesis first so proofs always consume a finished emission.
    # Retain all work: LEC must never silently resynthesize a different netlist.
    for phase, naming in (("synthesis in progress", synth_flow), ("LEC in progress", lec_flow)):
        if naming == synth_flow and spec.get("synth_run_id"):
            continue
        spec["phase"] = phase
        render()
        jobs = []
        for test in tests:
            for config in test.configs:
                for mapper in mappers:
                    for satopt in profiles:
                        name = naming(mapper, satopt)
                        if (test.name, config.id, name) in completed:
                            continue
                        module, path = flows[name]
                        jobs.append(Job(test, config, args.tech, name, module, path))
        workers = (args.lec_jobs or args.jobs) if naming == lec_flow else args.jobs
        runner.execute(jobs, jobs_parallel=workers, on_done=finished)
    # The cross-driver repeats the native proof before launching lgcheck. Its
    # watchdog can fire there; such a timeout is not an independent Yosys attempt.
    spec["phase"] = "auditing independent oracle coverage"
    render()
    subprocess.run([sys.executable, str(ROOT / "tools/recheck_missing_oracles.py"),
                    "--run", run], check=True)
    spec["phase"] = "complete"
    render()
    # Oracle rechecks append new evidence without deleting previous attempts.
    latest = {(r["test"], r["config"], r["flow"]): r
              for r in ledger.load(host) if r["run_id"] == run}
    rows = list(latest.values())
    print(json.dumps(dict(Counter(r["status"] for r in rows)), sort_keys=True), flush=True)
    return int(any(r["status"] == "failed" for r in rows))


if __name__ == "__main__":
    raise SystemExit(main())
