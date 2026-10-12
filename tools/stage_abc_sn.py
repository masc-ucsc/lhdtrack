#!/usr/bin/env python3
"""Build and stage ABC/SN plus LiveHD's shared mapping executor, preserving other tools.

Builds stay under var/build. Sibling ABC, slang and LiveHD checkouts are read-only.
The mapper links LiveHD's abc_flow.cpp unchanged and extracts its default command
strings, limits and standalone Liberty parsing code. Source hashes are archived
with immutable binaries. Existing bin.abc (the Yosys baseline) is not replaced.
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def command(argv: list, cwd: Path | None = None) -> None:
    subprocess.run([str(arg) for arg in argv], cwd=cwd, check=True)


def revision(path: Path) -> str:
    return subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=path, text=True).strip()


def recipes(livehd: Path) -> str:
    source = (livehd / "pass/abc/abc_backend.cpp").read_text()
    declarations = []
    for name in ("kCombFlow", "kBufferTail", "kAreaFlow", "kAreaTail", "kMapCmd", "kPutCmd"):
        # Semicolons in quoted ABC commands are not C++ statement terminators.
        match = re.search(rf"constexpr std::string_view {name}\s*=\s*((?:\"[^\"]*\"\s*)+);", source)
        if not match:
            raise ValueError(f"cannot extract LiveHD {name}; update the adapter for this source")
        declarations.append(f"constexpr const char* {name} = {match.group(1)};")
    defaults = (livehd / "pass/abc/pass_abc.cpp").read_text()
    for name, label in (("kMaxFanout", "max_fanout"), ("kAreaRelax", "area_relax"),
                        ("kMemoryMaxBits", "memory_max_bits")):
        match = re.search(rf'm\.add_label_optional\("{label}",.*?"(\d+)"\);', defaults, re.S)
        if not match:
            raise ValueError(f"cannot extract LiveHD default {label}")
        declarations.append(f"constexpr unsigned {name} = {match.group(1)};")
    return "\n".join(declarations) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    for name in ("abc", "slang", "livehd"):
        parser.add_argument(f"--{name}", type=Path)
    parser.add_argument("-j", "--jobs", type=int, default=8)
    parser.add_argument("--skip-build", action="store_true", help="reuse already-built ABC and sn")
    args = parser.parse_args()
    root = args.root.resolve()
    abc, slang, livehd = [getattr(args, n) or root.parent / n for n in ("abc", "slang", "livehd")]
    abc, slang, livehd = abc.resolve(), slang.resolve(), livehd.resolve()
    build = root / "var/build"
    abc_build, sn_build, adapter = build / "abc-sn", build / "sn", build / "abc-sn-adapter"
    adapter.mkdir(parents=True, exist_ok=True)
    if not args.skip_build:
        command(["cmake", "-S", abc, "-B", abc_build, "-DCMAKE_BUILD_TYPE=Release",
                 "-DREADLINE_FOUND=FALSE", "-DABC_SKIP_TESTS=ON", "-DABC_USE_SLANG=OFF"])
        command(["cmake", "--build", abc_build, "--target", "abc", "--parallel", args.jobs])
        configure = ["cmake", "-S", abc / "tools/sn", "-B", sn_build,
                     "-DCMAKE_BUILD_TYPE=Release", f"-DSLANG_SOURCE_DIR={slang}"]
        for name in ("fmt", "boost_regex", "tomlplusplus"):
            cached = slang / "build/_deps" / f"{name}-src"
            if cached.is_dir():
                configure.append(f"-DFETCHCONTENT_SOURCE_DIR_{name.upper()}={cached}")
        command(configure)
        command(["cmake", "--build", sn_build, "--target", "sn", "--parallel", args.jobs])

    # Only the parser's independent part is needed, preceding graph emission.
    # Preserve original definitions verbatim; remove graph-only dependencies.
    liberty = livehd / "pass/liberty"
    header = (liberty / "liberty_dff.hpp").read_text()
    header = header.split("std::shared_ptr<hhds::GraphIO> create_dff_io(")[0]
    header = header.replace('#include "hhds/graph.hpp"', "")
    header += "\n}  // namespace livehd::liberty\n"
    source = (liberty / "liberty_dff.cpp").read_text()
    source = source.split("std::string dff_descriptor(")[0]
    for include in ('#include "cell.hpp"', '#include "dlop.hpp"', '#include "node_util.hpp"',
                    "namespace gu = livehd::graph_util;"):
        source = source.replace(include, "")
    source += "\n}  // namespace livehd::liberty\n"
    (adapter / "liberty_dff.hpp").write_text(header)
    (adapter / "liberty_dff.cpp").write_text(source)
    (adapter / "recipe.hpp").write_text(recipes(livehd))
    for name in ("abc_flow.cpp", "abc_flow.hpp"):
        shutil.copy2(livehd / "pass/abc" / name, adapter / name)
    # Use the recorded C++ compiler; compilation and execution have separate
    # provenance. No benchmark executable is resolved from PATH.
    tc_path = root / "var/toolchain/toolchain.json"
    tc = json.loads(tc_path.read_text())
    # Expose the importer's existing timers as result JSON rather than scraping
    # -t stdout. Only the standalone main is copied; importer/libraries stay intact.
    frontend = (abc / "tools/sn/src/snMain.cpp").read_text()
    frontend = frontend.replace('#include "snSlang.h"',
                                '#include "snSlang.h"\n#include "frontend_timing.hpp"')
    ending = "    return 0;\n}"
    if frontend.count(ending) != 1:
        raise ValueError("cannot instrument snMain success return; update timing adapter")
    frontend = frontend.replace(ending,
        "    lhdtrack_frontend_timing(output, frontend_timing);\n" + ending)
    (adapter / "snMain.cpp").write_text(frontend)
    shutil.copy2(root / "tools/abc_sn/frontend_timing.hpp", adapter / "frontend_timing.hpp")
    command([tc["bin"]["cxx"], "-std=c++20", "-O3", "-DNDEBUG",
             "-I", abc / "tools/sn/src", "-I", abc / "src/base/sn", "-I", abc / "src",
             "-I", adapter, adapter / "snMain.cpp", sn_build / "libsn_slang_lib.a",
             sn_build / "slang/lib/libsvlang.a", "-lpthread", "-ldl", "-o", adapter / "sn"])
    command([tc["bin"]["cxx"], "-std=c++20", "-O3", "-DNDEBUG", "-DLIN64",
             "-DSIZEOF_VOID_P=8", "-DSIZEOF_LONG=8", "-DSIZEOF_INT=4", "-DABC_USE_PTHREADS",
             "-I", abc / "src", "-I", adapter, root / "tools/abc_sn/driver.cpp",
             adapter / "abc_flow.cpp", adapter / "liberty_dff.cpp", abc_build / "libabc.a",
             "-lpthread", "-ldl", "-lm", "-Wl,--wrap=Cmd_CommandExecute",
             "-o", adapter / "abc_sn_mapper"])
    sources = [abc / "src/base/abci/abc.c", abc / "src/aig/gia/giaIf.c",
               abc / "src/base/sn/snCom.c",
               abc / "src/base/sn/snBlast.h",
               abc / "tools/sn/src/snSlang.cpp",
               slang / "CMakeLists.txt", livehd / "pass/abc/abc_backend.cpp",
               livehd / "pass/abc/pass_abc.cpp", livehd / "pass/abc/abc_boundary.cpp",
               livehd / "pass/liberty/liberty_dff.cpp", livehd / "pass/liberty/liberty_dff.hpp",
               livehd / "pass/abc/abc_flow.cpp", livehd / "pass/abc/abc_flow.hpp",
               root / "tools/abc_sn/driver.cpp", root / "tools/abc_sn/frontend_timing.hpp",
               abc / "tools/sn/src/snMain.cpp", root / "tools/stage_abc_sn.py"]
    provenance = {"revisions": {n: revision(p) for n, p in
                               (("abc", abc), ("slang", slang), ("livehd", livehd))},
                  "source_sha256": {str(p): digest(p) for p in sources},
                  "recipe": (adapter / "recipe.hpp").read_text(),
                  "comparison_scope": "shared ABC commands and executor; SN partitions, "
                                      "state legalization and ABC revision differ from LiveHD"}
    binaries = {"abc_sn": abc_build / "abc", "sn": adapter / "sn",
                "abc_sn_mapper": adapter / "abc_sn_mapper"}
    serialized = json.dumps({**provenance, "binaries": {n: digest(p) for n, p in binaries.items()}},
                            sort_keys=True)
    signature = hashlib.sha256(serialized.encode()).hexdigest()
    dest = root / "var/toolchain/abc-sn" / signature[:16]
    dest.mkdir(parents=True, exist_ok=True)
    for name, path in binaries.items():
        shutil.copy2(path, dest / name)
        tc["bin"][name] = str(dest / name)
        tc.setdefault("versions", {})[name] = (
            f"abc-sn shared-backend:{signature[:16]} sha256:{digest(path)[:16]}"
        )
    for path in adapter.glob("*.?pp"):
        shutil.copy2(path, dest / path.name)
    shutil.copy2(root / "tools/abc_sn/driver.cpp", dest / "driver.cpp")
    for name, path, license_name in (("abc", abc, "copyright.txt"),
                                    ("slang", slang, "LICENSE"), ("livehd", livehd, "LICENSE")):
        shutil.copy2(path / license_name, dest / f"LICENSE-{name}")
    (dest / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")
    tc["abc_sn_provenance"] = str(dest / "provenance.json")
    tc["generated"] = datetime.datetime.now(datetime.timezone.utc).isoformat()
    tc_path.write_text(json.dumps(tc, indent=2) + "\n")
    print(f"staged ABC/SN and shared LiveHD mapper in {dest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
