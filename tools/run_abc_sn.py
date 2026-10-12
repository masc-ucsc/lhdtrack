#!/usr/bin/env python3
"""Compare ABC/SN against fixed Brent-Kung LiveHD ABC on ASAP7."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import write_results
from lhdtrack.run import Runner, load_flows, plan
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    parser.add_argument("-j", "--jobs", type=int, default=4)
    parser.add_argument("--lec", action="store_true", help="also prove the exact LHD comparison netlists")
    args = parser.parse_args()
    tc, cfg = Toolchain.load(args.toolchain_root), load_config(ROOT)
    tests = discover(ROOT, args.test or None)
    names = ["syn_abc_sn", "syn_lhd_verilog_abc_sn"]
    if args.lec:
        names.append("lec_netlist_verilog_abc_sn")
    jobs = plan(ROOT, tests, tc, load_flows(ROOT), ["asap7"], names)
    if not jobs:
        parser.error("no matching tests")
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-abc-sn-brent-sn"
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    snapshot = tc.write_snapshot(ROOT / "var" / "runs" / run / "toolchain-snapshot")
    identity = {**runner.identity(), "comparison_profile": "abc_sn_brent_sn",
                "toolchain_snapshot": str(snapshot)}
    ledger = Ledger(ROOT)
    done = 0

    def finished(row):
        nonlocal done
        runner.gate([row])
        ledger.append(identity, [row])
        done += 1
        print(f"[{done}/{len(jobs)}] {row.test}/{row.config} {row.flow}: {row.status} "
              f"{row.time_ms} {row.note[:180]}", flush=True)

    print(f"run {run}: {len(jobs)} jobs, fixed Brent-Kung / SN multiplier comparison", flush=True)
    rows = runner.execute(jobs, jobs_parallel=max(1, args.jobs), on_done=finished)
    write_results(ROOT, cfg=cfg, host=identity["host"])
    return int(any(row.status == "failed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
