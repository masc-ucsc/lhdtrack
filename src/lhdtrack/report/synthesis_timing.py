"""Synthesis step diagnostics, rendered entirely from recorded ledger evidence."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
from statistics import median

from ..ledger import Ledger, slug


def profile_pairs(history: list[dict], variant: str = "defaults") -> list[tuple[dict, dict]]:
    """Use each slot's latest paired run, retaining failed attempts as visible rows."""
    runs = defaultdict(dict)
    for row in history:
        if (row.get("synthesis_timing_profile") and row.get("tech") == "asap7"
                and row.get("synthesis_timing_variant", "defaults") == variant):
            key = row["test"], row.get("config", "default"), row["run_id"]
            runs[key][row["flow"]] = row
    selected = {}
    suffix = "_ware_cutoff" if variant == "ware-cutoff-1" else ""
    lhd_flow = ("syn_lhd_verilog_abc_sn" if variant == "brent"
                else "syn_lhd_verilog_no_satopt" + suffix)
    sn_flow = "syn_abc_sn" + suffix
    for key, entries in sorted(runs.items()):
        if all(f in entries for f in (lhd_flow, sn_flow)):
            selected[key[:2]] = (entries[lhd_flow], entries[sn_flow])
    return list(selected.values())


def lhd_steps(row: dict) -> dict[str, float]:
    phases = row.get("qor", {}).get("timing_breakdown", {}).get("phases", [])
    result = defaultdict(float)
    entries = phases.items() if isinstance(phases, dict) else (
        (p.get("name", ""), p.get("ms", 0)) for p in phases)
    for name, ms in entries:
        result[name] += float(ms)
    return dict(result)


def timing_section(history: list[dict], host: str, variant: str = "defaults") -> str:
    from .html import _e

    pairs = profile_pairs(history, variant)
    if not pairs:
        return ""
    body = []
    for lhd, sn in pairs:
        phases = lhd_steps(lhd)
        timing = sn.get("qor", {}).get("timing_breakdown", {})
        front, mapper = timing.get("frontend_ms", {}), timing.get("mapper_ms", {})
        name = f"{lhd['test']}/{lhd.get('config', 'default')}"
        if lhd.get("status") != "ok" or sn.get("status") != "ok":
            body.append(f'<tr><td class="l">{_e(name)}</td><td colspan="6">'
                        f'LHD: {_e(lhd["status"])}; SN: {_e(sn["status"])}</td></tr>')
            continue
        from .verilog_eval import sn_compatible
        if not sn_compatible(sn, lhd):
            body.append(f'<tr><td class="l">{_e(name)}</td><td colspan="6">'
                        'Incompatible host class or Liberty</td></tr>')
            continue
        values = [phases.get("inou.slang"), sum(front.get(k, 0) for k in
                  ("slang_parse", "slang_elaborate")), phases.get("pass.abc"),
                  mapper.get("shared_flow"), sn.get("time_ms", {}).get("map")]
        t = lhd.get("qor", {}).get("timing_breakdown", {})
        trials = sum(r.get("ware_trials", 0) for r in t.get("regions", []))
        policy = lhd.get("qor", {}).get("synth_policy", {})
        arithmetic = (f'<br><small>multiplier={_e(policy.get("pass.abc.multiplier", "unspecified"))}</small>'
                      if variant == "brent" else "")
        body.append('<tr><td class="l">' + _e(name) + arithmetic + '</td>' + ''.join(
            f'<td>{v / 1000:.3f}</td>' if v is not None else '<td>—</td>' for v in values)
                    + f'<td>{trials}</td></tr>')
    label = 'Fixed Brent-Kung · zero arithmetic trials' if variant == 'brent' else 'Default/auto'
    return (f'<h2>Synthesis step profiling · {label}</h2><p>Seconds, from sequential paired diagnostic runs '
            'using the tools recorded for each run. LHD frontend includes slang and LNAST lowering; '
            'SN frontend here is parsing plus elaboration only. LHD pass.abc includes region '
            'handling and arithmetic trials; SN shared flow includes ABC optimization, mapping, '
            'sizing and candidate evaluation. These scopes differ. '
            f'<a href="results-syn-timing-{slug(host)}.html">Full step breakdown and commands</a>'
            '.</p><div class="scroll"><table><thead><tr><th class="l">Test / config</th>'
            '<th>LHD frontend</th><th>SN slang</th><th>LHD pass.abc</th>'
            '<th>SN shared ABC flow</th><th>SN mapper total</th><th>LHD extra trials</th>'
            '</tr></thead><tbody>' + ''.join(body) + '</tbody></table></div>')


def write_timing(root: Path, host: str) -> Path | None:
    from .html import _e, _page

    history = Ledger(root).load(host)
    defaults = profile_pairs(history)
    brent = profile_pairs(history, "brent")
    pairs = defaults + brent
    if not pairs:
        return None
    body = ['<h1>LHD ABC / ABC-SN synthesis timing</h1>',
            f'<p><a href="results-syn-{slug(host)}.html">Synthesis results</a></p>',
            timing_section(history, host),
            timing_section(history, host, "brent"),
            '<p>Individual compiler phases and mapper steps below are milliseconds. '
            'SN frontend process overhead includes startup, binary serialization and teardown. '
            'Internal LHD OpenTimer and external emission / QoR collection are shown separately '
            'and excluded from reported synthesis time. Summed region durations are overlapping '
            'work when parallel; they cannot be subtracted from pass wall time. '
            'Pure ABC command time is currently instrumented only for SN. '
            'These diagnostic measurements do not establish netlist equivalence.</p>']
    body.append('<p>In the measured SN flow, @blast -c -f uses Brent–Kung parallel-prefix '
                'addition/subtraction and Baugh–Wooley multiplication with depth-ordered '
                'full-adder column compression. The final multiplication addition also uses '
                'Brent–Kung. No ripple (-r) or Booth (-b) override is passed. '
                'Default LHD initially uses ripple-carry arithmetic and can re-map existing regions '
                'with carry-lookahead, carry-skip, tree multiplier or alternate barrel-shifter '
                'implementations. These arithmetic trials preserve the partition boundaries. '
                'Consequently, sharing ABC scripts does not imply identical input networks '
                'or an equal number of mappings. The fixed comparison pins Brent–Kung and '
                'the recorded multiplier; newer measurements select the SN column compressor, '
                'while historical array measurements keep their original settings.</p>')
    for lhd, sn in pairs:
        name = f"{lhd['test']}/{lhd.get('config', 'default')}"
        body.append(f'<h2>{_e(name)}</h2><p>Run: {_e(lhd["run_id"])}; '
                    f'LHD: {_e(lhd.get("versions", {}).get("lhd", ""))}; '
                    f'SN: {_e(sn.get("versions", {}).get("sn", ""))}</p>')
        ltiming = lhd.get("qor", {}).get("timing_breakdown", {})
        stiming = sn.get("qor", {}).get("timing_breakdown", {})
        groups = [("LHD compiler phases", lhd_steps(lhd)),
                  ("SN frontend", stiming.get("frontend_ms", {})),
                  ("SN mapper", stiming.get("mapper_ms", {})),
                  ("LHD emission / QoR", {k: v for k, v in lhd.get("time_ms", {}).items()
                                          if k not in ("compile", "color", "map", "total")}),
                  ("SN emission / QoR", {k: v for k, v in sn.get("time_ms", {}).items()
                                         if k not in ("frontend", "map", "total")})]
        body.append('<div class="scroll"><table><thead><tr><th class="l">Group</th>'
                    '<th class="l">Step</th><th>ms</th></tr></thead><tbody>')
        for group, steps in groups:
            for step, ms in steps.items():
                body.append(f'<tr><td class="l">{_e(group)}</td><td class="l">{_e(step)}</td>'
                            f'<td>{ms:.3f}</td></tr>')
        body.append('</tbody></table></div><details><summary>SN ABC calls / '
                    'LHD region accounting</summary><pre>' + _e(json.dumps({
                        "sn_abc_commands": stiming.get("abc_commands", []),
                        "lhd_regions": ltiming.get("regions", []),
                        "lhd_parallel": ltiming.get("parallel", {}),
                        "lhd_invocation": ltiming.get("synthesis_invocation", {}),
                        "lhd_synth_policy": lhd.get("qor", {}).get("synth_policy", {}),
                    }, indent=2)) + '</pre></details>')
    ablations = profile_pairs(history, "ware-cutoff-1")
    if ablations:
        defaults = {(l["test"], l["config"]): l for l, _ in defaults}
        body.append('<h2>Arithmetic trial ablation</h2><p>Only the diagnostic LHD run sets '
                    'pass.abc.ware_max_cells=1, skipping post-mapping arithmetic trials on '
                    'these designs. Default measurements and baseline recipes are preserved. '
                    'All times below are broad pass.abc wall time, in seconds.</p>'
                    '<div class="scroll"><table><thead><tr><th class="l">Test</th>'
                    '<th>Default</th><th>Trials skipped</th><th>Default / skipped</th>'
                    '<th>Default cells</th><th>Skipped cells</th>'
                    '<th>Default path (ps)</th><th>Skipped path (ps)</th>'
                    '</tr></thead><tbody>')
        for experiment, _ in ablations:
            base = defaults.get((experiment["test"], experiment["config"]))
            if not base or base.get("versions") != experiment.get("versions"):
                continue
            a, b = lhd_steps(base).get("pass.abc"), lhd_steps(experiment).get("pass.abc")
            if not a or not b or experiment.get("status") != "ok":
                continue
            body.append(f'<tr><td class="l">{_e(experiment["test"])}</td><td>{a/1000:.3f}</td>'
                        f'<td>{b/1000:.3f}</td><td>{a/b:.3f}×</td>'
                        f'<td>{base["qor"].get("cells", "—")}</td>'
                        f'<td>{experiment["qor"].get("cells", "—")}</td>'
                        f'<td>{base.get("sta", {}).get("opensta_ns", "—")}</td>'
                        f'<td>{experiment.get("sta", {}).get("opensta_ns", "—")}</td></tr>')
        body.append('</tbody></table></div>')
    replays = { (r["test"], r.get("config", "default")): r for r in
               sorted(history, key=lambda r: r.get("run_id", ""))
               if r.get("flow") == "profile_abc_replay" and r.get("status") == "ok" }
    if replays:
        body.append('<h2>Identical-network ABC replay</h2><p>Both staged ABC executables '
                    'read the same exported BLIF, Liberty and recorded command sequence. '
                    'Both use the default unconstrained boundary environment. Values are '
                    'median process wall seconds, including startup, input/library reads and '
                    'output; this is a control for ABC binary differences, not a frontend '
                    'or LHD region comparison.</p><div class="scroll"><table><thead><tr>'
                    '<th class="l">Test</th><th>Staged baseline ABC</th>'
                    '<th>New ABC</th><th>Baseline / new</th></tr></thead><tbody>')
        for row in replays.values():
            observation = row["qor"]["abc_replay"]
            a, b = (median(observation["measurements"][k]["time_ms"])
                    for k in ("abc", "abc_sn"))
            body.append(f'<tr><td class="l">{_e(row["test"])}</td><td>{a/1000:.3f}</td>'
                        f'<td>{b/1000:.3f}</td><td>{a/b:.3f}×</td></tr>')
        body.append('</tbody></table></div>')
    out = root / "target" / f"results-syn-timing-{slug(host)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_page("Synthesis step timing", ''.join(body)))
    return out
