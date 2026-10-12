#!/usr/bin/env python3
"""Isolate color-policy effects using an external run's frozen sources and toolchain."""
from __future__ import annotations

import argparse
import datetime as dt
import os
import shutil
from pathlib import Path
import sys
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import load_test
from lhdtrack.ledger import Ledger
from lhdtrack.report.partition_diagnostics import write_partition_diagnostics
from lhdtrack.run import Job, Runner, load_flows
from lhdtrack.toolchain import Toolchain

VARIANTS = {
    "flat": {},
    "no-mux-cut": {"stop_mux": "false"},
    "no-operator-cuts": {k: "false" for k in
                         ("stop_arith", "stop_cmp", "stop_shift",
                          "ware_arith", "ware_cmp", "ware_shift")},
    "data-cones": {k: "false" for k in
                   ("stop_mux", "ctrl_cones", "stop_arith", "stop_cmp", "stop_shift",
                    "ware_arith", "ware_cmp", "ware_shift")},
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--variant", action="append", choices=VARIANTS, required=True)
    parser.add_argument("--max-gate", type=int, default=30000)
    parser.add_argument("--set", action="append", default=[])
    parser.add_argument("--toolchain-root", type=Path)
    parser.add_argument("--warm-cache", help="seed ABC cache from a retained partition run")
    parser.add_argument("--synth-timeout", type=int, default=1800)
    parser.add_argument("--timing-note", help="record contention or other timing limitations")
    args = parser.parse_args()
    if args.max_gate <= 0 or args.synth_timeout <= 60:
        parser.error("max-gate must be positive; synthesis timeout must exceed 60s")
    source = ROOT / "var/runs" / args.source_run
    tc_root = (ROOT / "var/runs" / args.warm_cache / "toolchain-snapshot"
               if args.warm_cache else source / "toolchain-snapshot")
    tc = Toolchain.load(args.toolchain_root or tc_root)
    if args.warm_cache:
        seed_tc = Toolchain.load(tc_root)
        if tc.version("lhd") != seed_tc.version("lhd"):
            parser.error("warm-cache compiler differs; use the retained run's toolchain snapshot")
    test = load_test(source / "source/tests" / args.design)
    cfg = load_config(ROOT)
    cfg["run"]["lec_timeout_s"] = (args.synth_timeout - 60 + 1) // 2
    flows = load_flows(ROOT)
    failed = False
    for variant in args.variant:
        module, path = flows["syn_lhd_verilog_abc_sn_flat" if variant == "flat"
                             else "syn_lhd_verilog_abc_sn_partition"]
        settings = {f"pass.color.synth.{key}": value for key, value in VARIANTS[variant].items()}
        # Ware preservation is common to every coloring, rather than synth-only.
        settings = {key.replace("pass.color.synth.ware_", "pass.color.ware_"): value
                    for key, value in settings.items()}
        settings.update({"pass.color.synth.max_gate": str(args.max_gate), "pass.abc.large_ge": "0"})
        for item in args.set:
            key, value = item.split("=", 1)
            settings[key] = value
        os.environ["LHDTRACK_LHD_SET"] = " ".join(f"{k}={v}" for k, v in settings.items())
        run = (dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S")
               + "-partition-" + variant)
        runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
        measured_module = module
        if args.warm_cache:
            seed = (ROOT / "var/work" / args.warm_cache / test.name / test.configs[0].id
                    / "asap7" / module.NAME / "W")
            if not (seed / "abc_cache" / "abc_cache.json").is_file():
                parser.error(f"no retained ABC cache in {seed}")

            def run_seeded(ctx, inner=module, cache_source=seed):
                # Runner deliberately clears each job before calling the flow.
                # Seed here so that copy survives and the original stays intact.
                shutil.copytree(cache_source, ctx.work / "W")
                return inner.run(ctx)

            measured_module = SimpleNamespace(NAME=module.NAME, KIND=module.KIND,
                                             NEEDS=module.NEEDS, USES_TECH=module.USES_TECH,
                                             run=run_seeded)
        identity = {**runner.identity(), "partition_diagnostic": True,
                    "partition_variant": variant + (" warm cache" if args.warm_cache else ""),
                    "partition_settings": settings,
                    **({"incremental_source_run": args.warm_cache} if args.warm_cache else {}),
                    "source_run": args.source_run, "measurement_jobs": 1,
                    **({"timing_note": args.timing_note} if args.timing_note else {}),
                    "toolchain_snapshot": str(tc.write_snapshot(
                        ROOT / "var/runs" / run / "toolchain-snapshot"))}

        def finished(row):
            runner.gate([row])
            Ledger(ROOT).append(identity, [row])
            abc = row.qor.get("timing_breakdown", {})
            print(f"{run}: {row.status} time={row.time_ms} cells={row.qor.get('cells')} "
                  f"path={row.sta.get('opensta_ns')} regions={len(abc.get('regions', []))} "
                  f"note={row.note}", flush=True)
            print(write_partition_diagnostics(ROOT, identity["host"], test.name), flush=True)

        print(f"run {run}: {settings}", flush=True)
        job = Job(test, test.configs[0], "asap7", module.NAME, measured_module, path)
        rows = runner.execute([job], jobs_parallel=1, on_done=finished)
        failed |= any(row.status == "failed" for row in rows)
    return int(failed)


if __name__ == "__main__":
    raise SystemExit(main())
