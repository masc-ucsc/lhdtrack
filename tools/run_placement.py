#!/usr/bin/env python3
"""Serial corpus ASAP7 comparison through synthesis and pre-CTS, with perf stat."""
from __future__ import annotations

import argparse
from dataclasses import replace
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cache import Cache
from lhdtrack.cli import load_config
from lhdtrack.corpus import discover
from lhdtrack.ledger import Ledger
from lhdtrack.report.prects import RUN_FLOWS, render
from lhdtrack.run import Job, Runner, host_name, load_flows
from lhdtrack.srams import prepare as prepare_srams
from lhdtrack.toolchain import Toolchain


def sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--test", action="append")
    parser.add_argument("--flow", action="append", choices=[f for f, _ in RUN_FLOWS])
    parser.add_argument("--run", help="fresh run identifier")
    args = parser.parse_args()
    tc = Toolchain.load(ROOT)
    cfg = load_config(ROOT)
    cfg["cache"]["baseline_flows"] = []
    cfg["run"]["jobs"] = 1
    if os.environ.get("LHDTRACK_LHD_SET") or os.environ.get("LHDTRACK_ABC_SATOPT"):
        raise SystemExit("clear synthesis experiment environment overrides for this comparison")
    lock_path = ROOT / "var/placement.lock"
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    lock = lock_path.open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    run = args.run or dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%S-placement")
    run_dir = ROOT / "var/runs" / run
    run_dir.mkdir(parents=True, exist_ok=False)
    tests = discover(ROOT, args.test or ["add", "br_counter", "br_amba_axi_demux"])
    frozen = []
    for test in tests:
        destination = run_dir / "inputs/tests" / test.name
        shutil.copytree(test.root, destination, symlinks=False)
        frozen.append(replace(test, root=destination))
    tests = frozen
    sram_configs = [t.root / "placement/srams.json" for t in tests
                    if (t.root / "placement/srams.json").exists()]
    tc = prepare_srams(tc, sram_configs, run_dir / "srams")
    tc.write_snapshot(run_dir)
    tc = Toolchain.load(run_dir)
    flows = load_flows(ROOT)
    runner = Runner(ROOT, tc, Cache(ROOT, enabled=False), run, cfg, keep_work=True)
    identity = runner.identity()
    library_sha = sha(tc.tech("asap7").liberty[0])
    if hashlib.sha256(library_sha.encode()).hexdigest()[:16] != tc.tech("asap7").sha256:
        raise SystemExit("staged Liberty bytes differ from toolchain registry")
    identity.update(placement_run=True, liberty_sha256=library_sha)
    harness = run_dir / "harness"
    harness.mkdir()
    sources = [Path(__file__), ROOT / "src/lhdtrack/resources.py",
               ROOT / "flows/lib/placement.py", ROOT / "flows/lib/physical_steps.py",
               ROOT / "flows/lib/lhd_synth_policy.py", ROOT / "flows/qor_endpoint.py",
               ROOT / "flows/lib/logic_depth.py", ROOT / "src/lhdtrack/run.py",
               ROOT / "src/lhdtrack/context.py", ROOT / "src/lhdtrack/corpus.py",
               ROOT / "src/lhdtrack/metrics.py", ROOT / "src/lhdtrack/toolchain.py",
               ROOT / "src/lhdtrack/srams.py", ROOT / "src/lhdtrack/report/prects.py"]
    for path in sources:
        destination = harness / path.relative_to(ROOT)
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
    selected = args.flow or [name for name, _ in RUN_FLOWS]
    for name in selected:
        source = flows[name][1]
        shutil.copy2(source, harness / source.name)
    tools = {key: sha(tc.bin(key)) for key in
             ("lhd", "yosys", "openroad", "make", "python", "perf", "time")}
    physical_files = [*tc.physical["lefs"], *[tc.physical[k] for k in (
        "tech_rc", "slang", "synth_script", "score_script")]]
    identity_path = run_dir / "identity.json"
    identity_path.write_text(json.dumps({**identity, "tools": tools,
        "physical": tc.physical, "physical_sha256": {p: sha(Path(p)) for p in physical_files},
        "harness_sha256": {str(p.relative_to(harness)): sha(p)
                           for p in harness.rglob("*") if p.is_file()},
        "tests": [t.name for t in tests], "flows": selected}, indent=2) + "\n")
    identity["placement_identity"] = str(identity_path)
    out = ROOT / "target/results-placement-llmhdl.html"
    ledger = Ledger(ROOT)
    failures = 0
    total = sum(len(t.configs) for t in tests) * len(selected)
    done = 0
    print(f"run {run}: {total} serial measurements\nreport: {out}", flush=True)
    for name in selected:
        module, path = flows[name]
        for test in tests:
            for config in test.configs:
                # A later build or staging operation must not mix tool bytes.
                if tools != {key: sha(tc.bin(key)) for key in tools}:
                    raise RuntimeError("frozen executable changed during comparison")
                print(f"running {test.name}/{config.id}/{name}", flush=True)
                row = runner.execute([Job(test, config, "asap7", name, module, path)],
                                     jobs_parallel=1)[0]
                work = runner.work_root / test.name / config.id / "asap7" / name
                record = {**row.to_dict(), "placement_work": str(work)}
                ledger.append(identity, [record])
                if work.is_dir():
                    (work / "row.json").write_text(
                        json.dumps({**identity, **record}, indent=2) + "\n")
                render(ROOT, out, host=host_name(), run=run)
                failures += row.status == "failed"
                done += 1
                print(f"[{done}/{total}] {test.name}/{name}: {row.status} {row.note}", flush=True)
    print(f"complete: {failures} failed measurements; {out}", flush=True)
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
