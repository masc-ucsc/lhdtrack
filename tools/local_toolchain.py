#!/usr/bin/env python3
"""Build `var/toolchain/toolchain.json` from tools already on this machine.

    tools/local_toolchain.py --liberty sky130=$HAGENT_TECH_DIR/sky130_..._1v80.lib

THIS IS NOT THE HERMETIC PATH. `bazel run //:sync-toolchain` is; it builds every
tool at a pin recorded in MODULE.bazel. This escape hatch exists because that
build is long, and a developer wanting one honest measurement today should not
have to wait for it.

What it does NOT compromise is the record. The versions written here are read
from the binaries that will actually run, the Liberty is hashed, and the file is
stamped `"provenance": "local"` -- which the report prints in its header. A
local run is never mistaken for a pinned one, and its rows are keyed on those
real version strings, so they cache and compare exactly like any other.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import gzip
import hashlib
import json
import os
import platform
import re
import shutil
import subprocess
import sys
from pathlib import Path

from merge_liberty import merge as merge_liberty

# How to ask each tool its version, and where to look when it is not on PATH.
TOOLS = {
    "lhd":       (["version"],     ["../livehd/bazel-bin/lhd/lhd"]),
    "yosys":     (["-V"],          ["/opt/homebrew/bin/yosys", "/usr/local/bin/yosys"]),
    "abc":       (["-h"],          ["/opt/homebrew/bin/yosys-abc", "/usr/local/bin/yosys-abc"]),
    "verilator": (["--version"],   ["/opt/homebrew/bin/verilator", "/usr/local/bin/verilator"]),
    # OpenSTA has no Homebrew formula and is not in the BCR; a local build
    # usually lands in ~/bin. The pinned path is packages/opensta.BUILD.
    "sta":       (["-version"],    ["/opt/homebrew/bin/sta", "/usr/local/bin/sta",
                                    str(Path.home() / "bin" / "sta")]),
}


# `lhd` is searched in the LiveHD checkout FIRST. A copy of lhd on PATH is
# usually an older build, and benchmarking last week's compiler while reporting
# today's date is worse than not running at all. Everything else prefers PATH.
CHECKOUT_FIRST = {"lhd"}


def find(name: str, extra: list[str], root: Path) -> Path | None:
    order = [*extra, None] if name in CHECKOUT_FIRST else [None, *extra]
    for cand in order:
        if cand is None:
            hit = shutil.which(name)
            if hit:
                return Path(hit).resolve()
            continue
        p = (root / cand).resolve() if not cand.startswith("/") else Path(cand)
        if p.exists() and os.access(p, os.X_OK):
            return p
    return None


def version_of(path: Path, args: list[str]) -> str:
    try:
        p = subprocess.run(  # noqa: S603
            [str(path), *args], capture_output=True, text=True, timeout=60
        )
        for line in (p.stdout + p.stderr).splitlines():
            if line.strip():
                return line.strip()
    except (subprocess.SubprocessError, OSError) as e:
        return f"unavailable: {e}"
    return "unknown"


# `time_unit : "1ps";` -- the unit EVERY delay from this library is expressed
# in, including the SDC period and whatever a timer reports. sky130 is 1ns,
# ASAP7 is 1ps, and a report that labels both "ns" is off by 1000x.
_TIME_UNIT = re.compile(r'time_unit\s*:\s*"?\s*(\d*)\s*([munpf]?s)\s*"?', re.I)


def time_unit_of(path: Path) -> str:
    """The Liberty's time unit, e.g. "ns" or "ps"; "" when it does not say."""
    try:
        with path.open(errors="replace") as fh:
            for _ in range(4000):          # it is in the header, not the cells
                line = fh.readline()
                if not line:
                    break
                m = _TIME_UNIT.search(line)
                if m:
                    mult, unit = m.group(1) or "1", m.group(2).lower()
                    return unit if mult == "1" else f"{mult}{unit}"
    except OSError:
        pass
    return ""


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _expand(pattern: str) -> list[Path]:
    """Glob a Liberty spec, absolute or relative, .lib or .lib.gz."""
    if any(c in pattern for c in "*?["):
        root, rest = (Path("/"), pattern.lstrip("/")) if pattern.startswith("/") else (Path(), pattern)
        return sorted(p for p in root.glob(rest) if p.is_file())
    p = Path(pattern)
    return [p] if p.is_file() else []


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    ap.add_argument(
        "--liberty", action="append", default=[],
        help="'<tech>=<path-to-.lib>' (repeatable). Globs are expanded.",
    )
    args = ap.parse_args()

    out = args.root / "var" / "toolchain"
    (out / "bin").mkdir(parents=True, exist_ok=True)

    # `yosys_slang` is a shared-library plugin rather than an executable, so it
    # does not belong in TOOLS/find(). Preserve a valid plugin staged by the
    # hermetic toolchain (the common local-development path), or accept an
    # explicit YOSYS_SLANG path when bootstrapping a local toolchain directly.
    old_slang_link = out / "bin" / "yosys_slang"
    old_slang = old_slang_link.resolve() if old_slang_link.exists() else None

    bins, versions = {}, {}
    for name, (vargs, extra) in TOOLS.items():
        path = find(name, extra, args.root)
        if path is None:
            print(f"  {name:<10} not found -- flows needing it will SKIP and say so")
            continue
        link = out / "bin" / name
        if link.is_symlink() or link.exists():
            link.unlink()
        link.symlink_to(path)
        bins[name] = str(path)
        versions[name] = version_of(path, vargs)
        print(f"  {name:<10} {versions[name][:78]}")

    slang_env = os.environ.get("YOSYS_SLANG", "")
    slang = Path(slang_env).expanduser().resolve() if slang_env else old_slang
    if slang is not None and slang.is_file():
        if old_slang_link.is_symlink() or old_slang_link.exists():
            old_slang_link.unlink()
        old_slang_link.symlink_to(slang)
        bins["yosys_slang"] = str(slang)
        versions["yosys_slang"] = f"sha256:{sha256(slang)[:16]}"
        print(f"  {'yosys_slang':<10} {versions['yosys_slang']}")
    else:
        print("  yosys_slang not found -- syn_yosys_abc will SKIP and say so")

    tech = {}
    for spec in args.liberty:
        name, _, pattern = spec.partition("=")
        libs = _expand(pattern)
        if not libs:
            print(f"  liberty {name}: nothing matched {pattern}", file=sys.stderr)
            continue
        # A .gz Liberty is decompressed into the staging area: neither yosys nor
        # OpenSTA reads gzip, and lambdapdk ships ASAP7 compressed.
        staged = []
        libdir = out / "lib" / name
        libdir.mkdir(parents=True, exist_ok=True)
        for lib in libs:
            if lib.suffix == ".gz":
                dest = libdir / lib.stem
                if not dest.exists() or dest.stat().st_mtime < lib.stat().st_mtime:
                    with gzip.open(lib, "rb") as src, dest.open("wb") as fh:
                        shutil.copyfileobj(src, fh)
                staged.append(dest)
            else:
                staged.append(lib.resolve())
        # LiveHD and Yosys's ABC mapping each accept one Liberty path. ASAP7 is
        # commonly installed as five family files, so make the local escape
        # hatch obey the same one-library contract as the hermetic toolchain.
        if name == "asap7" and len(staged) > 1:
            merged = libdir / "asap7sc7p5t_RVT_TT_merged.lib"
            merged.write_text(merge_liberty(staged))
            staged = [merged]
        unit = next((u for u in (time_unit_of(p) for p in staged) if u), "")
        tech[name] = {
            "liberty": [str(p) for p in staged],
            "sha256": hashlib.sha256("".join(sha256(p) for p in staged).encode()).hexdigest()[:16],
            "time_unit": unit,
        }
        print(f"  liberty {name:<6} {len(staged)} file(s), {unit or '?'}, {tech[name]['sha256']}")

    doc = {
        "schema_version": 1,
        # Recorded and surfaced in the report header: these tools came from the
        # machine, not from a pin, and a reader must be able to see that.
        "provenance": "local",
        "generated": _dt.datetime.now(_dt.timezone.utc).isoformat(timespec="seconds"),
        "host_class": f"{platform.system()}-{platform.machine()}",
        "bin": bins,
        "env": {},
        "versions": versions,
        "tech": tech,
    }
    (out / "toolchain.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"\nwrote {out / 'toolchain.json'}  (provenance: local, NOT the pinned toolchain)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
