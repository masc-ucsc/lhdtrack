#!/usr/bin/env python3
"""Measure sequential LHD ABC / ABC-SN step profiles on the same staged toolchain."""
from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import write_results
from lhdtrack.run import Job, Runner, load_flows
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append", help="repeat for each selected test")
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    parser.add_argument("--ware-cutoff", action="store_true",
                        help="diagnostic: skip arithmetic trials above one mapped cell")
    parser.add_argument("--brent", action="store_true",
                        help="compare SN with fixed LHD Brent-Kung / SN multiplier, without trials")
    args = parser.parse_args()
    if args.brent and args.ware_cutoff:
        parser.error("--brent and --ware-cutoff are separate comparison profiles")
    variant = "brent" if args.brent else "ware-cutoff-1" if args.ware_cutoff else "defaults"
    if args.ware_cutoff:
        os.environ["LHDTRACK_LHD_SET"] = (
            os.environ.get("LHDTRACK_LHD_SET", "") + " pass.abc.ware_max_cells=1").strip()
    tests = discover(ROOT, args.test or ["add", "br_counter", "barrel_shifter"])
    tc = Toolchain.load(args.toolchain_root)
    cfg = load_config(ROOT)
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-syn-profile"
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    snapshot = tc.write_snapshot(ROOT / "var" / "runs" / run / "toolchain-snapshot")
    identity = {**runner.identity(), "synthesis_timing_profile": True,
                "synthesis_timing_variant": variant, "measurement_jobs": 1,
                "lhd_settings": os.environ.get("LHDTRACK_LHD_SET", ""),
                "toolchain_snapshot": str(snapshot)}
    flows = load_flows(ROOT)
    jobs = []
    for test in tests:
        for config in test.configs:
            lhd_flow = "syn_lhd_verilog_abc_sn" if args.brent else "syn_lhd_verilog_no_satopt"
            for name in (lhd_flow, "syn_abc_sn"):
                module, path = flows[name]
                label = name + ("_ware_cutoff" if args.ware_cutoff else "")
                jobs.append(Job(test, config, "asap7", label, module, path))
    if not jobs:
        parser.error("no matching tests")
    ledger = Ledger(ROOT)
    failed = []

    def finished(row):
        runner.gate([row])
        ledger.append(identity, [row])
        print(f"{row.test}/{row.config} {row.flow}: {row.status} {row.time_ms}", flush=True)
        if row.status == "failed":
            failed.append(row)

    print(f"run {run}: {len(jobs)} jobs, sequential measurement", flush=True)
    runner.execute(jobs, jobs_parallel=1, on_done=finished)
    write_results(ROOT, cfg=cfg, host=identity["host"])
    print(f"target/results-syn-timing-{identity['host']}.html", flush=True)
    return bool(failed)


if __name__ == "__main__":
    raise SystemExit(main())
