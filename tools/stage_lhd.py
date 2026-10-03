#!/usr/bin/env python3
"""Stage the LiveHD checkout's `-c opt` lhd as this machine's lhdtrack evaluation copy.

`bazel run //:sync-toolchain` rebuilds every pinned tool and cannot build OpenSTA
on a host without system flex/bison/swig; `make toolchain-local` symlinks the
checkout's bazel-bin binary, which the next `bazel build` silently replaces
under a running regression. This takes the middle road the hand-staged
`var/toolchain/eval/<sha>-<rev>/` copies took: COPY the binary and its runfiles
(symlinks dereferenced, so a later bazel build cannot move the ground under a
measurement) into a directory named by the binary's digest and git revision,
then point `var/toolchain/toolchain.json` at it the way the eval tools expect:

    bin.lhd / bin.lgcheck / bin.yosys2       inside the staged runfiles
    env.lhd.RUNFILES_DIR                     the staged runfiles
    versions.lhd                             "livehd <rev>[+dirty] opt sha256:<16>"
    versions.lgcheck / versions.yosys2       sha256 of the staged scripts (lgcheck
                                             also carries its RTLIL adapter's)
    livehd_source_revision / _diff_sha256    what the binary was built from

Every other tool, the Liberty files and the technologies are left as they are.
Usage: tools/stage_lhd.py [--livehd ../livehd] [--root <lhdtrack>] [--dry-run]
"""

from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path


def sha16(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()[:16]


def git(livehd: Path, *args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=livehd, text=True).strip()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument("--livehd", type=Path, default=None, help="LiveHD checkout (default: <root>/../livehd)")
    ap.add_argument("--dry-run", action="store_true", help="print what would be staged, change nothing")
    args = ap.parse_args()
    root: Path = args.root.resolve()
    livehd: Path = (args.livehd or root.parent / "livehd").resolve()

    bin_dir = Path(subprocess.check_output(["bazel", "info", "-c", "opt", "bazel-bin"], cwd=livehd, text=True).strip())
    source = bin_dir / "lhd" / "lhd"
    runfiles = bin_dir / "lhd" / "lhd.runfiles"
    if not source.is_file() or not runfiles.is_dir():
        print(f"no -c opt lhd at {source} -- run `bazel build -c opt //lhd:lhd` in {livehd} first", file=sys.stderr)
        return 1

    digest = sha16(source)
    revision = git(livehd, "rev-parse", "--short=9", "HEAD")
    full_revision = git(livehd, "rev-parse", "HEAD")
    diff = subprocess.check_output(["git", "diff", "HEAD"], cwd=livehd)
    dirty = bool(diff.strip())
    dest = root / "var" / "toolchain" / "eval" / f"{digest}-{revision}{'-dirty' if dirty else ''}"
    version = f"livehd {revision}{'+dirty' if dirty else ''} opt sha256:{digest}"
    print(f"stage {source}\n  -> {dest}\n  {version}")
    if args.dry_run:
        return 0

    if not (dest / "lhd").exists() or sha16(dest / "lhd") != digest:
        if dest.exists():
            shutil.rmtree(dest)
        dest.mkdir(parents=True)
        shutil.copy2(source, dest / "lhd")
        # Dereference: a runfiles tree is symlinks into the execroot, and the
        # execroot is exactly what the next build rewrites.
        shutil.copytree(runfiles, dest / "lhd.runfiles", symlinks=False)
        (dest / "source.diff").write_bytes(diff)
    staged_runfiles = dest / "lhd.runfiles"
    yosys_dir = staged_runfiles / "_main" / "inou" / "yosys"
    lgcheck = yosys_dir / "lgcheck"
    yosys2 = yosys_dir / "yosys2"
    adapter = yosys_dir / "rtlil_split_port_adapter.py"
    for required in (lgcheck, yosys2, adapter):
        if not required.is_file():
            print(f"staged runfiles lack {required}", file=sys.stderr)
            return 1

    tc_path = root / "var" / "toolchain" / "toolchain.json"
    tc = json.loads(tc_path.read_text())
    tc.setdefault("bin", {})
    tc.setdefault("env", {})
    tc.setdefault("versions", {})
    tc["bin"]["lhd"] = str(dest / "lhd")
    tc["bin"]["lgcheck"] = str(lgcheck)
    tc["bin"]["yosys2"] = str(yosys2)
    env_lhd = dict(tc["env"].get("lhd", {}))
    env_lhd["RUNFILES_DIR"] = str(staged_runfiles)
    env_lhd.setdefault("LGCHECK_EQUIV_TIMEOUT", "300")
    env_lhd.setdefault("LGCHECK_SLANG_THREADS", "1")
    tc["env"]["lhd"] = env_lhd
    tc["versions"]["lhd"] = version
    tc["versions"]["lgcheck"] = f"sha256:{sha16(lgcheck)} adapter-sha256:{sha16(adapter)}"
    tc["versions"]["yosys2"] = f"sha256:{sha16(yosys2)}"
    tc["livehd_source_revision"] = full_revision
    tc["livehd_source_diff_sha256"] = hashlib.sha256(diff).hexdigest()
    tc["generated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tc_path.write_text(json.dumps(tc, indent=2) + "\n")
    print(f"toolchain.json -> {version}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
