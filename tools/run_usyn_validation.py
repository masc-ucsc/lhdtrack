#!/usr/bin/env python3
"""Validate exact retained USYN netlists against Pyrope and recorded simulation checksums."""
from __future__ import annotations

import argparse
from collections import Counter
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.context import FlowError, FlowSkip
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.run import Job, Runner, expected_checksums, host_name, load_flows
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--synth-run", required=True)
    parser.add_argument("--test", action="append")
    parser.add_argument("--jobs", type=int, default=2)
    parser.add_argument("--checks", choices=("both", "lec", "sim"), default="both")
    parser.add_argument("--simulator", choices=("lhd", "verilator"), default="lhd")
    args = parser.parse_args()
    source_spec = json.loads((ROOT / "var/runs" / args.synth_run / "evaluation.json").read_text())
    tc = Toolchain.load(ROOT)
    if source_spec["versions"] != tc.versions:
        raise SystemExit("staged toolchain differs from synthesis: "
                         "validate with the identical binary")
    if source_spec["liberty_sha256"] != tc.tech(source_spec["tech"]).sha256:
        raise SystemExit("retained synthesis uses a different Liberty library")
    load_flows(ROOT)  # installs the flow import path; tools always come from tc
    from lec_netlist import _check
    from lib.simgate import run_lhd_sim
    from sim_verilator import run as run_verilator

    tests = discover(ROOT, args.test or None)
    slots = {(s["test"], s["config"]) for s in source_spec["slots"]}
    requested = {(t.name, c.id) for t in tests for c in t.configs
                 if (t.name, c.id) in slots}
    source_rows = {(r["test"], r["config"]): r for r in Ledger(ROOT).load(source_spec["host"])
                   if r.get("run_id") == args.synth_run
                   and r.get("tech") == source_spec["tech"]
                   and r.get("flow") == "syn_lhd_verilog_usyn_no_satopt"}
    pending = sorted(requested - source_rows.keys())
    if pending:
        raise SystemExit(f"USYN synthesis is still pending for {len(pending)} requested slots: "
                         f"{pending[:5]}")
    run = dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = ROOT / "var/runs" / run
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "toolchain.json").write_text((ROOT / "var/toolchain/toolchain.json").read_text())
    spec = dict(run_id=run, source_run=args.synth_run, tech=source_spec["tech"],
                checks=args.checks, simulator=args.simulator, versions=tc.versions,
                phase="running", completed=0,
                source_liberty_sha256=source_spec["liberty_sha256"])

    def retained(ctx):
        directory = (ROOT / "var/work" / args.synth_run / ctx.test.name / ctx.config.id
                     / source_spec["tech"] / "syn_lhd_verilog_usyn_no_satopt")
        artifact = directory / "synth-artifacts.json"
        if not artifact.exists():
            raise FlowSkip("source USYN synthesis produced no retained netlist")
        doc = json.loads(artifact.read_text())
        netlist = Path(doc["netlist"])
        digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
        if doc["mapper"] != "usyn" or digest != doc["sha256"]:
            raise FlowError("retained netlist differs from its measured synthesis artifact")
        ctx.write("source-artifact.json", json.dumps(dict(source_run=args.synth_run,
                  netlist=str(netlist), netlist_sha256=digest), indent=2) + "\n")
        return netlist, digest

    def models(ctx):
        ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", str(ctx.liberty[0]),
                           "--emit-dir", "lg:models", "--emit", "verilog:models.v",
                           "--workdir", "Wm"],
                timeout=ctx.lec_timeout_s * 2 + 60)
        return ctx.work / "models.v"

    def lec(ctx):
        netlist, digest = retained(ctx)
        if ctx.test.pyrope_status == "none" or not ctx.test.pyrope_top.exists():
            raise FlowSkip("test has no Pyrope reference")
        block = _check(ctx, impl=f"verilog:{netlist}", ref=str(ctx.test.pyrope_top),
                       models=f"lg:{models(ctx).parent / 'models'}", solver="cvc5",
                       obligation="pyrope-vs-netlist", label="lec_pyrope_usyn_netlist",
                       impl_top=ctx.top, ref_top=f"{ctx.top}.{ctx.top}", bound=6, satopt=False)
        block.update(source_run=args.synth_run, mapper="usyn", netlist_sha256=digest,
                     liberty_sha256=ctx.tech.sha256)
        return {"lec": block}

    def sim(ctx):
        if reason := ctx.test.raw.get("sim", {}).get("skip_reason"):
            raise FlowSkip(reason)
        netlist, digest = retained(ctx)
        if not ctx.config.sim_checksum:
            raise FlowSkip("test has no recorded full-cycle simulation checksum")
        if ctx.config.sim_checksum[0] != ctx.test.sim_cycles:
            raise FlowError("manifest simulation length differs from its recorded checksum")
        if args.simulator == "verilator":
            filelist = ctx.write("mapped.f", f"{netlist}\n{models(ctx)}\n")

            class RetainedTest:
                def __init__(self, original):
                    self.original = original

                @property
                def filelist(self):
                    return filelist

                def sim_verilog_args(self):
                    env = ctx.tc.env_for(ctx.tool("lhd"))
                    ware = Path(env["RUNFILES_DIR"]) / "_main" / "ware" / "rtl"
                    if not ware.is_dir():
                        raise FlowError("staged LiveHD runfiles lack the retained memory models")
                    return [*self.original.sim_verilog_args(), "-I" + str(ware)]

                def __getattr__(self, name):
                    return getattr(self.original, name)

            original = ctx.test
            ctx.test = RetainedTest(original)
            ctx.sim_reps = 1
            try:
                result = run_verilator(ctx)
            finally:
                ctx.test = original
            if result["sim"]["cycles"] != ctx.test.sim_cycles:
                raise FlowError("retained netlist simulation used a different cycle count")
            result["sim"].update(source_run=args.synth_run, mapper="usyn",
                                 netlist_sha256=digest, validation_only=True,
                                 simulator="verilator")
            return result
        tb = ctx.test.sim_dir / f"{ctx.top}_tb.prp"
        if not tb.exists():
            raise FlowSkip("test has no Pyrope simulation driver")
        cell_models = models(ctx)
        harness = ctx.test.sim_dir / f"{ctx.top}_harness.sv"
        includes = [netlist, cell_models] + ([harness] if harness.exists() else [])
        source = ctx.write("sim-impl.sv", "`define SYNTHESIS\n`define BR_PPA_SYNTHESIS\n"
                           + "\n".join(f'`include "{p}"' for p in includes) + "\n")
        ctx.run("elab", [ctx.tool("lhd"), "compile", str(source), "--top",
                         f"{ctx.top}_harness" if harness.exists() else ctx.top,
                         "--emit-dir", "lg:dut", "--workdir", "tw"],
                timeout=ctx.lec_timeout_s * 2 + 60)
        result = run_lhd_sim(ctx, design_input="lg:dut", tb=tb, validation_only=True)
        result["sim"].update(source_run=args.synth_run, mapper="usyn",
                             netlist_sha256=digest, simulator="lhd")
        return result

    modules = []
    if args.checks != "sim":
        modules.append(SimpleNamespace(NAME="lec_retained_usyn_pyrope", KIND="lec", NEEDS=("lhd",),
                                       USES_TECH=True, run=lec))
    if args.checks != "lec":
        name = "sim_retained_usyn_netlist" + ("_verilator" if args.simulator == "verilator" else "")
        needs = ("lhd", "verilator", "make", "cxx") if args.simulator == "verilator" else ("lhd",)
        modules.append(SimpleNamespace(NAME=name, KIND="sim", NEEDS=needs,
                                       USES_TECH=True, run=sim))
    jobs = [Job(t, c, source_spec["tech"], m.NAME, m, Path(__file__))
            for t in tests for c in t.configs if (t.name, c.id) in slots for m in modules]
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, load_config(ROOT), keep_work=True)
    ledger = Ledger(ROOT)
    identity = {**runner.identity(), "source_usyn_run": args.synth_run}
    checksums = expected_checksums(jobs)
    results = []

    def finished(row):
        runner.gate([row], checksums)
        ledger.append(identity, [row])
        results.append(row)
        spec["completed"] = len(results)
        (folder / "usyn-validation.json").write_text(json.dumps(spec, indent=2) + "\n")
        verdict = row.lec_result.get("verdict", "")
        print(f"[{len(results)}/{len(jobs)}] {row.test}/{row.config} {row.flow}: "
              f"{row.status} {verdict} {row.note[:200]}", flush=True)

    print(f"run {run}: {len(jobs)} exact-netlist checks from {args.synth_run}", flush=True)
    runner.execute(jobs, jobs_parallel=args.jobs, on_done=finished)
    spec["phase"] = "complete"
    (folder / "usyn-validation.json").write_text(json.dumps(spec, indent=2) + "\n")
    print(dict(Counter(row.status for row in results)), flush=True)
    return int(any(row.status == "failed" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
