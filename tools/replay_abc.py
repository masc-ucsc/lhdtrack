#!/usr/bin/env python3
"""Replay one SN-exported logic network and identical commands through both staged ABCs.

This isolates ABC binary differences from frontend/partition/arithmetic differences.
It deliberately uses the same unconstrained boundary environment for both binaries;
it does not model LiveHD's patched per-region boundary timing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.ledger import Ledger
from lhdtrack.metrics import measure
from lhdtrack.run import Row, host_name
from lhdtrack.toolchain import Toolchain
from lhdtrack.report.synthesis_timing import profile_pairs


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--reps", type=int, default=2)
    args = parser.parse_args()
    if args.reps < 1:
        parser.error("--reps must be positive")
    tc = Toolchain.load(ROOT)
    host = host_name()
    ledger = Ledger(ROOT)
    selected = [sn for _, sn in profile_pairs(ledger.load(host))
                if sn["test"] in (args.test or ["add", "add_mop"])]
    if not selected:
        parser.error("no default synthesis profiles; run tools/profile_synthesis.py first")
    run = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-abc-replay"
    identity = {"run_id": run, "date": dt.date.today().isoformat(), "host": host,
                "host_class": tc.host_class, "versions": tc.versions, "measurement_jobs": 1}
    for original in selected:
        test, config = original["test"], original["config"]
        source = (ROOT / "var/work" / original["run_id"] / test / config /
                  "asap7/syn_abc_sn/design.sn")
        policy = original["qor"]["synth_policy"]
        work = ROOT / "var/work" / run / test / config / "abc-replay"
        work.mkdir(parents=True, exist_ok=True)
        export = '; '.join(policy["commands"][:7]).replace("@read design.sn", f"@read {source}")
        # A collapsed SN cloud can contain duplicate hierarchical port names.
        # Rename ABC objects uniquely before interchange; connectivity is unchanged.
        export += "; short_names; write_blif input.blif"
        imported = measure("export", [tc.bin("abc_sn"), "-c", export], work,
                           work / "logs", env=tc.env_for(tc.bin("abc_sn")), timeout=60)
        network = work / "input.blif"
        if not imported.ok or not network.is_file():
            raise RuntimeError("failed to export pre-optimization logic network")
        commands = [entry["command"] for entry in policy["command_timing"]
                    if entry["stage"] == "shared_flow"]
        script = (f"read_lib -s {tc.tech('asap7').liberty[0]}; read_blif input.blif; "
                  + '; '.join(commands) + "; write_blif mapped.blif")
        (work / "replay.abc").write_text(script + "\n")
        observation = {"source_run": original["run_id"], "network_sha256": sha(network),
                       "command_sha256": sha(work / "replay.abc"), "script": script,
                       "boundary": "same ABC default boundary environment, no driving cell",
                       "scope": "process wall: startup, Liberty/input read, recorded mapping "
                                "calls, BLIF output and shutdown", "measurements": {}}
        for name in ("abc", "abc_sn"):
            samples, outputs = [], []
            for repetition in range(args.reps):
                mapped = work / "mapped.blif"
                mapped.unlink(missing_ok=True)
                measured = measure(f"{name}-{repetition}", [tc.bin(name), "-c", script], work,
                                   work / "logs", env=tc.env_for(tc.bin(name)), timeout=660)
                if not measured.ok or not mapped.is_file():
                    raise RuntimeError(f"{name} failed identical-network replay")
                samples.append(measured.ms)
                outputs.append(sha(mapped))
            observation["measurements"][name] = {
                "time_ms": samples, "binary_sha256": sha(tc.bin(name)),
                "netlist_sha256": outputs,
            }
        (work / "result.json").write_text(json.dumps(observation, indent=2) + "\n")
        row = Row(test=test, config=config, suite=original["suite"], tech="asap7",
                  flow="profile_abc_replay", kind="diagnostic", passed=True, cached=False,
                  measured=dt.datetime.now(dt.timezone.utc).isoformat(),
                  qor={"liberty_sha256": tc.tech("asap7").sha256, "abc_replay": observation})
        ledger.append(identity, [row])
        print(test, observation["measurements"], flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
