"""ASAP7 comparison: syn_lhd_verilog_prects_baseline."""
from lib.placement import run_native

NAME = "syn_lhd_verilog_prects_baseline"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    return run_native(ctx)
