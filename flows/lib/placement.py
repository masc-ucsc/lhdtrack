"""Matched ASAP7 RTL-to-netlist and pre-CTS measurements, with separate scoring."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import re
import shlex
import shutil

from lhdtrack.context import FlowContext, FlowError, FlowSkip
from lhdtrack.resources import measure
from lib.lhd_synth_policy import mapping_settings


def sha(path: Path) -> str:
    with path.open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def tcl(value: str | Path) -> str:
    return '"' + re.sub(r'([\\"\[\]$])', r'\\\1', str(value)) + '"'


def settings(ctx: FlowContext) -> dict:
    if ctx.tech.name != "asap7" or len(ctx.liberty) != 1:
        raise FlowSkip("pre-CTS comparison requires one merged ASAP7 Liberty")
    required = {"flow_home", "platform", "lefs", "tech_rc", "slang", "threads",
                "synth_script", "score_script"}
    if required - ctx.tc.physical.keys():
        raise FlowSkip("physical toolchain incomplete; run tools/stage_placement.py")
    ctx.require_sdc()
    return ctx.tc.physical


def measured(ctx: FlowContext, argv: list, env: dict | None = None) -> dict:
    argv = list(map(str, argv))
    ctx.cmds.append("flow: " + shlex.join(argv))
    result = measure(argv, ctx.work / "measurement", cwd=ctx.work,
                     env={**ctx.tc.env_for(argv[0]), **(env or {})},
                     time_executable=ctx.tool("time"), perf_executable=ctx.tool("perf"))
    ctx.stage.time_ms["flow"] = round(result["command_wall_s"] * 1000)
    ctx.stage.peak_rss_kb["flow"] = result["max_process_rss_kb"]
    if result["status"] != "ok":
        raise FlowError(f"measured flow {result['status']}; see {ctx.work / 'measurement/run.log'}")
    return result


def structural(ctx: FlowContext, raw: Path) -> Path:
    """Elaborate wiring wrappers only, retaining every mapped cell and load."""
    out, net_json = ctx.work / "score-netlist.v", ctx.work / "score-netlist.json"
    script = ctx.write("score-prepare.ys", f"""
read_liberty -lib {tcl(ctx.liberty[0])}
read_verilog -sv -noblackbox {tcl(raw)}
hierarchy -check -top {ctx.top}
proc
flatten
splitnets -ports
write_json {tcl(net_json)}
write_verilog -noattr -noexpr {tcl(out)}
""")
    ctx.run("scoring-prepare", [ctx.tool("yosys"), "-s", script], timeout=120)
    modules = json.loads(net_json.read_text())["modules"]
    from lib.logic_depth import liberty_cells

    known, _ = liberty_cells(ctx.liberty)
    unknown = {cell["type"] for cell in modules[ctx.top].get("cells", {}).values()}
    unknown -= known | {"$scopeinfo"}
    if unknown:
        raise FlowSkip("mapped timing unavailable: native logic/state preserved: "
                       + ", ".join(sorted(unknown)))
    return out


def score(ctx: FlowContext, netlist: Path, physical_input: Path | None, place: bool) -> dict:
    cfg = ctx.tc.physical
    out = ctx.work / "score"
    out.mkdir()
    additional = ctx.work / "physical/memory_proxies.lef"
    env = {"SCORE_INPUT": str(physical_input or netlist),
           "SCORE_STAGE": "place" if place else "synth", "SCORE_TOP": ctx.top,
           "SCORE_OUT": str(out), "LIB_FILES": str(ctx.liberty[0]),
           "TECH_LEF": cfg["lefs"][0], "SC_LEF": cfg["lefs"][1],
           "ADDITIONAL_LEFS": " ".join([*cfg["lefs"][2:],
                                        *([str(additional)] if additional.exists() else [])]),
           "SDC_FILE": str(ctx.sdc), "SET_RC_TCL": cfg["tech_rc"]}
    # FlowContext.run obtains environment from the registered executable only.
    # A small generated launcher supplies this job's scorer inputs explicitly.
    script = ctx.write("score-launch.py", "import os, subprocess\n"
                       + "os.environ.update(" + repr(env) + ")\n"
                       + "raise SystemExit(subprocess.call("
                       + repr([str(ctx.tool("openroad")), "-exit", cfg["score_script"]]) + "))\n")
    ctx.run("scoring", [ctx.tool("python"), script], timeout=600)
    return json.loads((out / "score.json").read_text())


def finish(ctx: FlowContext, raw: Path, measurement: dict, *,
           physical_input: Path | None = None, place: bool = False) -> dict:
    netlist = structural(ctx, raw)
    common = score(ctx, netlist, physical_input, place)
    # The shared synthesis endpoint receives an already structural netlist.
    # No behavioral mapping or optimization is permitted in preparation for STA.
    from qor_endpoint import evaluate

    result = evaluate(ctx, netlist)
    phases = []
    document = ctx.work / "result.json"
    if document.exists():
        doc = json.loads(document.read_text())
        if doc.get("status") not in {"ok", "success", "pass"}:
            raise FlowError(f"LiveHD result reports {doc.get('status')}")
        phases = doc.get("phases", [])
    phase_file = ctx.work / "physical-phases.json"
    if phase_file.exists():
        phases = json.loads(phase_file.read_text())
    files = [p for p in (ctx.work / "physical").rglob("*") if p.is_file()]
    result["qor"]["placement_comparison"] = {
        "artifacts": str(ctx.work), "measurement": measurement, "score": common,
        "phases": phases, "stage": "place" if place else "synth",
        "netlist_sha256": sha(netlist), "sdc_sha256": sha(ctx.sdc),
        "physical_sha256": {str(p.relative_to(ctx.work)): sha(p) for p in files},
        "source_sha256": {str(p.relative_to(ctx.test.root)): sha(p)
                          for p in ctx.test.source_files() if p.is_file()},
        "timing_model": "placement RC / ideal clocks" if place else "zero-wire / ideal clocks",
    }
    result["qor"]["netlist_sha256"] = sha(netlist)
    result["sta"].update(common_opensta=common, time_unit="ps")
    return {"netlist": netlist, **result}


def run_native(ctx: FlowContext, *, place: bool = False, aegraph: bool = False,
               fp_plan: bool | None = None) -> dict:
    cfg = settings(ctx)
    command = [ctx.tool("lhd"), "synth", "--reader", "slang", "--top", ctx.top,
               "--workdir", "W", "--stats", "--result-json", "result.json",
               "--emit", "verilog:netlist.v", "--emit-dir", "report:reports",
               "--set", f"synth.liberty={ctx.liberty[0]}",
               "--set", f"synth.sdc={ctx.sdc}", "--set", "synth.mapper=abc",
               "--set", "pass.satopt=false",
               *mapping_settings(ctx.tech.name, "abc", ctx.abc_delay_ps())]
    if aegraph:
        command += ["--set", "compile.cprop.engine=aegraph"]
    if place and fp_plan is not None:
        command += ["--set", f"synth.place.fp={str(fp_plan).lower()}"]
    if place:
        command += ["--set", "synth.place=true", "--set", "synth.lef=" + ",".join(cfg["lefs"]),
                    "--set", "synth.place.tech_rc=" + cfg["tech_rc"],
                    "--emit-dir", "def:physical"]
    command += ["--", "-F", ctx.test.filelist, *ctx.test.verilog_args("synth"),
                *ctx.config.verilog_defines()]
    measurement = measured(ctx, command)
    result = finish(ctx, ctx.work / "netlist.v", measurement,
                    physical_input=ctx.work / "physical/out.def" if place else None, place=place)
    result["qor"]["placement_comparison"]["engine"] = "aegraph" if aegraph else "classic (default)"
    if place and fp_plan is not None:
        result["qor"]["placement_comparison"]["fp_plan"] = fp_plan
    result["qor"]["placement_comparison"]["synth_policy"] = {
        "mapper": "abc", "pass.satopt": False,
        "mapping_delay_ps": ctx.abc_delay_ps(), "base": "compiler defaults"}
    return result


def run_openroad(ctx: FlowContext, *, place: bool = False) -> dict:
    cfg = settings(ctx)
    # Synthesis and placement share one retained ORFS work tree. The placement
    # column begins at the exact netlist measured in the preceding column.
    shared = ctx.work.parent / "orfs"
    base = shared / "results/asap7" / ctx.top / "base"
    if place and not (base / "1_synth.odb").is_file():
        raise FlowSkip("OpenROAD pre-CTS requires the retained ORFS synthesis result")
    values = {"DESIGN_NAME": ctx.top, "PLATFORM": "asap7", "CORNER": "TC",
              "PLATFORM_DIR": cfg["platform"], "SDC_FILE": str(ctx.sdc),
              "VERILOG_FILES": " ".join(map(str, ctx.verilog_sources())),
              "SYNTH_HDL_FRONTEND": "slang", "SYNTH_SLANG_ARGS": " ".join(
                  [*ctx.test.verilog_args("synth"), *ctx.config.verilog_defines()]),
              "SLANG_PLUGIN_PATH": cfg["slang"], "SYNTH_SCRIPT": cfg["synth_script"],
              "CORE_UTILIZATION": "50", "PLACE_DENSITY": "0.60", "TNS_END_PERCENT": "100",
              "LEC_CHECK": "0", "NUM_CORES": str(cfg["threads"]), "WORK_HOME": str(shared)}
    if cfg.get("macro_liberty"):
        values.update(ADDITIONAL_LIBS=" ".join(cfg["macro_liberty"]),
                      ADDITIONAL_LEFS=" ".join(cfg["lefs"][2:]))
    config = ctx.work.parent / "orfs-config.mk"
    text = "".join(f"export {k} = {v}\n" for k, v in values.items())
    if not config.exists():
        config.write_text(text)
    elif config.read_text() != text:
        raise FlowError("ORFS synthesis/placement input configuration differs")
    odb = base / ("3_place.odb" if place else "1_synth.odb")
    physical = ctx.work / "physical"
    physical.mkdir()
    script = ctx.write("export.tcl", f"read_db {tcl(odb)}\n"
                       + f"write_def {tcl(physical / 'out.def')}\n"
                       + f"write_lef {tcl(physical / 'cells.lef')}\n"
                       + "set physical_masters {}\n"
                       + "foreach inst [[ord::get_db_block] getInsts] {\n"
                       + "  set master [$inst getMaster]\n"
                       + "  if {[$master isFiller] || [$master isEndCap] || "
                       + "[$master getType] eq \"CORE_WELLTAP\"} {\n"
                       + "    lappend physical_masters [$master getName]\n  }\n}\n"
                       + f"write_verilog -remove_cells [lsort -unique $physical_masters] "
                       + f"{tcl(physical / 'netlist.v')}\n")
    command = [ctx.tool("make"), "-f", cfg["flow_home"] + "/Makefile",
               "DESIGN_CONFIG=" + str(config), "FLOW_HOME=" + cfg["flow_home"]]
    targets = ["floorplan", "place"] if place else ["synth"]
    steps = [{"name": "floorplan_place" if place else "synthesis",
              "argv": list(map(str, [*command, *targets]))},
             {"name": "physical_export", "argv": [str(ctx.tool("openroad")), "-exit", str(script)]}]
    spec = ctx.write("physical-steps.json", json.dumps({
        "steps": steps, "phases": str(ctx.work / "physical-phases.json"),
        "physical": str(physical), "lefs": cfg["lefs"]}, indent=2))
    helper = ctx.work / "physical_steps.py"
    shutil.copy2(Path(__file__).with_name("physical_steps.py"), helper)
    env = {"YOSYS_EXE": str(ctx.tool("yosys")), "OPENROAD_EXE": str(ctx.tool("openroad")),
           "PYTHON_EXE": str(ctx.tool("python")), "STDBUF_CMD": "",
           "OMP_NUM_THREADS": str(cfg["threads"]), "OPENROAD_NUM_THREADS": str(cfg["threads"])}
    measurement = measured(ctx, [ctx.tool("python"), helper, spec], env)
    result = finish(ctx, physical / "netlist.v", measurement, physical_input=odb, place=place)
    result["qor"]["placement_comparison"]["synth_policy"] = values
    return result
