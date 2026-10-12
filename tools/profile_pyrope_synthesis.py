#!/usr/bin/env python3
"""Measure ASAP7 Pyrope baseline, explicit arithmetic profiling, and replay.

Every attempt goes through Runner and the append-only ledger. The portable
tuning file connects otherwise isolated measurement workdirs. Source bytes are
archived before measuring; this tool never edits the corpus.
"""
from __future__ import annotations

import argparse
import datetime as dt
from dataclasses import replace
import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.run import Job, Runner, load_flows
from lhdtrack.toolchain import Toolchain


def prove_retained(ctx, synthesis: str, netlist_flow) -> dict:
    work = ctx.work.parent / synthesis
    if not (work / "netlist").is_dir():
        from lhdtrack.context import FlowSkip
        raise FlowSkip("synthesis did not retain a mapped netlist")
    ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", str(ctx.liberty[0]),
                       "--emit-dir", "lg:models", "--workdir", "models-W"])
    return {"lec": netlist_flow._check(
        ctx, impl="lg:" + str(work / "netlist"), ref="lg:" + str(work / "W/synth/lg"),
        models="lg:" + str(ctx.work / "models"), solver="cvc5",
        obligation="pyrope-vs-exact-profiled-netlist", label="mapped",)}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--all", action="store_true", help="include auto and unproven Pyrope")
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    parser.add_argument("--attempts", type=int, default=12)
    parser.add_argument("--time-ms", type=int, default=10000)
    parser.add_argument("--validate", choices=("structural", "region", "design"),
                        default="structural")
    parser.add_argument("--lec-only", action="store_true",
                        help="run the existing source equivalence gate before synthesis")
    parser.add_argument("--mode", action="append", choices=("baseline", "profile", "replay", "pinned"))
    parser.add_argument("--set", action="append", default=[], dest="settings")
    parser.add_argument("--source-root", type=Path, help="snapshot test directories for annotation experiments")
    parser.add_argument("--candidate-root", type=Path,
                        help="use these test snapshots only for the pinned variant")
    parser.add_argument("--prove", action="store_true", help="prove each exact retained mapped netlist")
    args = parser.parse_args()
    tests = discover(ROOT, args.test)
    if not args.test and not args.all:
        tests = [test for test in tests if test.comparable()]
    if args.source_root:
        tests = [replace(test, root=args.source_root.resolve() / test.name) for test in tests]
    tc = Toolchain.load(args.toolchain_root)
    cfg = load_config(ROOT)
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-syntha"
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    snapshot = tc.write_snapshot(ROOT / "var/runs" / run / "toolchain-snapshot")
    identity = {**runner.identity(), "synthesis_attributes_profile": True,
                "measurement_jobs": 1, "toolchain_snapshot": str(snapshot)}
    flows = load_flows(ROOT)
    base, flow_path = flows["syn_lhd_pyrope"]
    ledger = Ledger(ROOT)
    results = []
    archive = ROOT / "var/runs" / run / "sources"
    for test in tests:
        for path in test.pyrope_dir.glob("*.prp"):
            dest = archive / test.name / path.name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(path.read_bytes())

    def finished(row):
        runner.gate([row])
        ledger.append(identity, [row])
        results.append(row.to_dict())
        (ROOT / "var/runs" / run / "results.json").write_text(
            json.dumps({"identity": identity, "rows": results}, indent=2) + "\n")
        print(f"{row.test}/{row.config} {row.flow}: {row.status} "
              f"{row.qor.get('cells', row.qor.get('gates', '?'))} cells "
              f"{row.sta.get('opensta_ns', '?')} ps {row.time_ms}", flush=True)

    jobs = []
    for test in tests:
        for config in test.configs:
            if args.lec_only:
                module, path = flows["lec_lhd"]
                jobs.append(Job(test, config, None, "lec_lhd", module, path))
                continue
            tune = ROOT / "var/runs" / run / "tune" / test.name / (config.id + ".json")
            variants = args.mode or ["baseline", "profile", "replay"]
            for variant in variants:
                selected_test = test
                if variant == "pinned" and args.candidate_root:
                    selected_test = replace(test, root=args.candidate_root.resolve() / test.name)
                    for path in selected_test.pyrope_dir.glob("*.prp"):
                        dest = archive / "pinned" / test.name / path.name
                        dest.parent.mkdir(parents=True, exist_ok=True)
                        dest.write_bytes(path.read_bytes())
                mode = {"baseline": "off", "profile": "on", "replay": "auto", "pinned": "off"}[variant]
                settings = ["--set", f"synth.tune.profile={mode}",
                            "--set", f"synth.tune.validate={args.validate}",
                            "--set", f"synth.tune.attempts={args.attempts}",
                            "--set", f"synth.tune.time_ms={args.time_ms}"]
                if mode == "on":
                    settings += ["--set", f"synth.tune.export={tune}"]
                elif mode == "auto":
                    settings += ["--set", f"synth.tune.file={tune}"]
                settings += [a for setting in args.settings for a in ("--set", setting)]
                module = ModuleType(f"syn_lhd_pyrope_tune_{variant}")
                for key in ("KIND", "NEEDS", "OPTIONAL", "USES_TECH"):
                    setattr(module, key, getattr(base, key))
                module.run = lambda ctx, selected=settings: base.run(ctx, settings=selected)
                jobs.append(Job(selected_test, config, "asap7", module.__name__, module, flow_path))
                if args.prove:
                    proof = ModuleType(f"lec_pyrope_tune_{variant}")
                    proof.KIND, proof.NEEDS, proof.USES_TECH = "lec", ("lhd",), True
                    proof.run = lambda ctx, synthesis=module.__name__: prove_retained(ctx, synthesis, flows["lec_netlist"][0])
                    jobs.append(Job(selected_test, config, "asap7", proof.__name__, proof, flow_path))
    print(f"{run}: {len(jobs)} sequential jobs; compiler {tc.versions.get('lhd')}", flush=True)
    runner.execute(jobs, jobs_parallel=1, on_done=finished)
    print(ROOT / "var/runs" / run / "results.json", flush=True)
    return int(any(row["status"] == "failed" for row in results))


if __name__ == "__main__":
    raise SystemExit(main())
