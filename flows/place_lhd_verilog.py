"""ASAP7 comparison: place_lhd_verilog."""
from lib.placement import run_native

NAME = "place_lhd_verilog"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    return run_native(ctx, place=True)
