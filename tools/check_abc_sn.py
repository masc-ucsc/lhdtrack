#!/usr/bin/env python3
"""Check retained SN netlists and append gated proof updates without remeasuring synthesis."""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / "src"), str(ROOT / "flows")]

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.context import FlowContext
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.run import Row, Runner, host_name
from lhdtrack.toolchain import Toolchain
from lec_netlist_verilog import independent_check


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append", required=True)
    parser.add_argument("--timeout", type=int, default=30)
    parser.add_argument("--toolchain-root", type=Path, default=ROOT)
    args = parser.parse_args()
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    tc = Toolchain.load(args.toolchain_root)
    missing = tc.missing(["lhd", "lgcheck", "yosys2"])
    if missing:
        parser.error("toolchain missing: " + ", ".join(missing))
    ledger = Ledger(ROOT)
    selected = {(r["test"], r["config"]): r for r in ledger.latest_rows(host_name())
                if r["flow"] == "syn_abc_sn" and r.get("tech") == "asap7"}
    run = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S") + "-sn-lec"
    runner = Runner(ROOT, tc, Cache(ROOT), run, load_config(ROOT), keep_work=True)
    updates = []
    for test in discover(ROOT, args.test):
        for config in test.configs:
            original = selected.get((test.name, config.id))
            if not original or not original.get("qor", {}).get("netlist_sha256"):
                print(f"skip {test.name}/{config.id}: no retained SN emission")
                continue
            for tool in ("sn", "abc_sn_mapper", "yosys"):
                if original.get("versions", {}).get(tool) != tc.version(tool):
                    raise RuntimeError(f"{tool} differs from the measured synthesis")
            if original["qor"].get("liberty_sha256") != tc.tech("asap7").sha256:
                raise RuntimeError("Liberty differs from the measured synthesis")
            for name, digest in original["qor"].get("source_sha256", {}).items():
                if sha(Path(name)) != digest:
                    raise RuntimeError(f"source changed since synthesis: {name}")
            source_run = original["qor"].get("synthesis_run_id", original["run_id"])
            netlist = (ROOT / "var/work" / source_run / test.name / config.id /
                       "asap7/syn_abc_sn/mapped.v")
            if sha(netlist) != original["qor"]["netlist_sha256"]:
                raise RuntimeError("retained SN emission differs from the measured netlist")
            work = runner.work_root / test.name / config.id / "asap7/sn-lec"
            work.mkdir(parents=True, exist_ok=True)
            ctx = FlowContext(test, config, tc.tech("asap7"), tc, work, work / "logs",
                              lec_timeout_s=args.timeout)
            ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim", ctx.liberty[0],
                               "--emit-dir", "lg:models", "--emit", "verilog:models.v",
                               "--workdir", "Wm"], timeout=args.timeout * 2 + 60)
            reference = ctx.write("original.sv", "`define SYNTHESIS\n`define BR_PPA_SYNTHESIS\n"
                                  + "\n".join(f'`include "{p}"' for p in ctx.verilog_sources())
                                  + "\n")
            proof = independent_check(ctx, netlist, reference, models=work / "models.v")
            proof.update(netlist_sha256=sha(netlist), liberty_sha256=tc.tech("asap7").sha256,
                         source_run=source_run)
            row = Row(**{name: original[name] for name in Row.__dataclass_fields__
                         if name in original})
            row.qor = {**row.qor, "synthesis_run_id": source_run}
            row.lec_aux_result = proof
            # The proof describes the retained measurement; its wall time is
            # stored in the proof block, never added to the synthesis runtime.
            runner.gate([row])
            ledger.append(runner.identity(), [row])
            updates.append(row)
            print(f"{test.name}/{config.id}: {proof['verdict']} ({proof['ms'] / 1000:.3f}s)",
                  flush=True)
    return int(any(row.status == "failed" for row in updates))


if __name__ == "__main__":
    raise SystemExit(main())
