#!/usr/bin/env python3
"""Snapshot an external RTL design and compare the fixed ASAP7 LHD ABC / SN flows."""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.context import FlowContext
from lhdtrack.corpus import load_test
from lhdtrack.ledger import Ledger
from lhdtrack.report.external_abc_sn import write_external_run
from lhdtrack.report.verilog_eval import SN_LHD_FLOW, SN_LHD_LEC
from lhdtrack.run import Job, Runner
from lhdtrack.run import load_flows
from lhdtrack.toolchain import Toolchain
from lec_netlist_verilog import independent_check


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def snapshot(source: Path, dest: Path, top: str, clock: str, period: float) -> dict:
    """Preserve source bytes, including headers, without importing a corpus slot."""
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True).strip()
    shutil.copytree(source / "verilog", dest / "verilog",
                    ignore=shutil.ignore_patterns("abc.history"))
    provenance = {"upstream": str(source), "rev": revision,
                  "source_sha256": {str(p.relative_to(dest)): sha(p)
                                    for p in sorted((dest / "verilog").rglob("*"))
                                    if p.is_file()}}
    license_file = next((source / n for n in ("LICENSE", "COPYING")
                         if (source / n).is_file()), None)
    if license_file:
        shutil.copy2(license_file, dest / "LICENSE")
        provenance["license_sha256"] = sha(license_file)
    else:
        provenance["license"] = "No license file supplied in this upstream design directory"
    (dest / "constraints").mkdir()
    (dest / "constraints/asap7.sdc").write_text(
        f"create_clock -name clk -period {period} [get_ports {clock}]\n"
        "set_input_delay -clock clk 0.0 [all_inputs -no_clocks]\n"
        "set_output_delay -clock clk 0.0 [all_outputs]\n")
    (dest / "design.toml").write_text(
        f"[design]\nname = {json.dumps(dest.name)}\ntop = {json.dumps(top)}\n"
        'kind = "sequential"\nsuite = "external-diagnostic"\n'
        f"[provenance]\nupstream = {json.dumps(str(source))}\nrev = {json.dumps(revision)}\n"
        '[status]\npyrope = "none"\nlec = "none"\n'
        '[[config]]\nid = "default"\nparams = {}\n'
        f'[synth]\nflows = ["{SN_LHD_FLOW}", "syn_abc_sn"]\n'
        '[sim]\nflows = ["sim_verilator"]\n'
        'skip_reason = "External synthesis diagnostic has no imported checksum harness"\n'
        f'[lec]\nflows = ["{SN_LHD_LEC}"]\n')
    (dest / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    return provenance


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--design-root", type=Path, required=True)
    parser.add_argument("--top", required=True)
    parser.add_argument("--clock", default="clock")
    parser.add_argument("--period-ps", type=float, default=1000)
    parser.add_argument("--synth-timeout", type=int, default=1800)
    parser.add_argument("--lec-timeout", type=int, default=60)
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    parser.add_argument("--report-run", help="regenerate this diagnostic from the ledger only")
    args = parser.parse_args()
    source = args.design_root.resolve()
    name = source.name
    ledger = Ledger(ROOT)
    tc = Toolchain.load(args.toolchain_root)
    cfg = load_config(ROOT)
    run = (args.report_run or
           dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-external-sn")
    if args.period_ps <= 0 or args.synth_timeout <= 60 or args.lec_timeout <= 0:
        parser.error("period and LEC timeout must be positive; synthesis timeout must exceed 60s")
    cfg["run"]["lec_timeout_s"] = max(1, (args.synth_timeout - 60 + 1) // 2)
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    if args.report_run:
        print(write_external_run(ROOT, runner.identity()["host"], run, name))
        return 0
    dest = ROOT / "var/runs" / run / "source/tests" / name
    dest.parent.mkdir(parents=True)
    provenance = snapshot(source, dest, args.top, args.clock, args.period_ps)
    test = load_test(dest)
    config = test.configs[0]
    toolchain = tc.write_snapshot(ROOT / "var/runs" / run / "toolchain-snapshot")
    identity = {**runner.identity(), "large_design_diagnostic": True,
                "comparison_profile": "abc_sn_brent_sn", "measurement_jobs": 1,
                "toolchain_snapshot": str(toolchain), "external_provenance": provenance}
    flows = load_flows(ROOT)
    captured = {}

    def job(flow):
        module, path = flows[flow]
        return Job(test, config, "asap7", flow, module, path)

    def finished(row):
        # Unsupported insertion can occur after expensive, successful ABC
        # mapping. Preserve those structured diagnostics without supplying QoR
        # for a netlist that was never emitted.
        if row.flow == "syn_abc_sn" and row.status != "ok":
            work = runner.work_root / name / config.id / "asap7/syn_abc_sn"
            front, mapped = work / "design.sn.timing.json", work / "mapping.json"
            if mapped.is_file():
                mapping = json.loads(mapped.read_text())
                row.qor["timing_breakdown"] = {
                    "frontend_ms": json.loads(front.read_text()).get("timing_ms", {})
                    if front.is_file() else {},
                    "mapper_ms": mapping.get("timing_ms", {}),
                    "abc_commands": mapping.get("command_timing", []),
                }
                row.qor["unsupported_mapping"] = mapping
        runner.gate([row])
        ledger.append(identity, [row])
        captured[row.flow] = row
        print(f"{row.flow}: {row.status} {row.time_ms} {row.note}", flush=True)
        write_external_run(ROOT, identity["host"], run, name)

    print(f"run {run}: {name}, sequential ASAP7 comparison", flush=True)
    # SN first exposes elaboration/support failures before the slower LHD mapping.
    runner.execute([job("syn_abc_sn"), job(SN_LHD_FLOW)], jobs_parallel=1, on_done=finished)
    cfg["run"]["lec_timeout_s"] = args.lec_timeout
    runner.execute([job(SN_LHD_LEC), job("sim_verilator")], jobs_parallel=1, on_done=finished)
    row = captured["syn_abc_sn"]
    if row.status == "ok":
        work = runner.work_root / name / config.id / "asap7/sn-lec"
        work.mkdir(parents=True, exist_ok=True)
        ctx = FlowContext(test, config, tc.tech("asap7"), tc, work, work / "logs",
                          lec_timeout_s=args.lec_timeout)
        ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", ctx.liberty[0],
                           "--emit-dir", "lg:models", "--emit", "verilog:models.v",
                           "--workdir", "Wm"], timeout=args.lec_timeout * 2 + 60)
        reference = ctx.write("original.sv", "`define SYNTHESIS\n`define BR_PPA_SYNTHESIS\n"
                              + "\n".join(f'`include "{p}"' for p in ctx.verilog_sources()) + "\n")
        netlist = work.parent / "syn_abc_sn/mapped.v"
        if sha(netlist) != row.qor["netlist_sha256"]:
            raise RuntimeError("retained SN netlist differs from measured bytes")
        proof = independent_check(ctx, netlist, reference, models=work / "models.v")
        proof.update(netlist_sha256=sha(netlist), liberty_sha256=tc.tech("asap7").sha256,
                     source_run=run)
        row.lec_aux_result = proof
        row.qor = {**row.qor, "synthesis_run_id": run}
        finished(row)
        print(f"SN LEC: {proof['verdict']}", flush=True)
    print(write_external_run(ROOT, identity["host"], run, name), flush=True)
    return int(any(r.status == "failed" for r in captured.values()))


if __name__ == "__main__":
    raise SystemExit(main())
