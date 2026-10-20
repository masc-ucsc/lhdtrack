"""ASAP7 comparison: syn_hightide."""
from lib.placement import run_openroad

NAME = "syn_hightide"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    return run_openroad(ctx)
