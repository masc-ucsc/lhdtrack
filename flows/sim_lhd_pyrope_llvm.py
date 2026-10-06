"""Pyrope simulation through LLVM, paired with sim_lhd_pyrope's Slop backend."""
from lhdtrack.context import FlowContext
from sim_lhd_pyrope import run as run_source

NAME = "sim_lhd_pyrope_llvm"
KIND = "sim"
NEEDS = ("lhd",)
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    return run_source(ctx, backend="llvm")
