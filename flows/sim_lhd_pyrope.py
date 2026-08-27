"""`lhd sim`, PYROPE in -- the simulation row the tracker exists to measure.

Same three legs, same testbench contract, same checksum gate as the other two.
As with syn_lhd_pyrope: on a test whose pyrope/ is still an `auto` seed this
measures front ends rather than languages, and the report keeps it out of the
headline.
"""

from __future__ import annotations

import shutil

from lhdtrack.context import FlowContext, FlowSkip

NAME = "sim_lhd_pyrope"
KIND = "sim"
NEEDS = ("lhd",)
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    from lib.simgate import run_lhd_sim

    tb = ctx.test.sim_dir / f"{ctx.top}_tb.prp"
    if not tb.exists():
        raise FlowSkip(f"no Pyrope testbench: {tb.name} (run `lhdtrack import seed`)")
    if not ctx.test.pyrope_top.exists():
        raise FlowSkip(f"no pyrope source: {ctx.test.pyrope_top.name}")

    # Stage the design tree next to the driver. `lhd sim` resolves sibling
    # imports by directory, so the harness (if any) must land beside the DUT
    # rather than arrive as a second positional input.
    tree = ctx.work / "tree"
    tree.mkdir(parents=True, exist_ok=True)
    for src in ctx.test.pyrope_dir.glob("*.prp"):
        shutil.copy(src, tree / src.name)
    harness = ctx.test.sim_dir / f"{ctx.top}_harness.prp"
    if harness.exists():
        shutil.copy(harness, tree / harness.name)

    entry = tree / (harness.name if harness.exists() else ctx.test.pyrope_top.name)
    out = run_lhd_sim(ctx, design_input=str(entry), tb=tb)
    out["pyrope_status"] = ctx.test.pyrope_status
    return out
