"""ASAP7 aegraph synthesis and placement, with floorplan planning disabled."""
from lib.placement import run_native

NAME = "place_lhd_verilog_aegraph"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    return run_native(ctx, place=True, aegraph=True, fp_plan=False)
