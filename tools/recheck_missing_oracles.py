#!/usr/bin/env python3
"""Complete independent checks that the lhd cross-driver never reached.

The native proof is retained. Each replacement oracle result records its exact
netlist, cell-model digest, command, log, timing, and predecessor measurement.
Original ledger rows and all original solver logs remain available.
"""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.metrics import measure
from lhdtrack.report.verilog_eval import bounded_yosys_evidence, select_rows, write_evaluation


def read(path):
    return path.read_text() if path.exists() else ""


def key(row):
    return row["test"], row["config"], row["flow"]


def workdir(row):
    return (ROOT / "var/work" / row["run_id"] / row["test"]
            / row["config"] / row["tech"] / row["flow"])


def needs_oracle(row):
    block = row.get("lec_aux_result", {})
    if not block or block.get("recheck") or block.get("invocation") == "direct-lgcheck":
        return False
    logs = workdir(row) / "LW-lec_lgyosys_verilog_netlist/logs"
    return not any("using yosys:" in read(p) for p in logs.glob("*lgcheck*"))


def execute(row, toolchain):
    work = workdir(row)
    mapper = "syn_lhd_verilog_usyn" if "_usyn" in row["flow"] else "syn_lhd_verilog"
    artifact = json.loads((work.parent / mapper / "synth-artifacts.json").read_text())
    netlist = Path(artifact["netlist"]).read_bytes()
    if hashlib.sha256(netlist).hexdigest() != row["lec_aux_result"]["netlist_sha256"]:
        raise RuntimeError(f"retained netlist changed: {work}")

    # This is the same Liberty-derived model text the native Verilog loader
    # materialized before reading the original reference. Neither DUT is
    # round-tripped through LiveHD for the independent oracle.
    models = work / "LW-lec_cvc5_verilog_netlist/check_ref_models0.v"
    model_text = models.read_bytes()
    if not model_text or b"endmodule" not in model_text:
        raise RuntimeError(f"incomplete cell model emission: {models}")
    dest = ROOT / "var/runs" / row["run_id"] / "oracle-direct"
    dest = dest / row["test"] / row["config"] / row["flow"]
    dest.mkdir(parents=True, exist_ok=True)
    reference = (work / "original-rtl.sv").read_bytes()
    (dest / "check_impl.v").write_bytes(netlist + b"\n" + model_text + b"\n")
    (dest / "check_ref.v").write_bytes(reference + b"\n" + model_text + b"\n")

    timeout = row["lec_aux_result"]["timeout_s"]
    wall = row["lec_aux_result"]["wall_limit_s"]
    env = {**toolchain["env"]["lhd"], "LGCHECK_BMC_STEPS": "6",
           "LGCHECK_EQUIV_TIMEOUT": str(timeout)}
    check = Path(env["RUNFILES_DIR"]) / "_main/inou/yosys/lgcheck"
    yosys = check.parent / "yosys2"
    test = next(t for t in discover(ROOT, [row["test"]]) if t.name == row["test"])
    argv = [str(check), "--yosys", str(yosys),
            "--implementation", str(dest / "check_impl.v"),
            "--reference", str(dest / "check_ref.v"),
            "--implementation_top", test.top, "--reference_top", test.top,
            "--gold_reader", "slang", "--gate_reader", "slang"]
    measured = measure("lgcheck", argv, dest, dest / "logs", env=env, timeout=wall)
    verdict = ("timeout" if measured.timed_out else
               {0: "proven", 1: "refuted", 2: "inconclusive"}.get(measured.rc, "error"))
    block = {**row["lec_aux_result"], "verdict": verdict, "ms": measured.ms,
             "bounded": False, "bound": None}
    block.pop("reason", None)
    block = bounded_yosys_evidence(block, read(measured.log),
                                  read(dest / "lgcheck_bmc.log"), read(dest / "lgcheck_bmc.err"))
    if block["verdict"] in ("refuted", "error", "inconclusive"):
        block["reason"] = measured.tail()[-2000:]
    block["recheck"] = {
        "measured": dt.datetime.now(dt.timezone.utc).isoformat(),
        "log": str(measured.log), "invocation": "direct-lgcheck",
        "lgcheck_sha256": hashlib.sha256(check.read_bytes()).hexdigest(),
        "models_sha256": hashlib.sha256(model_text).hexdigest(),
        "exit_code": measured.rc, "original_ms": row["lec_aux_result"]["ms"],
        "reason": "cross-driver timeout before independent oracle launch",
    }
    result = {"original_row": row, "block": block, "argv": argv,
              "peak_rss_kb": measured.peak_rss_kb}
    (dest / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def publish(result, spec_path, host):
    original, block = result["original_row"], result["block"]
    row = {**original, "lec_aux_result": block,
           "note": "Independent lgcheck run directly because the cross-driver timed out before launching Yosys."}
    label = "lec_lgyosys_verilog_netlist"
    row["time_ms"] = {**row["time_ms"], label: block["ms"]}
    row["time_ms"]["total"] = sum(v for k, v in row["time_ms"].items() if k != "total")
    row["peak_rss_kb"] = {**row["peak_rss_kb"], label: result["peak_rss_kb"]}
    row["peak_rss_kb"]["max"] = max(v for k, v in row["peak_rss_kb"].items() if k != "max")
    if block["verdict"] in ("refuted", "error"):
        row["status"], row["passed"] = "failed", False

    # One append syscall per complete row, including when the main sweep is
    # appending other rows. Never rewrite the ledger while it is active.
    fd = os.open(Ledger(ROOT).path_for(host), os.O_WRONLY | os.O_APPEND)
    try:
        data = (json.dumps(row, sort_keys=True) + "\n").encode()
        if os.write(fd, data) != len(data):
            raise OSError("incomplete ledger append")
    finally:
        os.close(fd)
    public = ROOT / "data" / f"verilog-eval-{host}.json"
    try:
        active = json.loads(public.read_text()).get("run_id") == row["run_id"]
    except (OSError, json.JSONDecodeError):
        active = False
    filename = f"report-{host}.html" if active else f"eval-{row['run_id']}.html"
    write_evaluation(ROOT, spec_path, out=ROOT / "target" / filename)
    print(*key(row), block["verdict"], block["ms"], flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--watch", action="store_true",
                        help="continue auditing until the parent sweep completes")
    args = parser.parse_args()
    spec_path = ROOT / "var/runs" / args.run / "evaluation.json"
    spec = json.loads(spec_path.read_text())
    host = spec["host"]
    toolchain = json.loads((spec_path.parent / "toolchain.json").read_text())
    submitted, pending = set(), {}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        while True:
            try:
                spec = json.loads(spec_path.read_text())
            except json.JSONDecodeError:
                time.sleep(1)
                continue
            if spec["run_id"] != args.run:
                raise RuntimeError("evaluation identity changed during oracle audit")
            rows = select_rows(Ledger(ROOT).load(host), spec)
            for row in rows.values():
                if key(row) in submitted or not needs_oracle(row):
                    continue
                submitted.add(key(row))
                pending[pool.submit(execute, row, toolchain)] = key(row)
                print("Queued independent oracle", *key(row), flush=True)
            for future in list(pending):
                if future.done():
                    publish(future.result(), spec_path, host)
                    del pending[future]
            if (not args.watch or spec.get("phase") == "complete") and not pending:
                break
            time.sleep(15)
    print("All completed netlist checks reached an independent oracle.", flush=True)


if __name__ == "__main__":
    main()
