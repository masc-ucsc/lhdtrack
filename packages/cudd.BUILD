load("@rules_foreign_cc//foreign_cc:defs.bzl", "configure_make")

package(default_visibility = ["//visibility:public"])

filegroup(
    name = "all_srcs",
    srcs = glob(["**"]),
)

# OpenSTA needs only the C API. CUDD's release tarball already contains the
# generated configure script, and `make install` supplies both libcudd.a and
# cudd.h to rules_foreign_cc's dependency prefix.
#
# On macOS Bazel's C++ toolchain archives with Apple's `libtool`, and hands
# rules_foreign_cc `AR=/usr/bin/libtool`. Automake's AM_PROG_AR probes an
# archiver by trying `$AR cru` then `$AR -NOLOGO`; libtool speaks neither, so
# configure aborts with "could not determine /usr/bin/libtool interface".
# Passing AR/RANLIB as configure arguments overrides that inherited environment
# (and lands in the generated Makefile), so the archive step uses a real ar.
configure_make(
    name = "cudd",
    configure_options = [
        "--disable-shared",
        "--disable-dddmp",
        "--disable-obj",
    ] + select({
        "@platforms//os:macos": [
            "AR=/usr/bin/ar",
            "RANLIB=/usr/bin/ranlib",
        ],
        "//conditions:default": [],
    }),
    lib_source = ":all_srcs",
    out_static_libs = ["libcudd.a"],
)
