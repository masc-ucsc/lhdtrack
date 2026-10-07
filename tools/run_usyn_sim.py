#!/usr/bin/env python3
"""Benchmark retained USYN Verilog with Verilator, LHD Slop and LHD LLVM."""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import datetime as dt
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import sys
import time
from types import SimpleNamespace


def physical_cpus(limit):
    """Use one hardware thread per available physical core on Linux."""
    selected, seen = [], set()
    for cpu in sorted(os.sched_getaffinity(0)):
        topology = Path(f"/sys/devices/system/cpu/cpu{cpu}/topology")
        identity = ((topology / "physical_package_id").read_text(),
                    (topology / "core_id").read_text())
        if identity not in seen:
            selected.append(cpu)
            seen.add(identity)
        if len(selected) == limit:
            break
    return selected


def timing_cycles(cycles, previous, seconds):
    """Shorten only the timing profile; the manifest's full reference stays intact."""
    elapsed = previous.get("exec_ms")
    if not seconds or not isinstance(elapsed, (int, float)) or elapsed <= 0:
        return cycles
    return min(cycles, max(1, int(cycles * seconds * 1000 / elapsed)))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--synth-run", required=True)
    parser.add_argument("--test", action="append")
    parser.add_argument("--build-jobs", type=int, default=32)
    parser.add_argument("--long-run-seconds", type=float, default=30,
                        help="use one full execution above this historical runtime (0 disables)")
    parser.add_argument("--timing-seconds", type=float, default=0,
                        help="calibrate a separate timing profile from prior netlist runtimes; "
                             "check it against fresh original-RTL Verilator (0 keeps full cycles)")
    args = parser.parse_args()
    if args.build_jobs < 1:
        parser.error("--build-jobs must be positive")
    if args.long_run_seconds < 0:
        parser.error("--long-run-seconds must be nonnegative")
    if args.timing_seconds < 0:
        parser.error("--timing-seconds must be nonnegative")
    root = args.root.resolve()
    sys.path.insert(0, str(root / "src"))
    from lhdtrack.cache import Cache
    from lhdtrack.cli import load_config
    from lhdtrack.context import FlowError, FlowSkip
    from lhdtrack.corpus import discover
    from lhdtrack.ledger import Ledger
    from lhdtrack.run import Job, Runner, load_flows
    from lhdtrack.toolchain import Toolchain

    cpus = physical_cpus(args.build_jobs)
    os.sched_setaffinity(0, cpus)
    source = json.loads((root / "var/runs" / args.synth_run / "evaluation.json").read_text())
    tc = Toolchain.load(root)
    if source["liberty_sha256"] != tc.tech(source["tech"]).sha256:
        raise SystemExit("retained netlists and simulator models use different Liberty libraries")
    if tc.env_for(tc.bin("lhd")).get("CXX") != str(tc.bin("cxx")):
        raise SystemExit("pin lhd's CXX environment to manifest cxx for equal compiler selection")
    load_flows(root)
    from sim_verilator import run as verilator
    from sim_lhd_verilog import run as lhd_verilog

    slots = {(slot["test"], slot["config"]) for slot in source["slots"]}
    tests = discover(root, args.test or None)
    historical = {}
    for row in sorted(Ledger(root).load(source["host"]), key=lambda r: r.get("run_id", "")):
        sim = row.get("sim", {})
        if (row.get("flow") == "sim_retained_usyn_netlist_verilator"
                and row.get("status") == "ok" and row.get("tech") == source["tech"]
                and row.get("source_usyn_run", sim.get("source_run")) == args.synth_run):
            key = row["test"], row["config"], sim.get("netlist_sha256"), sim.get("cycles")
            historical[key] = sim
    run_id = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = root / "var/runs" / run_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "toolchain.json").write_bytes((root / "var/toolchain/toolchain.json").read_bytes())

    profiles = {}

    def simulate(ctx, backend):
        if reason := ctx.test.raw.get("sim", {}).get("skip_reason"):
            raise FlowSkip(reason)
        if not ctx.config.sim_checksum:
            raise FlowSkip("test has no recorded full-cycle simulation checksum")
        if ctx.config.sim_checksum[0] != ctx.test.sim_cycles:
            raise FlowError("manifest simulation length differs from its recorded checksum")
        artifact = (root / "var/work" / args.synth_run / ctx.test.name / ctx.config.id
                    / source["tech"] / "syn_lhd_verilog_usyn_no_satopt" / "synth-artifacts.json")
        if not artifact.exists():
            raise FlowSkip("source USYN synthesis produced no retained netlist")
        doc = json.loads(artifact.read_text())
        netlist = Path(doc["netlist"])
        digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
        if doc["mapper"] != "usyn" or digest != doc["sha256"]:
            raise FlowError("retained netlist differs from its measured synthesis artifact")
        profile = profiles.get((ctx.test.name, ctx.config.id), {})
        original_cycles = profile.get("original_cycles", ctx.test.sim_cycles)
        previous = historical.get((ctx.test.name, ctx.config.id, digest, original_cycles), {})
        if (not args.timing_seconds and args.long_run_seconds and
                isinstance(previous.get("exec_ms"), (int, float)) and
                previous["exec_ms"] >= args.long_run_seconds * 1000):
            ctx.sim_reps = 1
        ctx.write("source-artifact.json", json.dumps({
            "source_run": args.synth_run, "netlist": str(netlist), "netlist_sha256": digest,
        }, indent=2) + "\n")
        ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", str(ctx.liberty[0]),
                           "--emit-dir", "lg:models", "--emit", "verilog:models.v",
                           "--workdir", "Wm"], timeout=ctx.lec_timeout_s * 2 + 60)
        filelist = ctx.write("mapped.f", f"{netlist}\n{ctx.work / 'models.v'}\n")
        ware = Path(ctx.tc.env_for(ctx.tool("lhd"))["RUNFILES_DIR"]) / "_main/ware/rtl"
        if not ware.is_dir():
            raise FlowError("staged LiveHD runfiles lack retained memory models")

        class RetainedTest:
            def __init__(self, original):
                self.original = original

            @property
            def filelist(self):
                return filelist

            def sim_verilog_args(self):
                return [*self.original.sim_verilog_args(), "-I" + str(ware)]

            def __getattr__(self, name):
                return getattr(self.original, name)

        original = ctx.test
        ctx.test = RetainedTest(original)
        try:
            result = (verilator(ctx) if backend == "verilator" else
                      lhd_verilog(ctx, backend=backend, direct_compile_timing=True))
        finally:
            ctx.test = original
        # Verilator includes elaboration in setup. Fold LHD's separate front-end
        # leg into the same column so setup + compile is end-to-end preparation.
        elab_ms = ctx.stage.time_ms.pop("elab", 0)
        ctx.stage.time_ms["setup"] += ctx.stage.time_ms.pop("models") + elab_ms
        result["sim"]["frontend_elab_ms"] = elab_ms
        if result["sim"]["cycles"] != ctx.test.sim_cycles:
            raise FlowError("retained netlist simulation used a different cycle count")
        result["sim"].update(source_run=args.synth_run, mapper="usyn", netlist_sha256=digest,
                             liberty_sha256=ctx.tech.sha256, validation_only=False,
                             compile_timing="direct",
                             exec_repetitions=ctx.sim_reps,
                             simulator="verilator" if backend == "verilator" else "lhd")
        result["sim"].update(profiles.get((ctx.test.name, ctx.config.id), {}))
        return result

    def reference(ctx):
        ctx.sim_reps = 1
        result = verilator(ctx)
        result["sim"].update(validation_only=True, reference_source="original RTL",
                            source_run=args.synth_run, exec_repetitions=1)
        return result

    reference_module = SimpleNamespace(
        NAME="sim_retained_usyn_timing_reference", KIND="sim", USES_TECH=False,
        NEEDS=("verilator", "make", "cxx"), run=reference)
    modules = []
    for suffix, backend in (("verilator", "verilator"), ("lhd_verilog", "slop"),
                            ("lhd_verilog_llvm", "llvm")):
        modules.append(SimpleNamespace(
            NAME="sim_retained_usyn_netlist_" + suffix, KIND="sim", USES_TECH=True,
            NEEDS=("lhd", "verilator", "make", "cxx") if backend == "verilator" else ("lhd", "cxx"),
            run=lambda ctx, backend=backend: simulate(ctx, backend),
        ))
    grouped = defaultdict(list)
    for test in tests:
        for config in test.configs:
            if (test.name, config.id) in slots:
                grouped[test.name, config.id] = [
                    Job(test, config, source["tech"], module.NAME, module, Path(__file__))
                    for module in modules]
    cfg = load_config(root)
    build_jobs = min(args.build_jobs, len(cpus))
    cfg["run"] = {**cfg.get("run", {}), "jobs": 1, "sim_build_jobs": build_jobs}
    runner = Runner(root, tc, Cache(root, enabled=False), run_id, cfg, keep_work=True)
    ledger = Ledger(root)
    identity = {**runner.identity(), "source_usyn_run": args.synth_run}
    progress = dict(run_id=run_id, source_run=args.synth_run, tech=source["tech"],
                    versions=tc.versions, cpus=cpus, build_jobs=build_jobs,
                    measurement_jobs=1, total=sum(map(len, grouped.values())),
                    long_run_seconds=args.long_run_seconds, timing_seconds=args.timing_seconds,
                    references=0,
                    completed=0, counts={}, phase="running")
    progress_path = folder / "usyn-simulation.json"
    counts = Counter()
    progress_path.write_text(json.dumps(progress, indent=2) + "\n")
    print(f"run {run_id}: {progress['total']} exact-netlist measurements", flush=True)
    for (name, config), jobs in grouped.items():
        measurement_started = time.time()
        original = jobs[0].test
        artifact = (root / "var/work" / args.synth_run / name / config / source["tech"]
                    / "syn_lhd_verilog_usyn_no_satopt" / "synth-artifacts.json")
        previous = {}
        if artifact.exists():
            digest = json.loads(artifact.read_text())["sha256"]
            previous = historical.get((name, config, digest, original.sim_cycles), {})
        cycles = timing_cycles(original.sim_cycles, previous, args.timing_seconds)
        profiles[name, config] = dict(original_cycles=original.sim_cycles,
                                     timing_profile="calibrated" if cycles < original.sim_cycles
                                     else "full", timing_target_seconds=args.timing_seconds)
        if cycles < original.sim_cycles:
            timed_test = replace(original, sim_cycles=cycles)
            ref = runner.execute([Job(timed_test, jobs[0].config, None, reference_module.NAME,
                                      reference_module, Path(__file__))], jobs_parallel=1)
            ledger.append(identity, ref)
            progress["references"] += 1
            if ref[0].status != "ok" or not ref[0].sim.get("checksum"):
                raise SystemExit(f"RTL reference failed for {name}/{config}: {ref[0].note}")
            config_copy = replace(jobs[0].config, sim_checksum=(cycles, ref[0].sim["checksum"]))
            jobs = [replace(job, test=timed_test, config=config_copy) for job in jobs]
            profiles[name, config].update(reference_run=run_id,
                                          reference_flow=reference_module.NAME)
            print(f"{name}/{config}: original RTL reference ok at {cycles} cycles "
                  f"(full validation: {original.sim_cycles})", flush=True)
        rows = runner.execute(jobs, jobs_parallel=1, on_done=lambda r: print(
            f"{r.test}/{r.config}: {r.flow} {r.status} {r.note[:240]}", flush=True))
        measurement_finished = time.time()
        for row in rows:
            if row.sim:
                row.sim["measurement_window_unix"] = [measurement_started, measurement_finished]
        ledger.append(identity, rows)
        counts.update(row.status for row in rows)
        progress.update(completed=progress["completed"] + len(rows), counts=dict(counts))
        progress_path.write_text(json.dumps(progress, indent=2) + "\n")
        print(f"PROGRESS {progress['completed']}/{progress['total']} {dict(counts)}", flush=True)
    progress["phase"] = "complete"
    progress_path.write_text(json.dumps(progress, indent=2) + "\n")
    print("DONE", json.dumps(progress), flush=True)
    return int(bool(counts["failed"]))


if __name__ == "__main__":
    raise SystemExit(main())
