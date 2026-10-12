#!/usr/bin/env python3
"""Measure a single-color LiveHD variant against an existing external ASAP7 run."""
from __future__ import annotations

import argparse
import datetime as dt
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import load_test
from lhdtrack.ledger import Ledger
from lhdtrack.report.external_abc_sn import write_external_color_run
from lhdtrack.run import Job, Runner, load_flows
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-run", required=True)
    parser.add_argument("--design", required=True)
    parser.add_argument("--synth-timeout", type=int, default=1800)
    parser.add_argument("--lec-timeout", type=int, default=20)
    args = parser.parse_args()
    if args.synth_timeout <= 60 or args.lec_timeout <= 0:
        parser.error("synthesis timeout must exceed 60s; LEC timeout must be positive")
    source = ROOT / "var/runs" / args.source_run
    tc = Toolchain.load(source / "toolchain-snapshot")
    test = load_test(source / "source/tests" / args.design)
    cfg = load_config(ROOT)
    cfg["run"]["lec_timeout_s"] = (args.synth_timeout - 60 + 1) // 2
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-external-flat"
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    ledger = Ledger(ROOT)
    baseline = next(r for r in reversed(ledger.load(runner.identity()["host"]))
                    if r["run_id"] == args.source_run and r["test"] == args.design
                    and r["flow"] == "syn_lhd_verilog_abc_sn")
    identity = {**runner.identity(), "external_color_diagnostic": True,
                "source_run": args.source_run, "comparison_profile": "abc_sn_brent_sn_flat",
                "external_provenance": baseline["external_provenance"],
                "measurement_jobs": 1,
                "toolchain_snapshot": str(tc.write_snapshot(
                    ROOT / "var/runs" / run / "toolchain-snapshot"))}
    flows = load_flows(ROOT)
    captured = []

    def finished(row):
        runner.gate([row])
        ledger.append(identity, [row])
        captured.append(row)
        print(f"{row.flow}: {row.status} {row.time_ms} {row.note}", flush=True)
        print(write_external_color_run(ROOT, identity["host"], run, args.design), flush=True)

    print(f"run {run}: {args.design}, one core color, default memory policy", flush=True)
    for flow in ("syn_lhd_verilog_abc_sn_flat", "lec_netlist_verilog_abc_sn_flat"):
        if flow.startswith("lec_"):
            cfg["run"]["lec_timeout_s"] = args.lec_timeout
        module, path = flows[flow]
        runner.execute([Job(test, test.configs[0], "asap7", flow, module, path)],
                       jobs_parallel=1, on_done=finished)
    return int(any(r.status == "failed" for r in captured))


if __name__ == "__main__":
    raise SystemExit(main())
