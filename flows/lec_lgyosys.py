"""BASELINE LEC: the same obligation through yosys (`lgcheck`).

`--set formal.solver=lgyosys` is LiveHD's yosys-backed equivalence path --
inou/yosys/lgcheck, the former `lhd check`. It proves exactly what `lec_lhd`
proves, so the two are comparable on both axes the tracker cares about:

    time     is LiveHD's own solver faster than driving yosys?
    verdict  do they agree?

THE VERDICTS DISAGREEING IS THE INTERESTING CASE. cvc5 reasons over the LGraph
while lgyosys reasons over the cgen-emitted Verilog, so a code-generation bug
shows up as one backend proving what the other refutes -- a discrepancy no
single-engine run can surface. run.py records that as a `lec_backend_split`
rather than picking a winner.

CACHEABLE: keyed on the sources, this file, and the lhd version like any other
baseline -- though in practice the lhd SHA moves daily and it re-measures.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext

NAME = "lec_lgyosys"
KIND = "lec"
NEEDS = ("lhd", "yosys")
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    from lib.lec import run_lec

    return run_lec(ctx, solver="lgyosys", timeout_s=ctx.lec_timeout_s)
