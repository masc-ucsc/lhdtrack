#!/usr/bin/env bash
# Stage every tool in the measured path into var/toolchain/, with its version
# string, and write the toolchain.json the python runner reads.
#
# This is the ONLY bridge between bazel and the runner. The runner never shells
# out to bazel and never reads PATH -- if a binary is not named in
# toolchain.json it does not exist as far as lhdtrack is concerned. That is what
# makes the baseline cache key trustworthy: a tool cannot change underneath a
# cached result without changing the key.
set -euo pipefail

# BUILD_WORKSPACE_DIRECTORY is set by `bazel run`; fall back for direct calls.
ROOT="${BUILD_WORKSPACE_DIRECTORY:-$(cd "$(dirname "$0")/.." && pwd)}"
OUT="$ROOT/var/toolchain"
RF="${RUNFILES_DIR:-${TEST_SRCDIR:-$0.runfiles}}"

mkdir -p "$OUT/bin" "$OUT/lib" "$OUT/share"

# find_runfile RELATIVE_PATH -- resolve a data dep to an absolute path.
find_runfile() {
  local hit
  hit=$(find -L "$RF" -type f -name "$1" -print -quit 2>/dev/null || true)
  [ -n "$hit" ] || { echo "sync-toolchain: cannot find '$1' in runfiles" >&2; return 1; }
  printf '%s\n' "$hit"
}

# stage NAME FILENAME -- symlink one tool into var/toolchain/bin under a stable
# name. A symlink, not a copy: bazel's output is already content-addressed, and
# copying would let var/ drift from what bazel actually built.
stage() {
  local name=$1 file=$2 src
  src=$(find_runfile "$file") || return 1
  ln -sfn "$src" "$OUT/bin/$name"
  printf '  %-10s %s\n' "$name" "$src"
}

echo "staging binaries into $OUT/bin"
stage lhd       lhd
stage yosys     yosys
stage yosys_slang slang.so
# The @yosys executable owns the exact ABC helper its `abc` pass invokes.
stage abc       yosys-abc
stage verilator verilator
stage sta       sta

# Bazel-built tools keep non-binary runtime data in the runfiles tree. Preserve
# stable links to that tree and record the environment below; resolving only an
# executable path makes Verilator look under @invalid@ and makes LiveHD lose
# slop.hpp/iassert.hpp during the run-only simulation leg.
ln -sfn "$RF" "$OUT/runfiles"

# The BCR Verilator binary exports its runtime headers but currently omits the
# configured `verilated.mk` used by its normal `--cc --exe` workflow. Assemble
# an install-shaped runtime root from those runfiles and render that one
# configure output for THIS execution image -- which is not always Linux, so the
# handful of substitutions that differ per platform are chosen below rather than
# hardcoded.
VERILATOR_SHARE="$OUT/share/verilator"
if [ -L "$VERILATOR_SHARE" ]; then
  unlink "$VERILATOR_SHARE"
fi
mkdir -p "$VERILATOR_SHARE/include"
for entry in "$RF/verilator+"/include/*; do
  ln -sfn "$entry" "$VERILATOR_SHARE/include/$(basename "$entry")"
done
ln -sfn "$RF/verilator+/bin" "$VERILATOR_SHARE/bin"
python3 - "$RF/verilator+/include/verilated.mk.in" "$VERILATOR_SHARE/include/verilated.mk" <<'PY'
import platform
import re
import sys

src, dst = sys.argv[1:]
text = open(src).read()
macos = platform.system() == "Darwin"
substitutions = {
    "AR": "ar",
    "CXX": "g++",
    "OBJCACHE": "",
    "PERL": "perl",
    "PYTHON3": "python3",
    "CFG_WITH_CCWARN": "no",
    "CFG_WITH_DEV_GCOV": "no",
    "CFG_WITH_LONGTESTS": "no",
    "CFG_CXX_VERSION": "g++",
    "CFG_CXXFLAGS_PROFILE": "-pg",
    "CFG_CXXFLAGS_STD": "-std=gnu++17",
    "CFG_CXXFLAGS_STD_NEWEST": "-std=gnu++17",
    "CFG_CXXFLAGS_NO_UNUSED": "-faligned-new -Wno-sign-compare -Wno-unused-parameter -Wno-unused-variable",
    "CFG_CXXFLAGS_WEXTRA": "-Wextra",
    "CFG_CXXFLAGS_COROUTINES": "-fcoroutines",
    "CFG_CXXFLAGS_PCH_I": "-include",
    "CFG_GCH_IF_CLANG": "",
    "CFG_LDFLAGS_VERILATED": "",
    # macOS has no separate libatomic -- the atomics live in the C++ runtime,
    # and `-latomic` is a hard link failure ("ld: library 'atomic' not found")
    # that kills every verilator model before it runs.
    "CFG_LDLIBS_THREADS": (
        "-pthread -lpthread" if macos else "-pthread -lpthread -latomic"),
}
for key, value in substitutions.items():
    text = text.replace(f"@{key}@", value)
unresolved = sorted(set(re.findall(r"@[A-Z][A-Z0-9_]*@", text)))
if unresolved:
    raise SystemExit(f"unresolved verilated.mk substitutions: {', '.join(unresolved)}")
with open(dst, "w") as fh:
    fh.write(text)
PY

# Liberty: one directory per technology, mirroring tech/<name>/.
echo "staging Liberty into $OUT/lib"
for tech in sky130 asap7; do
  mkdir -p "$OUT/lib/$tech"
  while IFS= read -r lib; do
    ln -sfn "$lib" "$OUT/lib/$tech/$(basename "$lib")"
    printf '  %-10s %s\n' "$tech" "$(basename "$lib")"
  done < <(find -L "$RF" -type f -name '*.lib' 2>/dev/null | grep -i "$tech" || true)
done

# ---- versions -------------------------------------------------------------
# Recorded verbatim from each tool rather than from the bazel pin, so the key
# reflects the binary that will actually run. A pin and a binary disagreeing is
# exactly the situation this catches.
ver() {
  local bin=$1
  shift
  [ -x "$OUT/bin/$bin" ] || { echo "missing"; return 0; }
  "$OUT/bin/$bin" "$@" 2>&1 | head -1 | tr -d '\r' || echo "unknown"
}

python3 - "$OUT" <<'PY'
import hashlib, json, os, subprocess, sys, platform, datetime, re

out = sys.argv[1]
bindir, libdir = os.path.join(out, "bin"), os.path.join(out, "lib")

VERSION_ARGS = {
    "lhd":       ["version"],
    "yosys":     ["-V"],
    "abc":       ["-h"],
    "verilator": ["--version"],
    "sta":       ["-version"],
}

def first_line(binary, args):
    path = os.path.join(bindir, binary)
    if not os.path.exists(path):
        return None
    try:
        p = subprocess.run([path, *args], capture_output=True, text=True, timeout=60)
        for line in (p.stdout + p.stderr).splitlines():
            if line.strip():
                return line.strip()
    except Exception as e:                      # noqa: BLE001 -- report, never abort
        return f"unavailable: {e}"
    return "unknown"

def sha256(path, limit=None):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

TIME_UNIT = re.compile(r'time_unit\s*:\s*"?\s*(\d*)\s*([munpf]?s)\s*"?', re.I)

def time_unit_of(path):
    """Return the unit declared in a Liberty header (for example ns or ps)."""
    try:
        with open(path, errors="replace") as fh:
            for _ in range(4000):
                line = fh.readline()
                if not line:
                    break
                match = TIME_UNIT.search(line)
                if match:
                    multiplier = match.group(1) or "1"
                    unit = match.group(2).lower()
                    return unit if multiplier == "1" else f"{multiplier}{unit}"
    except OSError:
        pass
    return ""

tools, versions = {}, {}
for name, args in VERSION_ARGS.items():
    path = os.path.join(bindir, name)
    if os.path.exists(path):
        tools[name] = os.path.realpath(path)
        versions[name] = first_line(name, args)

# A plugin is not executable, so its content hash is its exact version.  It is
# recorded separately from Yosys because either half changing invalidates a
# cached baseline.
plugin = os.path.join(bindir, "yosys_slang")
if os.path.exists(plugin):
    tools["yosys_slang"] = os.path.realpath(plugin)
    versions["yosys_slang"] = f"yosys-slang sha256:{sha256(plugin)[:16]}"

# Liberty is hashed, not versioned: the file content IS the identity, and it is
# what a synthesis result actually depends on.
tech = {}
for name in sorted(os.listdir(libdir)) if os.path.isdir(libdir) else []:
    d = os.path.join(libdir, name)
    if not os.path.isdir(d):
        continue
    libs = sorted(os.path.join(d, f) for f in os.listdir(d) if f.endswith(".lib"))
    if not libs:
        continue
    tech[name] = {
        "liberty": [os.path.realpath(p) for p in libs],
        "sha256": hashlib.sha256("".join(sha256(p) for p in libs).encode()).hexdigest()[:16],
        "time_unit": next((u for u in (time_unit_of(p) for p in libs) if u), ""),
    }

doc = {
    "schema_version": 1,
    "generated": datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"),
    # host_class is part of every cache key. A number from another box is not
    # evidence, and this is what lets the runner prove it is not reusing one.
    "host_class": f"{platform.system()}-{platform.machine()}",
    "bin": tools,
    "env": {
        "lhd": {"RUNFILES_DIR": os.path.join(out, "runfiles")},
        "verilator": {"VERILATOR_ROOT": os.path.join(out, "share", "verilator")},
    },
    "versions": versions,
    "tech": tech,
}
with open(os.path.join(out, "toolchain.json"), "w") as fh:
    json.dump(doc, fh, indent=2, sort_keys=True)
    fh.write("\n")

print(f"\nwrote {out}/toolchain.json")
for k, v in sorted(versions.items()):
    print(f"  {k:<10} {v}")
missing = [k for k in VERSION_ARGS if k not in tools]
if missing:
    print(f"\nMISSING: {', '.join(missing)} -- flows needing them will be skipped and reported")
if not tech:
    print("MISSING: no Liberty staged -- every synthesis flow will be skipped")
PY
