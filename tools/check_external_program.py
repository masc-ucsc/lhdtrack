#!/usr/bin/env python3
"""Check an external design's retained emissions with its asserting Verilator program."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.context import FlowContext
from lhdtrack.corpus import load_test
from lhdtrack.ledger import Ledger
from lhdtrack.report.external_abc_sn import write_external_color_run, write_external_run
from lhdtrack.run import Row, Runner, host_name
from lhdtrack.toolchain import Toolchain


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, help="external synthesis run ID")
    parser.add_argument("--design", required=True)
    parser.add_argument("--driver", type=Path, required=True)
    parser.add_argument("--cycles", type=int, default=1000)
    parser.add_argument("--evidence", type=Path, help="reuse an exact existing program-check JSON")
    parser.add_argument("--source-run", help="source snapshot run for a color experiment")
    parser.add_argument("--flow", choices=["syn_lhd_verilog_abc_sn_flat",
                                          "syn_lhd_verilog_abc_sn_partition"],
                        help="check just this color variant instead of the original pair")
    args = parser.parse_args()
    if args.cycles <= 0:
        parser.error("cycles must be positive")
    root, run, name = ROOT, args.run, args.design
    base = root / "var/work" / run / name / "default/asap7"
    tc = Toolchain.load(root / "var/runs" / run / "toolchain-snapshot")
    test = load_test(root / "var/runs" / (args.source_run or run) / "source/tests" / name)
    work = root / "var/runs" / run / "program-check"
    work.mkdir(parents=True, exist_ok=True)
    driver = work / args.driver.name
    shutil.copy2(args.driver.resolve(), driver)
    lhd_flow = args.flow or "syn_lhd_verilog_abc_sn"
    models = base / lhd_flow.replace("syn_lhd_", "lec_netlist_") / "models.v"
    artifact = json.loads((base / lhd_flow / "synth-artifacts.json").read_text())
    if args.flow and not models.is_file():
        # A focused color experiment need not repeat the expensive full-core
        # proof. Prepare the same Liberty models for its architectural program.
        models.parent.mkdir(parents=True, exist_ok=True)
        ctx = FlowContext(test, test.configs[0], tc.tech("asap7"), tc,
                          models.parent, models.parent / "logs")
        ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", ctx.liberty[0],
                           "--emit-dir", "lg:models", "--emit", "verilog:models.v",
                           "--workdir", "Wm"], timeout=120)
    inputs = {"rtl": test.verilog_sources(),
              "lhd_mapped": [Path(artifact["netlist"]), models]}
    if not args.flow:
        inputs["sn_mapped"] = [base / "syn_abc_sn/mapped.v", models]
    if args.evidence:
        evidence = json.loads(args.evidence.read_text())
        if evidence["driver_sha256"] != sha(driver) or evidence["versions"] != tc.versions:
            raise RuntimeError("program driver or toolchain differs from retained evidence")
        results = evidence["results"]
        for label, sources in inputs.items():
            block = results[label]
            if (block.get("stage") != "exec" or block.get("cycles") != args.cycles
                    or block["source_sha256"] != {str(p): sha(p) for p in sources}):
                raise RuntimeError(f"{label}: program evidence is incomplete or stale")
    else:
        results = {}
        for label, sources in inputs.items():
            dest = work / label
            dest.mkdir(exist_ok=True)
            ctx = FlowContext(test, test.configs[0], tc.tech("asap7"), tc,
                              dest, dest / "logs")
            obj = dest / "obj"
            ctx.run("setup", [ctx.tool("verilator"), "--cc", "--exe", "--Mdir", obj,
                              "--top-module", test.top, "-Wno-fatal", "--x-initial", "0",
                              "--x-assign", "0", "-DSYNTHESIS", "-DBR_PPA_SYNTHESIS",
                              *sources, driver], timeout=180)
            ctx.run("cc", [ctx.tool("make"), "-C", obj, "-f", f"V{test.top}.mk", "-j", "2",
                           f"CXX={ctx.tool('cxx')}", f"V{test.top}"], timeout=300)
            measured = ctx.run("exec", [obj / f"V{test.top}", "--cycles", str(args.cycles)],
                               check=False, timeout=30)
            results[label] = {"stage": "exec", "rc": measured.rc, "cycles": args.cycles,
                              "log": str(measured.log),
                              "source_sha256": {str(p): sha(p) for p in sources}}
            print(f"{label}: exit {measured.rc}", flush=True)
        evidence = {"driver_sha256": sha(driver), "versions": tc.versions, "results": results}
    (work / "result.json").write_text(json.dumps(evidence, indent=2) + "\n")
    ledger = Ledger(root)
    history = [r for r in ledger.load(host_name()) if r.get("run_id") == run]
    selected = {r["flow"]: r for r in history}
    runner = Runner(root, tc, Cache(root, enabled=False), run, load_config(root), keep_work=True)
    updates = []
    checks = [(lhd_flow, "lhd_mapped")]
    if not args.flow:
        checks.append(("syn_abc_sn", "sn_mapped"))
    for flow, label in checks:
        original = selected[flow]
        row = Row(**{k: original[k] for k in Row.__dataclass_fields__ if k in original})
        netlist = inputs[label][0]
        if row.qor.get("netlist_sha256") != sha(netlist):
            raise RuntimeError("program did not check the measured emission")
        row.qor["program_check"] = {
            "reference_rc": results["rtl"]["rc"], "netlist_rc": results[label]["rc"],
            "cycles": args.cycles, "netlist_sha256": sha(netlist),
            "driver_sha256": sha(driver), "driver": str(driver),
            "reference": results["rtl"], "implementation": results[label],
            "versions": tc.versions, "checked_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        }
        runner.gate([row])
        identity = {k: v for k, v in original.items() if k not in Row.__dataclass_fields__}
        ledger.append(identity, [row])
        updates.append(row)
        print(f"{flow}: {row.status} {row.note}", flush=True)
    if selected[lhd_flow].get("partition_diagnostic"):
        from lhdtrack.report.partition_diagnostics import write_partition_diagnostics

        print(write_partition_diagnostics(root, host_name(), name))
    else:
        report = write_external_color_run if args.flow else write_external_run
        print(report(root, host_name(), run, name))
    return int(any(row.status == "failed" for row in updates))


if __name__ == "__main__":
    raise SystemExit(main())
