#!/usr/bin/env python3
"""Refresh this host's simulations and Sky130 synthesis, publishing gated groups."""
import argparse
from collections import Counter, defaultdict
import datetime as dt
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.report.html import write_report, write_index
from lhdtrack.run import Runner, host_name, load_flows, plan
from lhdtrack.toolchain import Toolchain

FLOWS = ["sim_verilator", "sim_lhd_verilog", "sim_lhd_pyrope",
         "syn_yosys_abc", "syn_lhd_verilog", "syn_lhd_pyrope"]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument("--resume")
    args = parser.parse_args()
    tc = Toolchain.load(ROOT)
    cfg = load_config(ROOT)
    tests = discover(ROOT, args.test or None)
    jobs = plan(ROOT, tests, tc, load_flows(ROOT), ["sky130"], FLOWS)
    run = args.resume or dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    folder = ROOT / "var/runs" / run
    folder.mkdir(parents=True, exist_ok=True)
    snapshot = folder / "toolchain.json"
    current = (ROOT / "var/toolchain/toolchain.json").read_text()
    if args.resume:
        if json.loads(snapshot.read_text()) != json.loads(current):
            raise SystemExit("toolchain changed; start a new measurement run")
    else:
        snapshot.write_text(current)
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    identity = runner.identity()
    ledger = Ledger(ROOT)
    completed = {(row["test"], row["config"], row["tech"], row["flow"])
                 for row in ledger.load(host_name()) if row["run_id"] == run}
    jobs = [job for job in jobs
            if (job.test.name, job.config.id, job.tech, job.flow) not in completed]
    expected = Counter((job.test.name, job.config.id) for job in jobs)
    groups = defaultdict(list)
    done = 0
    last_render = 0.0
    out = ROOT / "target" / (f"host-refresh-{run}.html" if args.test else f"report-{host_name()}-full.html")
    state = dict(run_id=run, host=host_name(), jobs=args.jobs, flows=FLOWS,
                 phase="running", total=len(jobs), completed=0)

    def render():
        nonlocal last_render
        (folder / "host-refresh.json").write_text(json.dumps(state, indent=2) + "\n")
        write_report(ROOT, out=out, cfg=cfg, host=host_name(), comparison_name="host-refresh")
        write_index(ROOT)
        last_render = time.monotonic()

    def finished(row):
        nonlocal done
        done += 1
        key = row.test, row.config
        groups[key].append(row)
        # Checksum agreement is a group gate. Publish only when all selected
        # simulators for this design/config have returned.
        if len(groups[key]) == expected[key]:
            runner.gate(groups[key])
            ledger.append(identity, groups.pop(key))
        state["completed"] = done
        print(f"[{done}/{len(jobs)}] {row.test}/{row.config} {row.flow}: "
              f"{row.status} {row.note[:200]}", flush=True)
        if time.monotonic() - last_render >= 30:
            render()

    print(f"run {run}: {len(jobs)} measurements; report: {out}", flush=True)
    render()
    rows = runner.execute(jobs, jobs_parallel=args.jobs, on_done=finished)
    assert not groups, "unpublished incomplete measurement group"
    state["phase"] = "complete"
    render()
    print(dict(Counter(row.status for row in rows)), flush=True)
    return int(any(row.status == "failed" for row in rows))


if __name__ == "__main__":
    raise SystemExit(main())
