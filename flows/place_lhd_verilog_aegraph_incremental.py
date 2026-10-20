"""Repeat aegraph placement using the preceding cold run's unchanged workdir."""
import json

from lhdtrack.context import FlowSkip
from lib.placement import run_native

NAME = "place_lhd_verilog_aegraph_incremental"
KIND = "synth"
NEEDS = ("lhd", "yosys", "openroad", "perf", "time", "python", "make")
USES_TECH = True


def run(ctx):
    parent = ctx.work.parent / "place_lhd_verilog_aegraph"
    row = parent / "row.json"
    if not row.is_file():
        raise FlowSkip("incremental placement requires the preceding cold run")
    cold = json.loads(row.read_text())
    comparison = cold.get("qor", {}).get("placement_comparison", {})
    if cold["status"] != "ok" or comparison.get("fp_plan") is not False:
        raise FlowSkip("incremental placement requires successful aegraph placement with fp=false")
    result = run_native(ctx, place=True, aegraph=True, fp_plan=False, workdir=parent / "W")
    result["qor"]["placement_comparison"]["incremental_parent"] = str(parent)
    return result
