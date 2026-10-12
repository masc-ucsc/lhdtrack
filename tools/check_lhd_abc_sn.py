#!/usr/bin/env python3
"""Prove retained fixed-Brent LHD netlists from an existing ABC/SN regression."""
from __future__ import annotations

import argparse
import hashlib
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.run import Runner, host_name, load_flows, plan
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--test", action="append", required=True)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("-j", "--jobs", type=int, default=4)
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.timeout < 1 or args.jobs < 1:
        parser.error("timeout and jobs must be positive")
    tc, cfg = Toolchain.load(args.toolchain_root), load_config(ROOT)
    cfg.setdefault("run", {})["lec_timeout_s"] = args.timeout
    ledger = Ledger(ROOT)
    measurements = {(r["test"], r["config"]): r for r in ledger.load(host_name())
                    if r.get("run_id") == args.run and r.get("tech") == "asap7"
                    and r.get("flow") == "syn_lhd_verilog_abc_sn"}
    jobs = plan(ROOT, discover(ROOT, args.test), tc, load_flows(ROOT), ["asap7"],
                ["lec_netlist_verilog_abc_sn"])
    selected = []
    for job in jobs:
        row = measurements.get((job.test.name, job.config.id), {})
        if not row.get("qor", {}).get("netlist_sha256"):
            print(f"skip {job.test.name}/{job.config.id}: no retained measured netlist")
            continue
        for tool in ("lhd", "lgcheck", "yosys2"):
            if row.get("versions", {}).get(tool) != tc.version(tool):
                raise RuntimeError(f"{tool} changed since the measured synthesis")
        if row["qor"].get("liberty_sha256") != tc.tech("asap7").sha256:
            raise RuntimeError("Liberty changed since the measured synthesis")
        for path, digest in row["qor"].get("source_sha256", {}).items():
            if hashlib.sha256(Path(path).read_bytes()).hexdigest() != digest:
                raise RuntimeError(f"input changed since the measured synthesis: {path}")
        selected.append(job)
    if not selected:
        parser.error("no retained measured LHD netlists for the requested tests/run")
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), args.run, cfg, keep_work=True)
    profiles = {row.get("qor", {}).get("comparison_profile")
                for row in measurements.values() if row.get("qor", {}).get("comparison_profile")}
    identity = {**runner.identity(), "comparison_profile": sorted(profiles),
                "proof_of_retained_synthesis": True, "proof_timeout_s": args.timeout}

    def finished(row):
        runner.gate([row])
        ledger.append(identity, [row])
        print(f"{row.test}/{row.config}: {row.status}; native "
              f"{row.lec_verilog_result.get('verdict')}; independent "
              f"{row.lec_aux_result.get('verdict')}", flush=True)

    rows = runner.execute(selected, jobs_parallel=args.jobs, on_done=finished)
    return int(any(row.status == "failed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
