"""LiveHD synthesis, VERILOG in.

The middle row of the three-flow rule, and the one that makes the other two
readable:

    syn_lhd_verilog vs syn_lhd_pyrope   -> Pyrope vs Verilog as a LANGUAGE
                                           (identical backend)
    syn_lhd_verilog vs syn_yosys_abc    -> LiveHD vs yosys+slang/abc as a TOOL
                                           (identical source)

Without it every daily movement is confounded and nothing can be attributed.

NOT CACHEABLE: the lhd SHA moves daily, so the key would always miss anyway --
and re-measuring LiveHD is the entire point.
"""

from __future__ import annotations

from lhdtrack.context import FlowContext, FlowError, FlowSkip
from lib.lhd_synth_policy import abc_sn_settings, mapping_settings

NAME = "syn_lhd_verilog"
KIND = "synth"
NEEDS = ("lhd", "yosys")
OPTIONAL = ("sta",)
USES_TECH = True


def run(ctx: FlowContext) -> dict:
    return run_mapper(ctx, "abc")


def run_mapper(ctx: FlowContext, mapper: str, *, satopt: bool = True,
               abc_sn: bool = False, flat: bool = False) -> dict:
    import hashlib
    import json

    ctx.require_sdc()
    lhd = ctx.tool("lhd")
    params = [f"-G{k}={v}" for k, v in sorted(ctx.chparams().items())]
    # TELL lhd WHICH LIBRARY. `synth.liberty` -- the ONE Liberty pass.abc,
    # pass.opentimer and `lhd synth` all read (the former per-pass
    # `pass.abc.library` was removed) -- defaults to
    # $HAGENT_TECH_DIR/sky130_..., so without this every technology would
    # silently map to sky130 and the ASAP7 rows would be sky130 wearing an
    # ASAP7 label. The staged ASAP7 Liberty is merged ahead of time because
    # ABC's read_lib takes one file.
    if len(ctx.liberty) != 1:
        raise FlowSkip(
            f"lhd's synth.liberty takes a single Liberty file, but "
            f"{ctx.tech.name} staged {len(ctx.liberty)} Liberty files; "
            "merge the technology before running LiveHD"
        )
    settings = [
        "--set", f"synth.mapper={mapper}",
        "--set", f"synth.liberty={ctx.liberty[0]}",
        *mapping_settings(ctx.tech.name, mapper, str(ctx.abc_delay_ps())),
        # SAT optimization is an explicit experiment; the compiler defaults
        # it off. Keep each profile's recorded name consistent with its argv.
        "--set", f"pass.satopt={str(satopt).lower()}",
    ]
    if abc_sn:
        if mapper != "abc" or ctx.tech.name != "asap7":
            raise FlowSkip("the fixed Brent-Kung ABC/SN comparison requires ABC on ASAP7")
        settings = abc_sn_settings(settings)
    lhd = ctx.tool("lhd")
    top = f"{ctx.top}.{ctx.top}"
    if flat:
        _flat_synthesis(ctx, settings, params)
    else:
        ctx.run(
            "synth",
            [lhd, "synth", "--reader", "slang", "--top", ctx.top,
             "--emit-dir", "lg:netlist", "--workdir", "W",
             "--result-json", "synth.json", "--stats", *settings,
             "--", "-F", str(ctx.test.filelist), "-DSYNTHESIS", "-DBR_PPA_SYNTHESIS", *params],
            timeout=ctx.lec_timeout_s * 2 + 60,
        )
    # Both languages use the fused flow and the compiler's threading defaults.
    from syn_lhd_pyrope import _phases

    phases = _phases(ctx)
    if phases:
        ctx.stage.time_ms.pop("synth", None)
        rss = ctx.stage.peak_rss_kb.pop("synth", ctx.stage.peak_rss_kb.get("map", 0))
        ctx.stage.time_ms.update(phases)
        ctx.stage.peak_rss_kb["map"] = rss

    # There is no `pass cgen`. A gate-level Verilog emission comes from feeding
    # the mapped lg: library back through `lhd compile` with an
    # `--emit-dir verilog:` -- lg: directories are valid compile INPUTS.
    #
    # This emission is what lets OpenSTA time the SAME netlist LiveHD's own
    # OpenTimer times. Without it the two engines would be timing two different
    # things and the correlation column would mean nothing.
    ctx.run(
        "emit",
        # The graph is already technology-mapped, so this is only the typed
        # LG->Verilog emitter. `--recipe O0` used to pin that; recipes were
        # removed from the CLI (compile now always runs cprop + bitwidth), and
        # passing one is a hard usage error.
        [lhd, "compile", "lg:netlist", "--top", top,
         "--emit-dir", "verilog:netv", "--workdir", "Wemit"],
    )

    from qor_endpoint import emitted_verilog, evaluate

    try:
        netlist = emitted_verilog(ctx, ctx.work / "netv")
    except FlowError as error:
        raise FlowError(
            "lhd emitted no gate-level Verilog; OpenSTA cannot time the netlist "
            "(check `lhd pass cgen verilog` support for mapped designs)"
        ) from error

    # The proof consumes this exact emission, including when QoR collection fails.
    digest = hashlib.sha256(netlist.read_bytes()).hexdigest()
    sdc_digest = hashlib.sha256(ctx.sdc.read_bytes()).hexdigest()
    source_digests = {str(p): hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in ctx.test.source_files() if p.is_file()}
    ctx.write("synth-artifacts.json", json.dumps({
        "mapper": mapper, "netlist": str(netlist), "sha256": digest,
        "reference": str(ctx.work / "W/synth/lg"),
        "sdc_sha256": sdc_digest, "source_sha256": source_digests,
    }, indent=2))
    result = evaluate(ctx, netlist, lgraph=ctx.work / "netlist",
                      qor_json=ctx.work / "W" / "synth" / "qor.json")
    result["qor"].update(mapper=mapper, netlist_sha256=digest,
                         liberty_sha256=ctx.tech.sha256, sdc_sha256=sdc_digest,
                         source_sha256=source_digests)
    # Preserve individual compiler steps in the ledger, in addition to the
    # coarse compile/color/map columns. Region durations are overlapping work
    # when parallel; they must never be subtracted from pass wall time.
    document = json.loads((ctx.work / "synth.json").read_text())
    abc = document.get("qor", {}).get("abc", {})
    if abc_sn:
        regions = abc.get("regions")
        if not isinstance(regions, list) or any("ware_trials" not in r for r in regions):
            raise FlowError("fixed Brent-Kung comparison lacks arithmetic-trial accounting")
        if any(region["ware_trials"] for region in regions):
            raise FlowError("fixed Brent-Kung comparison unexpectedly ran arithmetic trials")
    result["qor"]["timing_breakdown"] = {
        "schema_version": 1,
        "phases": document.get("phases", []),
        "synthesis_invocation": document.get("synthesis_invocation", {}),
        "regions": [{k: region[k] for k in ("module", "ms", "ware_trials", "cache",
                                            "color", "gates", "instances", "input_nodes",
                                            "input_ge", "pred_aig", "logic_depth", "boundary")
                     if k in region} for region in abc.get("regions", [])],
        "parallel": abc.get("parallel", {}),
        **({"process_time_ms": document["process_time_ms"]} if flat else {}),
    }
    policy = result["qor"].setdefault("synth_policy", {})
    policy["synth.mapper"] = mapper
    policy["pass.satopt"] = str(satopt).lower()
    if abc_sn:
        result["qor"]["comparison_profile"] = "abc_sn_brent_sn"
        result["qor"]["arithmetic_trials"] = 0
    if flat:
        result["qor"]["coloring_profile"] = "flat-no-arithmetic-boundaries"
        policy.update({"pass.color.alg": "flat", "pass.abc.flatten": "true",
                       "pass.abc.large_ge": "0",
                       **{f"pass.color.ware_{kind}": "false"
                          for kind in ("arith", "cmp", "shift")}})
    if mapper == "usyn":
        from lib.usyn_evidence import native_evidence

        evidence_path = ctx.work / "W/synth/qor.json.usyn.json"
        usyn = json.loads((ctx.work / "W/synth/qor.json.usyn.json").read_text())
        if usyn.get("tmap") != "abc" or usyn.get("output") != "mapped-cmos":
            raise FlowError(
                "USYN evaluation requires tmap=abc and output=mapped-cmos, "
                f"got tmap={usyn.get('tmap')} output={usyn.get('output')}"
            )
        result["qor"].update(usyn_tmap=usyn["tmap"], usyn_output=usyn["output"],
                             usyn_evidence=native_evidence(evidence_path))
        policy["pass.usyn.tmap"] = usyn["tmap"]
        policy.setdefault("pass.usyn.delay", str(ctx.abc_delay_ps()))
    return {"netlist": netlist, **result}


def _flat_synthesis(ctx: FlowContext, settings: list[str], params: list[str]) -> None:
    """The supported manual flat path; fused synth intentionally requires synth coloring.

    Include arithmetic in the top color. The compiler still preserves its default
    memory implementation boundaries, which remain explicit in region accounting.
    """
    import json

    lhd = ctx.tool("lhd")
    top = f"{ctx.top}.{ctx.top}"
    common = ["--top", top, "--stats", *settings]
    ctx.run("compile", [lhd, "compile", "--reader", "slang", "--top", ctx.top,
                        "--emit-dir", "lg:W/synth/lg", "--workdir", "Wcompile",
                        "--result-json", "compile.json", "--stats", *settings,
                        "--", "-F", ctx.test.filelist, "-DSYNTHESIS",
                        "-DBR_PPA_SYNTHESIS", *params], timeout=ctx.lec_timeout_s * 2 + 60)
    color_sets = [a for kind in ("arith", "cmp", "shift")
                  for a in ("--set", f"pass.color.ware_{kind}=false")]
    ctx.run("color", [lhd, "pass", "color", "flat", "lg:W/synth/lg", *common,
                      *color_sets, "--workdir", "Wcolor", "--result-json", "color.json"])
    ctx.run("map", [lhd, "pass", "abc", "lg:W/synth/lg", *common,
                    "--set", "pass.abc.flatten=true", "--emit-dir", "lg:netlist",
                    "--set", "pass.abc.large_ge=0",
                    "--workdir", "W/synth", "--result-json", "map.json"],
            timeout=ctx.lec_timeout_s * 2 + 60)
    ctx.run("opentimer", [lhd, "pass", "opentimer", "lg:netlist", *common,
                          "--workdir", "Wtiming", "--result-json", "opentimer.json"],
            timeout=ctx.lec_timeout_s * 2 + 60)
    documents = [json.loads((ctx.work / f"{label}.json").read_text())
                 for label in ("compile", "color", "map", "opentimer")]
    process_times = dict(ctx.stage.time_ms)
    # As in the fused flow, internal OpenTimer is outside reported synthesis time.
    ctx.stage.time_ms.pop("opentimer")
    abc = json.loads((ctx.work / "W/synth/qor.json").read_text())
    ctx.write("synth.json", json.dumps({
        "schema_version": 1, "command": "manual-flat-synthesis",
        "phases": [p for doc in documents for p in doc.get("phases", [])],
        "qor": {"abc": abc}, "process_time_ms": process_times,
        "synthesis_invocation": {"scope": "compile-color-map-opentimer-processes",
                                 "wall_ms": sum(process_times.values())},
    }, indent=2) + "\n")
