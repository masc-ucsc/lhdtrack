# OpenSTA, built with its own CMake through rules_foreign_cc.
#
# OpenSTA's CUDD dependency is built hermetically; CMake, Tcl, flex, bison and
# SWIG are build-time/system interfaces supplied by the Bazel execution image.
#
# It earns its keep: OpenSTA is the independent check on LiveHD's built-in
# OpenTimer. Timing both engines on the SAME netlist, .lib and .sdc is what
# turns "LiveHD says 1.23 ns" into a claim anyone can check.
load("@rules_foreign_cc//foreign_cc:defs.bzl", "cmake")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "all_srcs",
    srcs = glob(["**"]),
)

cmake(
    name = "opensta_build",
    build_args = ["-j"],
    cache_entries = {
        "CMAKE_BUILD_TYPE": "Release",
        # No GUI, no Verilog writer -- the runner only needs the timing engine.
        "USE_TCL_READLINE": "OFF",
        # OpenSTA defaults BUILD_TESTS ON, and that arm is a hard
        # `find_package(GTest REQUIRED)`. Nothing here runs OpenSTA's own unit
        # tests, and a host GTest is exactly the kind of unpinned dependency
        # this toolchain avoids, so configure the tests out.
        "BUILD_TESTS": "OFF",
    } | select({
        # OpenSTA compiles flex's C++ lexer, so it needs FlexLexer.h next to the
        # flex CMake picks up. macOS ships that header in the active developer
        # toolchain rather than in any directory CMake's find_path searches, so
        # FLEX_INCLUDE_DIR came back NOTFOUND and the generate step aborted.
        # CMAKE_INCLUDE_PATH is searched before the system paths and silently
        # skips entries that do not exist, so listing the candidates covers
        # Command Line Tools, a full Xcode, and Homebrew's keg-only flex without
        # asserting which one this machine has. Linux ships it in /usr/include.
        "@platforms//os:macos": {
            "CMAKE_INCLUDE_PATH": ";".join([
                "/Library/Developer/CommandLineTools/usr/include",
                "/Applications/Xcode.app/Contents/Developer/Toolchains/XcodeDefault.xctoolchain/usr/include",
                "/opt/homebrew/opt/flex/include",
                "/usr/local/opt/flex/include",
            ]),
            # The grammars use `%code`, so they need bison 3.x. macOS still
            # ships GNU bison 2.3 in /usr/bin, and OpenSTA's version guard is
            # inert (`find_package(BISON REQUIRED 3.2)` puts the version after
            # REQUIRED, where CMake ignores it), so the stale bison is found and
            # only fails once it reaches the first directive. Search Homebrew's
            # keg-only bison first; same skip-if-absent rule as above.
            "CMAKE_PROGRAM_PATH": ";".join([
                "/opt/homebrew/opt/bison/bin",
                "/usr/local/opt/bison/bin",
            ]),
        },
        "//conditions:default": {},
    }),
    deps = ["@cudd//:cudd"],
    lib_source = ":all_srcs",
    out_binaries = ["sta"],
)

filegroup(
    name = "sta",
    srcs = [":opensta_build"],
    output_group = "sta",
)
