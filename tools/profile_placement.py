#!/usr/bin/env python3
"""Sample the frozen default LiveHD commands from a retained placement run.

Profiles use fresh workdirs. Sampling overhead is diagnostic and never replaces
the serial corpus measurements. perf stat wraps each perf record command.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.ledger import Ledger
from lhdtrack.resources import measure
from lhdtrack.toolchain import Toolchain


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--test", required=True)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args()
    out = args.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    run = ROOT / "var/runs" / args.run
    tc = Toolchain.load(run)
    identity = json.loads((run / "identity.json").read_text())
    digest = hashlib.sha256(tc.bin("lhd").read_bytes()).hexdigest()
    if digest != identity["tools"]["lhd"]:
        raise ValueError("retained compiler changed")
    rows = {r["flow"]: r for r in Ledger(ROOT).load()
            if r.get("placement_run") and r["run_id"] == args.run and r["test"] == args.test}
    results = {}
    for label, flow in (("synthesis", "syn_lhd_verilog_prects_baseline"),
                        ("placement", "place_lhd_verilog")):
        row = rows[flow]
        command = row["qor"]["placement_comparison"]["measurement"]["argv"]
        directory = out / label
        directory.mkdir()
        perf_data = directory / "perf.data"
        record = [tc.bin("perf"), "record", "-e", "cycles:u", "-F", "99",
                  "--call-graph", "dwarf,8192", "-o", perf_data, "--", *command]
        print(f"profiling {label}: {args.test}", flush=True)
        measurement = measure(list(map(str, record)), directory / "measurement", cwd=directory,
            env=tc.env_for(tc.bin("lhd")), time_executable=tc.bin("time"),
            perf_executable=tc.bin("perf"))
        if measurement["status"] != "ok":
            raise RuntimeError(f"{label} profiling failed; inspect {directory}")
        report = subprocess.run([str(tc.bin("perf")), "report", "--stdio", "--no-children",
            "-g", "none", "--column-widths", "8,160",
            "--percent-limit", "0.5", "--sort=symbol", "-i", str(perf_data)],
            text=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, check=True)
        (directory / "symbols.txt").write_text(report.stdout)
        symbols = []
        for line in report.stdout.splitlines():
            match = re.match(r'^\s*(\d+\.\d+)%\s+\[.\]\s+(.*?)\s{2,}', line)
            if match:
                symbols.append({"user_cycles_percent": float(match[1]), "symbol": match[2]})
        results[label] = {"source_artifacts": row["placement_work"], "profile": str(perf_data),
                          "symbols": str(directory / "symbols.txt"), "measurement": measurement,
                          "top_symbols": symbols[:12]}
    (out / "summary.json").write_text(json.dumps({"run_id": args.run, "test": args.test,
        "lhd_sha256": digest, "sampling": "cycles:u, 99 Hz, DWARF; includes profiling overhead",
        "profiles": results}, indent=2) + "\n")
    print(out / "summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
