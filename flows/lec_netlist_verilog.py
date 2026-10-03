"""Check the retained ABC Verilog emission against its original Verilog source."""
import hashlib
import json
import shlex
from pathlib import Path

from lhdtrack.context import FlowError, FlowSkip
from lec_netlist import _check
from lib.lec import stage_readmem_data
from lhdtrack.metrics import measure
from lhdtrack.report.verilog_eval import bounded_yosys_evidence, synth_flow

NAME = "lec_netlist_verilog"
KIND = "lec"
NEEDS = ("lhd",)
USES_TECH = True
CHECK_BOUND = 6


def run(ctx):
    return run_mapper(ctx, "abc")


def run_mapper(ctx, mapper, *, satopt=True):
    ctx.require_sdc()
    flow = synth_flow(mapper, satopt)
    artifacts = ctx.work.parent / flow / "synth-artifacts.json"
    if not artifacts.exists():
        raise FlowSkip(f"{mapper} synthesis produced no retained netlist in this run")
    doc = json.loads(artifacts.read_text())
    netlist = Path(doc["netlist"])
    digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
    if doc["mapper"] != mapper or digest != doc["sha256"]:
        raise FlowError("retained netlist differs from the measured synthesis artifact")
    emission = None
    if (ctx.work.parent / "reemit-logical-hierarchy.json").exists():
        from lhdtrack.report.verilog_eval import verify_transparent_instance_renaming
        from qor_endpoint import emitted_verilog
        mapped_graphs = artifacts.parent / "netlist"
        ctx.run("reemit", [ctx.tool("lhd"), "compile", f"lg:{mapped_graphs}",
                           "--top", ctx.top, "--emit-dir", "verilog:netv", "--workdir", "Wemit"])
        fresh = emitted_verilog(ctx, ctx.work / "netv")
        try:
            renamed = verify_transparent_instance_renaming(netlist.read_text(), fresh.read_text())
        except ValueError as error:
            raise FlowError(str(error)) from error
        emission = dict(kind="transparent_instance_renaming", source_netlist_sha256=digest,
                        source_netlist=str(netlist), netlist=str(fresh), renamed_instances=renamed)
        netlist = fresh
        digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
        emission["netlist_sha256"] = digest
        ctx.write("emission-provenance.json", json.dumps(emission, indent=2) + "\n")
    ctx.run("models", [ctx.tool("lhd"), "pass", "liberty", "gensim",
                       str(ctx.liberty[0]), "--emit-dir", "lg:models",
                       "--emit", "verilog:models.v", "--workdir", "Wm"],
            timeout=ctx.lec_timeout_s * 2 + 60)
    # Present the ORIGINAL source to both frontends, not a cgen round trip of
    # the compiler's reference graph. Includes retain source-relative headers.
    reference = ctx.write("original-rtl.sv", "`define SYNTHESIS\n`define BR_PPA_SYNTHESIS\n"
                          + "\n".join(f'`include "{p}"' for p in ctx.verilog_sources()) + "\n")
    # Liberty models are shared setup, outside BOTH measured checker invocations.
    # Passing --lib here would make native LEC export the library again, while
    # the independent oracle consumes that export for free.
    models = ctx.work / "models.v"
    native_impl = ctx.write("native-impl.sv", f'`include "{netlist}"\n`include "{models}"\n')
    native_ref = ctx.write("native-ref.sv", f'`include "{reference}"\n`include "{models}"\n')
    native = _check(
        ctx, impl=f"verilog:{native_impl}", ref=f"verilog:{native_ref}",
        models=None, solver="cvc5", obligation="verilog-vs-netlist",
        label="lec_cvc5_verilog_netlist", impl_top=ctx.top, ref_top=ctx.top, bound=CHECK_BOUND,
        satopt=satopt,
    )
    oracle = independent_check(ctx, netlist, reference, models=models)
    for block in (native, oracle):
        if emission:
            block["emission"] = emission
        block.update(mapper=mapper, netlist_sha256=digest,
                     models_sha256=hashlib.sha256(models.read_bytes()).hexdigest(),
                     timing_scope="prepared-verilog-inputs",
                     liberty_sha256=ctx.tech.sha256)
    return {"lec_verilog": native, "lec_aux": oracle}


def independent_check(ctx, netlist, reference, *, models=None):
    """Check original RTL against the exact measured emission in Yosys."""
    if models is None:
        models = ctx.work / "LW-lec_cvc5_verilog_netlist/check_ref_models0.v"
    model_text = models.read_bytes()
    if b"endmodule" not in model_text:
        raise FlowError("missing Liberty cell models for independent netlist LEC")
    dest = ctx.work / "oracle"
    dest.mkdir(exist_ok=True)
    images = stage_readmem_data(ctx, dest)
    (dest / "impl.v").write_bytes(netlist.read_bytes() + b"\n" + model_text)
    (dest / "ref.v").write_bytes(reference.read_bytes() + b"\n" + model_text)
    env = {**ctx.tc.env_for(str(ctx.tool("lhd"))),
           "LGCHECK_BMC_STEPS": str(CHECK_BOUND), "LGCHECK_EQUIV_TIMEOUT": str(ctx.lec_timeout_s)}
    check = (ctx.tool("lgcheck") if ctx.tc.has("lgcheck") else
             Path(env["RUNFILES_DIR"]) / "_main/inou/yosys/lgcheck")
    yosys = ctx.tool("yosys2") if ctx.tc.has("yosys2") else check.parent / "yosys2"
    argv = [str(check), "--yosys", str(yosys),
            "--implementation", str(dest / "impl.v"),
            "--reference", str(dest / "ref.v"), "--top", ctx.top,
            "--gold_reader", "slang", "--gate_reader", "slang"]
    label = "lec_lgyosys_verilog_netlist"
    ctx.cmds.append(f"{label}: {shlex.join(argv)}")
    wall = ctx.lec_timeout_s * 2 + 60
    measured = ctx.stage.add(measure(label, argv, dest, ctx.logs, env=env, timeout=wall))
    verdict = ("timeout" if measured.timed_out else
               {0: "proven", 1: "refuted", 2: "inconclusive"}.get(measured.rc, "error"))
    block = dict(verdict=verdict, bounded=False, bound=None, solver="lgyosys",
                 obligation="verilog-vs-netlist", declared="none", ms=measured.ms,
                 timeout_s=ctx.lec_timeout_s, wall_limit_s=wall,
                 invocation="direct-lgcheck", log=str(measured.log),
                 lgcheck_sha256=hashlib.sha256(check.read_bytes()).hexdigest(),
                 models_sha256=hashlib.sha256(model_text).hexdigest())
    if images:
        block["input_images"] = images
    def read(path):
        return path.read_text() if path.exists() else ""
    block = bounded_yosys_evidence(block, read(measured.log),
                                  read(dest / "lgcheck_bmc.log"), read(dest / "lgcheck_bmc.err"))
    if block["verdict"] in ("refuted", "inconclusive", "error"):
        block["reason"] = measured.tail()[-2000:]
    (dest / "result.json").write_text(json.dumps({"block": block, "argv": argv}, indent=2) + "\n")
    return block
