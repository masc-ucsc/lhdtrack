"""ASAP7 comparison: place_openroad_prects."""
from lib.placement import run_openroad

NAME = "place_openroad_prects"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    return run_openroad(ctx, place=True)
