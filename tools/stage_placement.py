#!/usr/bin/env python3
"""Register an explicit frozen HighTide tool set for the pre-CTS comparison.

Staging accepts executable paths; measurement never searches PATH. Run
stage_lhd.py afterwards to replace the manifest's older lhd with today's opt build.
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
from pathlib import Path
import platform
import shutil
import subprocess


def sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--tools", type=Path, required=True)
    parser.add_argument("--perf", type=Path, required=True)
    parser.add_argument("--time", type=Path, required=True)
    parser.add_argument("--git", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    source = json.loads(args.tools.read_text())
    path = root / "var/toolchain/toolchain.json"
    doc = json.loads(path.read_text()) if path.exists() else {}
    bins = {key: source[key] for key in ("lhd", "yosys", "openroad", "make", "python")}
    bins.update(perf=str(args.perf.resolve()), time=str(args.time.resolve()),
                git=str(args.git.resolve()))
    if not all(Path(p).is_file() for p in bins.values()):
        raise ValueError("every registered executable must exist")
    doc.setdefault("bin", {}).update(bins)
    doc.setdefault("versions", {}).update({key: "sha256:" + sha(Path(p))
                                          for key, p in bins.items()})
    doc.setdefault("env", {})["lhd"] = {"RUNFILES_DIR": source["lhd"] + ".runfiles"}
    library = Path(source["liberty"])
    digest = hashlib.sha256(sha(library).encode()).hexdigest()[:16]
    doc.setdefault("tech", {})["asap7"] = {"liberty": [str(library)], "sha256": digest,
                                           "time_unit": "ps"}
    physical = {key: source[key] for key in ("flow_home", "platform", "lefs", "tech_rc",
                                            "slang", "threads", "synth_script")}
    physical["build_description"] = source.get("build_description", "unspecified")
    physical["source_manifest"] = str(args.tools.resolve())
    # Keep the scorer used by this registry immutable, including its exact revision.
    scorer = Path(source["hightide"]) / "tools/vslivehd/score_flow.tcl"
    frozen = root / "var/toolchain/physical" / sha(scorer)[:16]
    frozen.mkdir(parents=True, exist_ok=True)
    shutil.copy2(scorer, frozen / "score.tcl")
    physical["score_script"] = str(frozen / "score.tcl")
    shutil.copy2(Path(source["hightide"]) / "tools/vslivehd/merge_liberty.py",
                 frozen / "merge_liberty.py")
    physical["merge_script"] = str(frozen / "merge_liberty.py")
    fakeram = Path(source["hightide"]) / "tools/bsg_fakeram"
    if (fakeram / "scripts/run.py").is_file():
        revision = subprocess.check_output(
            [str(args.git), "-C", str(fakeram), "rev-parse", "HEAD"], text=True).strip()
        destination = root / "var/toolchain/fakeram" / revision
        if not destination.exists():
            shutil.copytree(fakeram / "scripts", destination / "scripts",
                            ignore=shutil.ignore_patterns("__pycache__"))
            shutil.copy2(fakeram / "LICENSE", destination / "LICENSE")
            shutil.copy2(fakeram / "NOTICE", destination / "NOTICE")
        physical["fakeram_generator"] = str(destination / "scripts/run.py")
        physical["fakeram_revision"] = revision
    physical["hightide_revision"] = subprocess.check_output(
        [str(args.git), "-C", source["hightide"], "rev-parse", "HEAD"], text=True).strip()
    doc["physical"] = physical
    doc["host_class"] = f"{platform.system()}-{platform.machine()}-{platform.node()}"
    doc["generated"] = dt.datetime.now(dt.timezone.utc).isoformat()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(doc, indent=2) + "\n")
    print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
