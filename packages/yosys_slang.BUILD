load("@rules_cc//cc:defs.bzl", "cc_binary", "cc_library")

cc_binary(
    name = "slang.so",
    srcs = glob(["src/*.cc"], exclude = ["*test*.cpp"]),
    copts = [
        "-fvisibility=hidden",
        "-fvisibility-inlines-hidden",
    ],
    features = ["-hardening"],
    linkopts = select({
        "@platforms//os:macos": ["-Wl,-undefined,dynamic_lookup"],
        "//conditions:default": [
            "-Wl,-z,lazy",
            "-Wl,-z,norelro",
        ],
    }),
    linkshared = True,
    visibility = ["//visibility:public"],
    deps = [":yosys_slang"],
)

cc_library(
    name = "yosys_slang",
    hdrs = glob(["src/*.h"]),
    defines = [
        "SLANG_STATIC_DEFINE",
        "YOSYS_ENABLE_PLUGINS",
        "yosys_slang_EXPORTS",
    ],
    includes = ["src"],
    visibility = ["//visibility:public"],
    deps = [
        "@slang_v10//:slang",
        "@yosys//:hdrs",
    ],
)
