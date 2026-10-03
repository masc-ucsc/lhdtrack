#!/usr/bin/env python3
"""Repeat direct lgcheck against a retained USYN emission and original Verilog."""
import argparse
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.report.verilog_eval import bounded_yosys_evidence
from lhdtrack.toolchain import Toolchain


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def check(row, variant, tc, dest, steps, timeout, bmc_only):
    result = dict(test=row["test"], config=row["config"], steps=steps, bmc_only=bmc_only)
    v = row["variants"][variant]
    proof = v["proof"].get("lec_aux_result")
    if not proof:
        if v["synthesis"].get("status") == "ok":
            return dict(result, verdict="error", reason="Missing retained lgcheck artifacts")
        return dict(result, verdict="skipped", reason=v["synthesis"].get("note", "No emission"))
    work = Path(proof["log"]).parents[1]
    old = work / "oracle"
    artifacts = json.loads((work.parent / v["synthesis"]["flow"]
                            / "synth-artifacts.json").read_text())
    raw = Path(artifacts["netlist"]).read_bytes()
    models = (work / "models.v").read_bytes()
    if digest(raw) != proof["netlist_sha256"] or digest(raw) != artifacts["sha256"]:
        raise ValueError("emission differs from measured netlist digest")
    if digest(models) != proof["models_sha256"]:
        raise ValueError("Liberty model bytes differ from the retained proof")
    if (old / "impl.v").read_bytes() != raw + b"\n" + models:
        raise ValueError("lgcheck implementation differs from exact measured emission plus models")
    for path, expected in v["synthesis"]["qor"]["source_sha256"].items():
        if digest(Path(path).read_bytes()) != expected:
            raise ValueError(f"corpus source changed: {path}")
    previous = json.loads((old / "result.json").read_text())["argv"]
    argv = [str(tc.bin("lgcheck")), "--yosys", str(tc.bin("yosys2")),
            "--implementation", str(old / "impl.v"), "--reference", str(old / "ref.v"),
            "--top", previous[previous.index("--top") + 1],
            "--gold_reader", "slang", "--gate_reader", "slang"]
    dest.mkdir(parents=True, exist_ok=True)
    env = {**os.environ, **tc.env_for(tc.bin("lhd")), "LGCHECK_BMC_STEPS": str(steps),
           "LGCHECK_EQUIV_TIMEOUT": str(timeout), "LGCHECK_SLANG_THREADS": "1",
           "LGCHECK_BMC_ONLY": "1" if bmc_only else "0"}
    log = dest / "lgcheck.log"
    start = time.monotonic()
    with log.open("w") as stream:
        try:
            p = subprocess.run(argv, cwd=dest, env=env, stdout=stream,
                               stderr=subprocess.STDOUT, timeout=timeout * 2 + 60)
            rc = p.returncode
        except subprocess.TimeoutExpired:
            rc = 124
    verdict = {0: "proven", 1: "refuted", 2: "inconclusive", 124: "timeout"}.get(rc, "error")
    result.update(verdict=verdict, bounded=False, bound=None, rc=rc, argv=argv, log=str(log),
                  ms=round((time.monotonic() - start) * 1000), invocation="direct-lgcheck",
                  netlist_sha256=digest(raw), models_sha256=digest(models),
                  implementation_with_models_sha256=digest((old / "impl.v").read_bytes()),
                  reference_with_models_sha256=digest((old / "ref.v").read_bytes()),
                  lgcheck_sha256=digest(tc.bin("lgcheck").read_bytes()))
    def read(path):
        return path.read_text() if path.exists() else ""
    result = bounded_yosys_evidence(result, log.read_text(), read(dest / "lgcheck_bmc.log"),
                                    read(dest / "lgcheck_bmc.err"))
    (dest / "result.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--summary", type=Path, required=True)
    parser.add_argument("--variant", default="improved")
    parser.add_argument("--test", action="append")
    parser.add_argument("--steps", type=int, default=6)
    parser.add_argument("--timeout", type=int, default=300)
    parser.add_argument("--jobs", type=int, default=16)
    parser.add_argument("--bmc-only", action="store_true")
    parser.add_argument("--unresolved-only", action="store_true")
    parser.add_argument("--output", type=Path, required=True,
                        help="retained JSON results")
    args = parser.parse_args()
    if min(args.steps, args.timeout, args.jobs) < 1:
        parser.error("steps, timeout and jobs must be positive")
    matrix = json.loads(args.summary.read_text())
    rows = []
    for row in matrix["rows"]:
        if args.test and row["test"] not in args.test:
            continue
        b = row["variants"][args.variant]["proof"].get("lec_aux_result", {})
        if args.unresolved_only and (not b or (b.get("verdict") == "proven"
                                              and not b.get("bounded"))):
            continue
        rows.append(row)
    tc = Toolchain.load(ROOT)
    run_id = "lgcheck-audit-" + dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    work = ROOT / "var/work" / run_id
    results = []
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        jobs = {pool.submit(check, r, args.variant, tc, work / r["test"] / r["config"],
                            args.steps, args.timeout, args.bmc_only): r for r in rows}
        for job in as_completed(jobs):
            row = jobs[job]
            try:
                result = job.result()
            except (OSError, ValueError, KeyError) as error:
                result = dict(test=row["test"], config=row["config"], verdict="error",
                              reason=str(error))
            results.append(result)
            ordered = sorted(results, key=lambda r: (r["test"], r["config"]))
            args.output.write_text(json.dumps(ordered, indent=2) + "\n")
            print(len(results), result["test"], result["verdict"],
                  result.get("bounded"), flush=True)
    print(json.dumps(dict(Counter(r["verdict"] for r in results)), sort_keys=True))
    if any(r["verdict"] in {"refuted", "error"} for r in results):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
