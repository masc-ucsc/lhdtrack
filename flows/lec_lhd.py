"""LEC with LiveHD's own solver -- the gate that makes every QoR number mean something.

`lhd lec --impl pyrope --ref verilog` with the default in-process SMT backend
(cvc5, reasoning over the LGraph). Until this proves the two sides equivalent, a
Pyrope area win might simply be a smaller, different circuit, and
`corpus.Test.comparable()` keeps such a test out of every headline aggregate.

It is also a MEASUREMENT, not only a gate: how long LiveHD takes to prove
equivalence is a LiveHD metric, and `lec_lgyosys` runs the identical obligation
through yosys so the two are directly comparable.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext

NAME = "lec_lhd"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = False


def run(ctx: FlowContext) -> dict:
    from lib.lec import run_lec

    return run_lec(ctx, solver="cvc5", timeout_s=ctx.lec_timeout_s)
