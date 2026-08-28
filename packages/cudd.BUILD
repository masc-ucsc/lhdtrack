load("@rules_foreign_cc//foreign_cc:defs.bzl", "configure_make")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "all_srcs",
    srcs = glob(["**"]),
)

# OpenSTA needs only the C API. CUDD's release tarball already contains the
# generated configure script, and `make install` supplies both libcudd.a and
# cudd.h to rules_foreign_cc's dependency prefix.
configure_make(
    name = "cudd",
    configure_options = [
        "--disable-shared",
        "--disable-dddmp",
        "--disable-obj",
    ],
    lib_source = ":all_srcs",
    out_static_libs = ["libcudd.a"],
)
