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
    },
    deps = ["@cudd//:cudd"],
    lib_source = ":all_srcs",
    out_binaries = ["sta"],
)

filegroup(
    name = "sta",
    srcs = [":opensta_build"],
    output_group = "sta",
)
