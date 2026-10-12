"""ABC's slang/SN frontend with LiveHD's shared ABC mapping and sizing policy."""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

from lhdtrack.context import FlowContext, FlowError, FlowSkip

NAME = "syn_abc_sn"
KIND = "synth"
NEEDS = ("sn", "abc_sn_mapper", "yosys")
OPTIONAL = ("sta",)
USES_TECH = True


def diagnostic(log: Path) -> str:
    # The measurement harness echoes argv; it is never a tool diagnostic.
    lines = [line for line in log.read_text(errors="replace").splitlines()
             if not line.startswith("$ ")]
    errors = [line for line in lines if re.search(r"\b(error|cannot|refused)\b", line, re.I)]
    return "\n".join(errors[-4:] or lines[-12:])[-1800:]


def run(ctx: FlowContext) -> dict:
    ctx.require_sdc()
    if ctx.tech.name != "asap7":
        raise FlowSkip("ABC/SN comparison is currently enabled for ASAP7 only")
    if len(ctx.liberty) != 1:
        raise FlowSkip("ABC/SN requires a single merged Liberty")
    if ctx.chparams():
        raise FlowSkip("SN requires a monomorphic top; parameter overrides are unsupported")
    liberty = str(ctx.liberty[0])
    if re.search(r'[\s;"\\]', liberty):
        raise FlowSkip("ABC/SN Liberty path contains unsupported ABC command characters")
    sources = ctx.verilog_sources()
    if not sources or any(not path.is_file() for path in sources):
        raise FlowSkip("SN has missing Verilog sources")
    imported = ctx.run("frontend", [
        ctx.tool("sn"), "-M", ctx.top, "-s", "-D", "SYNTHESIS",
        "-D", "BR_PPA_SYNTHESIS", "-I", ctx.test.verilog_dir,
        "-P", "ports.json", "-o", "design.sn", *sources,
    ], check=False, timeout=ctx.lec_timeout_s * 2 + 60)
    if not imported.ok:
        if imported.timed_out:
            raise FlowError("SN frontend timed out\n" + diagnostic(imported.log))
        if imported.rc < 0:
            raise FlowError(f"SN frontend terminated by signal {-imported.rc}\n"
                            + diagnostic(imported.log))
        raise FlowSkip("SN frontend cannot elaborate this design:\n" + diagnostic(imported.log))
    mapped = ctx.run("map", [ctx.tool("abc_sn_mapper"), "design.sn", liberty,
                             ctx.abc_delay_ps(), "sn-mapped.v", "mapping.json"],
                     check=False, timeout=ctx.lec_timeout_s * 2 + 60)
    result_path = ctx.work / "mapping.json"
    if mapped.timed_out:
        raise FlowError("ABC/SN mapping timed out\n" + diagnostic(mapped.log))
    if not result_path.is_file():
        raise FlowError("ABC/SN mapper produced no result JSON\n" + diagnostic(mapped.log))
    result = json.loads(result_path.read_text())
    if result.get("status") == "unsupported":
        raise FlowSkip(result.get("reason", "ABC/SN mapping unsupported") +
                       "\n" + diagnostic(mapped.log))
    if not mapped.ok or result.get("status") != "ok":
        raise FlowError(result.get("reason", "ABC/SN mapping failed") +
                        "\n" + diagnostic(mapped.log))
    raw = ctx.work / "sn-mapped.v"
    if not raw.is_file():
        raise FlowError("ABC/SN reported success without a mapped netlist")
    # SN emits valid `input wire` declarations that OpenSTA's restricted
    # netlist parser rejects. Re-emit structurally through Yosys, without any
    # optimization or mapping passes. Retain the original SN emission as well.
    emission = ctx.write("emit.ys", "read_verilog sn-mapped.v\n"
                         "write_verilog -noattr -noexpr mapped.v\n")
    ctx.run("emit", [ctx.tool("yosys"), "-q", "-s", emission])
    netlist = ctx.work / "mapped.v"
    from qor_endpoint import evaluate

    measurement = evaluate(ctx, netlist, lgraph=None)
    measurement["qor"].update(
        mapper="abc_sn", synth_policy=result,
        netlist_sha256=hashlib.sha256(netlist.read_bytes()).hexdigest(),
        liberty_sha256=ctx.tech.sha256,
        sdc_sha256=hashlib.sha256(ctx.sdc.read_bytes()).hexdigest(),
        source_sha256={str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in sources},
        synthesis_time_ms=sum(ctx.stage.time_ms.get(stage, 0) for stage in ("frontend", "map")),
        comparison_scope="shared LiveHD ABC executor and default scripts; "
                         "SN frontend, partitions, state mapping and ABC revision differ",
    )
    frontend_timing = ctx.work / "design.sn.timing.json"
    if frontend_timing.is_file():
        measurement["qor"]["timing_breakdown"] = {
            "schema_version": 1,
            "frontend_ms": json.loads(frontend_timing.read_text())["timing_ms"],
            "mapper_ms": result.get("timing_ms", {}),
            "abc_commands": result.get("command_timing", []),
        }
    return {"netlist": netlist, **measurement}
