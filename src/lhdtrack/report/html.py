"""Render `target/` from `data/`.

Host results have separate synthesis, simulation and equivalence pages.
Synthesis keeps its STA diagnostics; simulation keeps backend speed comparisons;
LEC keeps proof coverage and timing. Explicitly named snapshots can still show
the complete selected evaluation.

Two more rules shape everything below.

AGGREGATE ONLY WHAT IS COMPARABLE. The headline geomean covers rows whose
Pyrope is hand-written AND LEC-proven. An `auto` seed enters the same LGraph the
Verilog does, so aggregating it would report a front-end delta as a language
result; an unproven pair might be two different circuits.

NEVER PLOT ACROSS A DISCONTINUITY. A series breaks at any host or tool-version
change. A step in the chart caused by a new machine is worse than no chart.
"""

from __future__ import annotations

import datetime as _dt
import html
import json
import math
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

from ..corpus import discover
from ..ledger import DATA_DIR, TARGET_DIR, Ledger, slug
from ..run import host_name

CSS = """
:root{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;--head:#f7f8fa;
--good:#0a7c3f;--bad:#b91c1c;--warn:#a16207;--accent:#1d4ed8;--chip:#eef2ff;--series-a:#b45309;--series-b:#1d4ed8;--series-c:#0a7c3f}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ab;
--line:#262b33;--head:#161a20;--good:#4ade80;--bad:#f87171;--warn:#fbbf24;
--accent:#93b4ff;--chip:#1b2233;--series-a:#fbbf24;--series-b:#93b4ff;--series-c:#4ade80}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.5rem 4rem;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .25rem}
h2{font-size:1.05rem;margin:2.5rem 0 .5rem;padding-bottom:.35rem;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);margin:0 0 1.5rem}
.meta{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 1.5rem}
.chip{background:var(--chip);border-radius:999px;padding:.15rem .6rem;font-size:12px;
white-space:normal;overflow-wrap:anywhere;max-width:100%}
nav a{text-decoration:none;color:var(--accent)}
nav a[aria-current="page"]{background:var(--accent);color:var(--bg)}
.scroll{overflow-x:auto;border:1px solid var(--line);border-radius:8px}
table{border-collapse:collapse;width:100%;font-variant-numeric:tabular-nums}
th,td{padding:.4rem .6rem;text-align:right;border-bottom:1px solid var(--line);white-space:nowrap}
th{background:var(--head);font-weight:600;position:sticky;top:0}
th.g{border-left:2px solid var(--line)}
td.g{border-left:2px solid var(--line)}
td.l,th.l{text-align:left}
tbody tr:hover{background:var(--head)}
tfoot td{font-weight:600;background:var(--head)}
.good{color:var(--good)}.bad{color:var(--bad)}.warn{color:var(--warn)}
.muted{color:var(--muted)}
.tag{font-size:11px;padding:.05rem .35rem;border-radius:4px;background:var(--chip);
color:var(--muted)}
.note{color:var(--muted);font-size:12px;max-width:34rem;white-space:normal;text-align:left}
svg{display:block;max-width:100%}
.legend{display:flex;gap:1rem;flex-wrap:wrap;margin:.5rem 0 0;font-size:12px;color:var(--muted)}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:.3rem}
"""

SERIES_COLORS = ["#1d4ed8", "#b45309", "#0a7c3f", "#7c3aed", "#be123c", "#0891b2"]

RESULT_KINDS = {"synth": ("syn", "Synthesis"), "sim": ("sim", "Simulation"),
                "lec": ("lec", "LEC")}


def _results_nav(host: str, kind: str | None = None) -> str:
    links = []
    for key, (suffix, label) in RESULT_KINDS.items():
        current = ' aria-current="page"' if key == kind else ""
        links.append(f'<a class="chip"{current} href="results-{suffix}-{slug(host)}.html">'
                     f'{label}</a>')
    return ('<nav class="meta" aria-label="Results">'
            '<a class="chip" href="index.html">Machines</a>' + "".join(links)
            + f'<a class="chip" href="timeseries-{slug(host)}.html">History</a></nav>')


def _coverage_plot(groups: list[tuple[str, Counter]]) -> str:
    """Count outcomes independently; bounded proofs keep their own category."""
    categories = ("proven", "bounded", "refuted", "timeout", "inconclusive",
                  "unsupported", "error", "skipped", "not-measured")
    colors = ("var(--good)", "var(--accent)", "var(--bad)", "var(--warn)",
              "var(--series-a)", "var(--muted)", "var(--series-b)",
              "#7c3aed", "var(--line)")
    groups = [(label, counts) for label, counts in groups if counts]
    if not groups:
        return '<p class="sub">No verdicts recorded yet.</p>'
    # Unknown outcomes remain visible instead of disappearing from the denominator.
    extra = sorted({v for _, counts in groups for v in counts} - set(categories))
    palette = dict(zip(categories, colors))
    categories = (*categories, *extra)
    height = 32 * len(groups) + 16
    bars = []
    for i, (label, counts) in enumerate(groups):
        total = sum(counts.values())
        bars.append(f'<text x="4" y="{i * 32 + 24}" fill="var(--fg)" '
                    f'font-size="12">{_e(label)}</text>')
        x = 330.0
        for verdict in categories:
            count = counts.get(verdict, 0)
            if not count:
                continue
            width = 500 * count / total
            bars.append(f'<rect x="{x:.2f}" y="{i * 32 + 8}" width="{width:.2f}" '
                        f'height="22" fill="{palette.get(verdict, "var(--muted)")}">'
                        f'<title>{_e(label)}: {_e(verdict)} {count}/{total}</title></rect>')
            x += width
        bars.append(f'<text x="840" y="{i * 32 + 24}" fill="var(--muted)" '
                    f'font-size="12">n={total}</text>')
    legend = "".join(f'<span><i style="background:{palette.get(v, "var(--muted)")}"></i>'
                     f'{_e(v)}</span>' for v in categories
                     if any(counts.get(v) for _, counts in groups))
    return ('<div class="scroll"><svg role="img" aria-label="Verdict coverage by checker" '
            f'viewBox="0 0 920 {height}" style="min-width:700px">'
            + "".join(bars) + f'</svg></div><div class="legend">{legend}</div>')


def _coverage_verdict(block: dict, fallback: str = "not-measured") -> str:
    if fallback == "ok":
        fallback = "not-measured"
    verdict = _display_lec_verdict(block) or fallback
    return "bounded" if verdict.startswith("bounded(") else verdict


def write_results(root: Path, cfg: dict | None = None, host: str | None = None) -> list[Path]:
    """Render separate domains for a host."""
    host = host or host_name()
    outputs = [write_report(root, cfg=cfg, host=host, kind=kind)
               for kind in RESULT_KINDS]
    evaluation = root / DATA_DIR / f"verilog-eval-{slug(host)}.json"
    if evaluation.exists():
        auxiliary = json.loads(evaluation.read_text()).get("auxiliary_report")
        if auxiliary:
            name = _auxiliary_report_name(host, auxiliary)
            outputs.append(write_report(root, out=root / TARGET_DIR / name, cfg=cfg,
                                        host=host, prefer_evaluation=False, kind="synth"))
    return outputs


def _auxiliary_report_name(host: str, name: str) -> str:
    return (f"results-syn-{slug(host)}-full.html"
            if name == f"report-{slug(host)}-full.html" else name)


def _e(x) -> str:
    return html.escape(str(x))


def _fmt(v, digits=2, dash="—"):
    if v is None or v == "":
        return dash
    if isinstance(v, float):
        return f"{v:,.{digits}f}"
    if isinstance(v, int):
        return f"{v:,}"
    return _e(v)


def _synth_display_values(row):
    """Show every recorded metric, labeling partial mapped-logic measurements."""
    qor, sta = row.get("qor", {}), row.get("sta", {})
    ms, kb = row.get("time_ms", {}).get("total"), row.get("peak_rss_kb", {}).get("max")
    values = [sta.get("opensta_ns"), qor.get("area_um2"), qor.get("cells"),
              qor.get("logic_depth"), ms / 1000 if ms is not None else None,
              kb / 1024 if kb is not None else None]
    scopes = [""] * len(values)
    for index, field, scope in ((0, "abc_max_delay_ns", "region"),
                                (1, "lhd_area_um2", "logic"), (2, "lhd_cells", "logic")):
        if values[index] is None and qor.get(field) is not None:
            values[index], scopes[index] = qor[field], scope
    return values, scopes


def _synth_scope_tag(scope):
    if not scope:
        return ""
    explanation = ("Maximum mapped region delay in this library's time unit; whole-design STA unavailable."
                   if scope == "region" else
                   "Mapped combinational logic only; preserved native state or memory is not included.")
    return f' <span class="tag" title="{_e(explanation)}">{_e(scope)}</span>'


def _display_lec_verdict(block: dict | None) -> str | None:
    """Normalize legacy UNKNOWN rows using the current timeout contract.

    Older ledgers labeled every solver UNKNOWN as `timeout`, even when lgcheck
    returned in a second against a 300-second budget.  New runs classify those
    as `inconclusive`; apply the same rule while rendering old measurements so
    a focused corrective rerun need not re-run hundreds of unaffected netlists.
    Explicit prerequisite timeouts carry a reason and remain timeouts.
    """
    block = block or {}
    verdict = block.get("verdict")
    if verdict == "proven" and block.get("bounded"):
        return f"bounded({block.get('bound', '?')})"
    if verdict != "timeout" or block.get("reason"):
        return verdict
    timeout_s = block.get("timeout_s")
    elapsed_ms = block.get("ms")
    if timeout_s is None or elapsed_ms is None:
        return verdict
    budget_ms = timeout_s * 1000 * (2 if block.get("solver") == "lgyosys" else 1)
    return "inconclusive" if elapsed_ms < budget_ms * 0.98 else verdict


def _gain(measured, base) -> float | None:
    """baseline / measured — the ONE orientation used everywhere on this page.

    Every metric lhdtrack reports is one where less is better: nanoseconds,
    µm², seconds, megabytes. Dividing the baseline BY the measurement turns all
    of them into the same thing — "how many times better than the baseline" —
    so a reader never has to remember which column inverts. 2.00× is twice as
    fast, or half the area, or half the memory.
    """
    if not measured or not base:
        return None
    return base / measured


def _ratio(values: list[float]) -> str:
    """A geomean cell, coloured. An absent ratio prints a dash, never `—×`."""
    g = _geomean(values)
    if not g:
        return '<span class="muted">—</span>'
    cls = "good" if g > 1.02 else ("bad" if g < 0.98 else "muted")
    return f'<span class="{cls}">{g:.2f}×</span>'


def _geomean(values: list[float]) -> float | None:
    vals = [v for v in values if v and v > 0]
    if not vals:
        return None
    return math.exp(sum(math.log(v) for v in vals) / len(vals))


def _synth_policy_note(rows: list[dict]) -> str:
    """Show recorded settings without projecting current defaults onto old rows."""
    policies: dict[tuple, int] = defaultdict(int)
    missing = 0
    for row in rows:
        if not row.get("flow", "").startswith("syn_lhd_"):
            continue
        if row.get("qor", {}).get("cells") is None:
            continue
        policy = row.get("qor", {}).get("synth_policy")
        if policy:
            policies[tuple(sorted(policy.items()))] += 1
        else:
            missing += 1
    if not policies:
        return ""
    parts = []
    for policy, count in sorted(policies.items()):
        settings = "; ".join(f"{key.removeprefix('pass.')}={value}" for key, value in policy)
        parts.append(f"<p class='sub'>LiveHD settings ({count} measurements): "
                     f"<code>{_e(settings)}</code>.</p>")
    if missing:
        parts.append(f"<p class='sub'>{missing} older measurements have no recorded settings.</p>")
    return "".join(parts)


# ---------------------------------------------------------------- report ----
def _satopt_comparison(root: Path, host: str, name: str = "satopt") -> str:
    """Render a dated evaluation derived from immutable ledger observations."""
    path = root / "data" / f"{slug(name)}-{slug(host)}-comparison.json"
    if not path.exists():
        return ""
    doc = json.loads(path.read_text())
    if doc.get("host") != host or not doc.get("liberty_match"):
        return ""
    metrics = ("delay", "area", "cells", "depth", "time", "mem")
    labels = ("Delay", "Area", "Cells", "Depth", "Runtime", "Memory")

    def ratios(values, counts=None):
        return "".join(
            f'<td class="{"good" if values.get(m, 1) >= 1 else "bad"}">'
            f'{_fmt(values.get(m), 3)}×'
            f'{" <small>(n=" + str(counts.get(m, 0)) + ")</small>" if counts is not None else ""}</td>'
            if values.get(m) is not None else '<td>—</td>'
            for m in metrics
        )

    headers = "".join(f"<th>{label}</th>" for label in labels)
    summary = "".join(
        f'<tr><td class="l">{_e(r["tech"])}</td><td class="l">{_e(r["flow"])}</td>'
        f'<td class="l">{_e(r["population"])}</td><td>{r["n"]}</td>'
        f'{ratios(r["ratios"], r["counts"])}</tr>' for r in doc["summary"]
    )
    detail = "".join(
        f'<tr><td class="l">{_e(r["test"])}</td><td class="l">{_e(r["config"])}</td>'
        f'<td class="l">{_e(r["tech"])}</td><td class="l">{_e(r["flow"])}'
        f'{" · refuted netlist" if r.get("netlist_refuted") else ""}</td>'
        f'{ratios(r["ratios"])}</tr>' for r in doc["pairs"]
    )
    notes = " ".join(_e(note) for note in doc.get("notes", []))
    coverage = "".join(
        f'<tr><td class="l">{_e(r["flow"])}</td><td class="l">{_e(r["tech"])}</td>'
        f'<td>{r["before_ok"]}</td><td>{r["after_ok"]}</td>'
        f'<td>{r["new_failures"]}</td><td>{r["recovered"]}</td></tr>'
        for r in doc.get("coverage", [])
    )
    return (
        f'<section id="{_e(name)}-comparison"><h2>{_e(doc.get("title", "SAT optimization · preserved-report comparison"))}</h2>'
        f'<p class="sub">Evaluation {_e(doc["date"])} against '
        f'<a href="{_e(doc["baseline_html"])}">{_e(doc["baseline_html"])}</a>. '
        f'Ratios are {_e(doc.get("ratio_label", "pre-SAT / measured"))}; higher is better. Geomeans use matched successful '
        'rows on the same host and unchanged Liberty files. Headline rows require idiomatic '
        'Pyrope and a proven language-equivalence claim.</p>'
        '<div class="scroll"><table><thead><tr><th class="l">Tech</th>'
        '<th class="l">Flow</th><th class="l">Population</th><th>Pairs</th>'
        f'{headers}</tr></thead><tbody>{summary}</tbody></table></div>'
        f'<p class="sub">{notes}</p>'
        '<details><summary>Coverage and every matched measurement</summary>'
        '<div class="scroll"><table><thead><tr><th class="l">Flow</th>'
        '<th class="l">Tech</th><th>Before OK</th><th>After OK</th>'
        '<th>New failures</th><th>Recovered</th></tr></thead>'
        f'<tbody>{coverage}</tbody></table></div>'
        '<div class="scroll"><table><thead><tr><th class="l">Test</th>'
        '<th class="l">Config</th><th class="l">Tech</th><th class="l">Flow</th>'
        f'{headers}</tr></thead><tbody>{detail}</tbody></table></div></details></section>'
    )


def write_report(
    root: Path, out: Path | None = None, cfg: dict | None = None, host: str | None = None,
    synthesis_run: str | None = None, comparison_name: str | None = None,
    prefer_evaluation: bool = True,
    kind: str | None = None,
) -> Path:
    cfg = cfg or {}
    host = host or host_name()
    if kind is not None and kind not in RESULT_KINDS:
        raise ValueError(f"unknown result kind: {kind}")
    if (kind is None and out is None and synthesis_run is None
            and comparison_name is None and prefer_evaluation):
        return write_results(root, cfg=cfg, host=host)[0]
    evaluation = root / "data" / f"verilog-eval-{slug(host)}.json"
    if (prefer_evaluation and evaluation.exists() and kind != "sim"
            and synthesis_run is None and comparison_name is None):
        from .verilog_eval import write_evaluation

        return write_evaluation(root, evaluation, out=out, kind=kind)
    rcfg = cfg.get("report", {})
    base_syn = rcfg.get("baseline_flow", "syn_yosys_abc")
    base_sim = rcfg.get("baseline_sim_flow", "sim_verilator")
    headline = set(rcfg.get("headline_pyrope_status", ["idiomatic"]))

    # Scoped to ONE machine (`uname -n`). Wall clock and peak RSS are the
    # majority of what is reported here and they are not portable, so a table
    # mixing hosts would be a table of hardware differences.
    # A focused rerun overlays its corrected slots on the most recent full
    # matrix.  Showing only the newest run would turn a one-test rerun into a
    # one-test report and make all unaffected measurements disappear.
    ledger = Ledger(root)
    rows = ledger.latest_rows(host)
    # Formal gating is corpus policy, not a property of an old measurement.
    # Applying the current manifest policy at render time lets a CDC result
    # remain visible and timed without forcing a ten-minute prover rerun merely
    # to clear a status bit in an append-only ledger.
    informational = {test.name for test in discover(root) if not test.lec_gate}
    if informational:
        normalized = []
        for original in rows:
            row = dict(original)
            if row.get("kind") == "lec" and row.get("test") in informational:
                prior_status = row.get("status")
                prior_note = row.get("note", "")
                row["status"] = "ok"
                row["passed"] = True
                row["comparable"] = False
                row["lec_gate"] = False
                if prior_status == "failed" and prior_note:
                    row["note"] = f"informational formal result: {prior_note}"
            normalized.append(row)
        rows = normalized
    if synthesis_run is not None:
        snapshot = [r for r in ledger.load(host) if r.get("run_id") == synthesis_run]
        if not any(r.get("kind") == "synth" for r in snapshot):
            raise ValueError(f"no synthesis rows for {host}/{synthesis_run}")
        # A named snapshot is reproducible even when a later focused run
        # overlays the ordinary host page. Keep the append-only ledger intact.
        # A profile rerun may measure only the LHD flows. Keep the existing
        # baseline flow while replacing every measured flow with its snapshot.
        snapshot_flows = {r["flow"] for r in snapshot}
        rows = [r for r in rows if r.get("flow") not in snapshot_flows] + snapshot
        rows = _gate_snapshot_lec(rows, synthesis_run)
    if kind is not None:
        rows = [r for r in rows if r.get("kind") == kind]
    suffix = f"results-{RESULT_KINDS[kind][0]}" if kind else "report"
    out = out or root / TARGET_DIR / f"{suffix}-{slug(host)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    page_name = f"{RESULT_KINDS[kind][1]} · {host}" if kind else "lhdtrack"

    if not rows:
        out.write_text(
            _page(
                f"lhdtrack — {page_name}",
                f"<h1>{_e(page_name)}</h1>{_results_nav(host, kind)}"
                f"<p class='sub'>No runs recorded on "
                f"<b>{_e(host)}</b> yet. Run <code>make run</code>.</p>",
            )
        )
        return out

    ident = max(rows, key=lambda row: row.get("run_id", ""))
    body = [_results_nav(host, kind), _header(ident, rows, page_name)]
    if kind in (None, "synth") and (synthesis_run is None or comparison_name is not None):
        body.append(_satopt_comparison(root, host, comparison_name or "satopt"))
    if synthesis_run is not None:
        body.append(f'<p class="sub" data-synthesis-run="{_e(synthesis_run)}">'
                    f'Synthesis snapshot: <code>{_e(synthesis_run)}</code>.</p>')

    # index: (test, config, tech) -> flow -> row
    syn: dict[tuple, dict[str, dict]] = defaultdict(dict)
    sim: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        key = (r["suite"], r["test"], r.get("config", "default"), r.get("tech"))
        if r.get("kind") == "synth":
            syn[key][r["flow"]] = r
        elif r.get("kind") == "sim":
            if r.get("flow", "").startswith("sim_retained_usyn_netlist"):
                continue
            sim[(r["suite"], r["test"], r.get("config", "default"), None)][r["flow"]] = r
    # ORDER: synthesis, then STA accuracy, then simulation. Synthesis and
    # simulation are the results; STA accuracy sits between them because it is a
    # DIAGNOSTIC -- nobody is trying to improve it, but a timer drifting from the
    # reference is how you learn a synthesis number above it cannot be trusted.
    #
    # ONE table per technology, every test in it, one geomean at the bottom.
    # `suite` is a grouping field in the manifest, not a reason to split the
    # page -- these are just tests, and splitting them fragments the only
    # number most readers want.
    for tech in sorted({k[3] for k in syn if k[3]}):
        keys = sorted((k for k in syn if k[3] == tech), key=lambda k: (k[1], k[2]))
        unit = next(
            (r.get("sta", {}).get("time_unit") for k in keys for r in syn[k].values()
             if r.get("sta", {}).get("time_unit")),
            "ns",
        )
        # The chart leads, the table backs it up. A reader wants the shape
        # first -- who is ahead, on what, by how much -- and the numbers only
        # once something looks worth chasing.
        body.append(f"<h2>Synthesis · {_e(tech)}</h2>")
        if not any("syn_lhd_verilog_usyn" in syn[k] for k in keys):
            body.append('<p class="sub">USYN has not been measured on this host and technology; '
                        'its columns are unmeasured. Results from other hosts are excluded.</p>')
        body.append(_synth_policy_note([r for k in keys for r in syn[k].values()]))
        data = _chart_data(
            keys, syn, base_syn, list(_SYN_FLOWS[1:]),
            [
                ("delay", "Delay", unit, lambda r: r.get("sta", {}).get("opensta_ns")),
                ("area", "Area", "µm²", lambda r: r.get("qor", {}).get("area_um2")),
                ("cells", "Cells", "", lambda r: r.get("qor", {}).get("cells")),
                ("depth", "Logic depth", "cells", lambda r: r.get("qor", {}).get("logic_depth")),
                ("time", "Tool runtime", "s",
                 lambda r: r.get("time_ms", {}).get("total")),
                ("mem", "Peak memory", "MB",
                 lambda r: r.get("peak_rss_kb", {}).get("max")),
            ],
        )
        if data:
            body.append(_chart_block(f"chart-syn-{slug(tech)}", data))
        else:
            # An absent chart is explained, not left as a gap. A reader who
            # sees one technology plotted and another not should be told which
            # flow is missing rather than left to infer it from the table.
            body.append(
                "<p class='sub'>No comparison chart: no LiveHD flow produced a "
                f"result for <b>{_e(tech)}</b>, so there is nothing to plot "
                "against the baseline. See the table and the notes below.</p>"
            )
        body.append(_synth_table(None, keys, syn, base_syn, headline, unit))

    if kind in (None, "synth"):
        body.append(_sta_section(rows, cfg))

    simulation_start = len(body)
    if sim:
        keys = sorted(sim, key=lambda k: (k[1], k[2]))
        body.append("<h2>Simulation</h2>")
        body.append('<p class="sub">Latest recorded measurements for Verilator, LiveHD Verilog '
                    'and LiveHD Pyrope. Regenerating this report does not remeasure changed '
                    'sources. Verilator execution targets 0.5–2 seconds per test. Execution '
                    'speed is separate from host C++ compilation and '
                    'front-end setup; checksum failures remain failed rows.</p>')
        workers = Counter(r.get("sim", {}).get("measurement_jobs")
                          for k in keys for r in sim[k].values()
                          if r.get("sim", {}).get("measurement_jobs"))
        if workers:
            phases = "; ".join(f"{n} workers ({count} measurements)"
                               for n, count in sorted(workers.items()))
            body.append(f'<p class="sub">Measurement concurrency: {phases}. '
                        'Slop/LLVM pairs must share the same outer concurrency setting.</p>')
        builds = Counter(r.get("sim", {}).get("build_jobs")
                         for k in keys for r in sim[k].values()
                         if r.get("sim", {}).get("build_jobs"))
        if builds:
            limits = "; ".join(f"{n} build jobs per flow ({count} measurements)"
                               for n, count in sorted(builds.items()))
            body.append(f'<p class="sub">Build parallelism: {limits}. '
                        'C++ compilation and LLVM native-object lowering share the same '
                        'worker limit. Setup/code generation remains sequential; final '
                        'linking follows object compilation. Backend pairs require '
                        'matching build limits.</p>')
        data = _chart_data(
            keys, sim, base_sim, list(_SIM_FLOWS[1:]),
            [
                ("exec", "Simulation speed", "", lambda r: r.get("sim", {}).get("exec_ms")),
                ("cc", "Host C++ compile", "s", lambda r: r.get("time_ms", {}).get("cc")),
                ("setup", "Front end", "s", lambda r: r.get("time_ms", {}).get("setup")),
                ("prepare", "Setup + compile", "s", lambda r: _sim_cost_ms(r, "prepare")),
                ("total", "Total incl. one simulation", "s", lambda r: _sim_cost_ms(r, "total")),
                ("mem", "Peak memory", "MB",
                 lambda r: r.get("peak_rss_kb", {}).get("max")),
            ],
        )
        if data:
            body.append(_chart_block("chart-sim", data))
        body.append(_sim_table(None, keys, sim, base_sim, headline))
        llvm_data = _llvm_chart_data(keys, sim)
        body.append('<h2>LLVM relative to Slop</h2>'
                    '<p class="sub">Same source language, cycles, checksum, host and LiveHD build '
                    'in the same run. Slop execution time ÷ LLVM execution time: above 1× '
                    'means LLVM is faster. Failed and unmatched runs are excluded; all '
                    'Pyrope source styles are eligible for this backend comparison.</p>'
                    '<p class="sub">Setup + compile combines code generation, LLVM kernel-object '
                    'creation, host compilation and linking. Total adds one simulation and '
                    'the separate Verilog elaboration step. Compile time is estimated as '
                    'run-only wall time minus the best standalone execution; these are '
                    'measured cold-flow costs, not isolated compiler timings.</p>')
        if llvm_data:
            for language in ("verilog", "pyrope"):
                gains = [g["values"]["exec"][language] for g in llvm_data["groups"]
                         if language in g["values"]["exec"]]
                if gains:
                    faster = sum(gain > 1.05 for gain in gains)
                    slower = sum(gain < 0.95 for gain in gains)
                    similar = len(gains) - faster - slower
                    body.append(f'<p class="sub"><b>{language.title()}</b>: '
                                f'LLVM speed / Slop geomean {_ratio(gains)} '
                                f'over {len(gains)} pairs; '
                                f'LLVM is more than 5% faster on {faster}, more than 5% slower '
                                f'on {slower}, and within 5% on {similar}.</p>')
            body.append(_llvm_timing_summary(llvm_data))
            body.append(_chart_block("chart-sim-llvm", llvm_data))
        else:
            body.append('<p class="sub">No matching successful Slop/LLVM measurements yet.</p>')

    if kind is None and sim and rcfg.get("simulation_first", False):
        simulation = body[simulation_start:]
        del body[simulation_start:]
        body[2:2] = simulation

    body.append(_language_lec_section(rows))
    body.append(_netlist_lec_section(rows))

    if kind == "sim" and evaluation.exists():
        from .verilog_eval import retained_usyn_validation_section

        spec = json.loads(evaluation.read_text())
        body.append(retained_usyn_validation_section(ledger.load(host), spec, kind="sim"))

    body.append(_problems(rows))
    title = RESULT_KINDS[kind][1] if kind else "QoR report"
    out.write_text(_page(f"lhdtrack — {title} — {host}", "\n".join(body)))
    return out


def _header(ident: dict, rows: list[dict], title: str = "lhdtrack") -> str:
    versions = ident.get("versions", {})
    chips = [
        f"<span class='chip'>{_e(k)} {_e(v)}</span>"
        for k, v in sorted(versions.items())
        if v and v != "missing"
    ]
    cached = sum(1 for r in rows if r.get("cached"))
    failed = sum(1 for r in rows if r.get("status") == "failed")
    skipped = sum(1 for r in rows if r.get("status") == "skipped")
    return f"""
<h1>{_e(title)}</h1>
<p class="sub"><b>{_e(ident.get('host'))}</b> ({_e(ident.get('host_class'))}) ·
{_e(ident.get('date'))} · run <code>{_e(ident.get('run_id'))}</code> ·
{len(rows)} rows, {cached} reused from cache,
<span class="{'bad' if failed else 'muted'}">{failed} failed</span>,
<span class="muted">{skipped} skipped</span></p>
<div class="meta">{''.join(chips)}</div>
"""


def _sta_section(rows: list[dict], cfg: dict) -> str:
    """OpenSTA vs LiveHD's own OpenTimer, on the SAME netlist and Liberty.

    A DIAGNOSTIC, not a result -- which is why it sits below the synthesis
    tables rather than above them. Nobody is trying to improve this number. It
    is here because a timer drifting from the reference is how you find out
    that a delay reported above it cannot be trusted, and because a systematic
    drift means every timing-driven decision LiveHD made was made on the wrong
    number.

    A histogram rather than a column, because the shape is the finding: if
    LiveHD's timer is optimistic that shows up as a distribution sitting left
    of zero, not as one bad row.

    Only LiveHD flows appear: the yosys netlist has no LiveHD timing to check
    against, so there is nothing to correlate there.
    """
    limit = float(cfg.get("gates", {}).get("sta_delta_pct_max", 10.0))
    items = [
        r for r in rows
        if r.get("sta", {}).get("delta_pct") is not None
        and r.get("sta", {}).get("opensta_ns")
    ]
    if not items:
        notes = {r["sta"][key] for r in rows for key in ("opensta_note", "delta_note")
                 if r.get("sta", {}).get(key)}
        missing = {
            r["flow"] for r in rows
            if r.get("kind") == "synth" and "lhd" in r["flow"]
            and r.get("sta", {}).get("opentimer_ns") is None
        }
        detail = "; ".join(f"<b>{_e(n)}</b>" for n in sorted(notes)) or "no paired timings"
        extra = (
            f" No OpenTimer result from {_e(', '.join(sorted(missing)))}."
            if missing else ""
        )
        # An ABSENT correlation is reported, never left blank: a missing
        # section reads like the two timers agreed.
        return (
            "<h2>STA accuracy — LiveHD OpenTimer vs OpenSTA</h2>"
            f"<p class='sub'>Not available this run. {detail}.{extra} "
            "Accuracy requires matching timing metrics and constraints on the same netlist. "
            "The synthesis delay comparisons above use OpenSTA for every producer.</p>"
        )

    # Signed error: positive means LiveHD reports a LONGER path than OpenSTA
    # (pessimistic, safe); negative means SHORTER (optimistic, dangerous).
    # Folding them into one absolute number would hide exactly that difference.
    for r in items:
        ot, st = r["sta"]["opentimer_ns"], r["sta"]["opensta_ns"]
        r["_signed"] = (ot - st) / st * 100.0

    signed = sorted(r["_signed"] for r in items)
    median = signed[len(signed) // 2]
    optimistic = sum(1 for v in signed if v < -limit)

    rowsl = "".join(
        f'<tr><td class="l">{_e(r["test"])}</td>'
        f'<td class="l muted">{_e(r.get("config"))}</td>'
        f'<td class="l muted">{_e(r.get("tech"))}</td>'
        f'<td class="l muted">{_e(r["flow"].replace("syn_", ""))}</td>'
        f'<td>{_fmt(r["sta"]["opentimer_ns"], 4)}</td>'
        f'<td>{_fmt(r["sta"]["opensta_ns"], 4)}</td>'
        f'<td class="{_delta_cls(r["_signed"], limit)}">{r["_signed"]:+.2f}%</td>'
        f'<td>{_fmt(r["sta"].get("wns_ns"), 4)}</td></tr>'
        for r in sorted(items, key=lambda r: r["_signed"])
    )

    return f"""
<h2>STA accuracy — LiveHD OpenTimer vs OpenSTA</h2>
<p class="sub">A diagnostic on the tables above, not a result in itself: this is
how a delay that cannot be trusted gets found. Same netlist, same Liberty, same
SDC — only the timer differs.
Median error <b>{median:+.2f}%</b> over {len(items)} paired timings;
<b>{optimistic}</b> more than {limit}% <b>optimistic</b> (LiveHD reporting a
shorter path than OpenSTA, which is the dangerous direction — a design signed
off on it would miss timing). The gate fails a test past &plusmn;{limit}%.</p>
{_delta_histogram(signed, limit)}
<div class="scroll"><table>
<thead><tr><th class="l">test</th><th class="l">config</th><th class="l">tech</th>
<th class="l">flow</th><th>OpenTimer</th><th>OpenSTA</th>
<th>error</th><th>WNS</th></tr></thead>
<tbody>{rowsl}</tbody></table></div>
"""


def _delta_cls(v: float, limit: float) -> str:
    if v < -limit:
        return "bad"      # optimistic past the gate
    if abs(v) > limit:
        return "warn"     # pessimistic past the gate
    return "muted"


def _delta_histogram(values: list[float], limit: float, w=760, h=170) -> str:
    """Distribution of the signed error, with zero marked.

    A histogram rather than a single number because the shape is the finding:
    a tight cluster on zero means the timer can be trusted, a wide spread means
    it cannot, and a cluster sitting left of zero means it is systematically
    optimistic.
    """
    if not values:
        return ""
    lo, hi = min(values + [-limit]), max(values + [limit])
    pad = max((hi - lo) * 0.1, 1.0)
    lo, hi = lo - pad, hi + pad
    nbins = 24
    width = (hi - lo) / nbins
    bins = [0] * nbins
    for v in values:
        bins[min(int((v - lo) / width), nbins - 1)] += 1
    peak = max(bins) or 1

    pl, pb, pt = 40, 34, 12
    bw = (w - pl - 12) / nbins

    def x(v):
        return pl + (v - lo) / (hi - lo) * (w - pl - 12)

    bars = "".join(
        f'<rect x="{pl + i * bw:.1f}" y="{h - pb - (n / peak) * (h - pb - pt):.1f}" '
        f'width="{max(bw - 1.5, 1):.1f}" height="{(n / peak) * (h - pb - pt):.1f}" '
        f'fill="{"var(--bad)" if lo + (i + 0.5) * width < -limit else "var(--accent)"}" '
        f'opacity="0.85"><title>{n} at {lo + i * width:+.1f}%..{lo + (i + 1) * width:+.1f}%</title>'
        f"</rect>"
        for i, n in enumerate(bins) if n
    )
    marks = "".join(
        f'<line x1="{x(v):.1f}" x2="{x(v):.1f}" y1="{pt}" y2="{h - pb}" '
        f'stroke="{c}" stroke-dasharray="4 3"/>'
        f'<text x="{x(v):.1f}" y="{h - pb + 14}" text-anchor="middle" font-size="11" '
        f'fill="var(--muted)">{lbl}</text>'
        for v, c, lbl in (
            (0.0, "var(--fg)", "0%"),
            (-limit, "var(--bad)", f"-{limit:g}%"),
            (limit, "var(--warn)", f"+{limit:g}%"),
        )
        if lo <= v <= hi
    )
    return f"""<div class="scroll"><svg viewBox="0 0 {w} {h}" role="img"
aria-label="distribution of OpenTimer error against OpenSTA">
<line x1="{pl}" x2="{w - 12}" y1="{h - pb}" y2="{h - pb}" stroke="var(--line)"/>
{bars}{marks}
<text x="{pl}" y="{pt + 4}" font-size="11" fill="var(--muted)">n={len(values)}</text>
</svg></div>
<p class="legend"><span>left of 0% = LiveHD optimistic (reports a shorter path
than OpenSTA)</span><span>right = pessimistic</span></p>"""


_SYN_FLOWS = (
    "syn_yosys_abc", "syn_lhd_verilog", "syn_lhd_pyrope", "syn_lhd_verilog_usyn",
)
_SYN_LABELS = ("yosys+slang+abc", "lhd·verilog·abc", "lhd·pyrope·abc", "lhd·verilog·usyn")


def _synth_table(title, keys, index, base_flow, headline, unit="ns") -> str:
    head = ['<tr><th class="l" rowspan="2">test</th><th class="l" rowspan="2">config</th>']
    for label in _SYN_LABELS:
        head.append(f'<th class="g" colspan="6">{_e(label)}</th>')
    head.append("</tr><tr>")
    # Timing first, then area, then what the tool cost to run -- the order the
    # metrics actually matter in.
    for _ in _SYN_FLOWS:
        # The delay unit is the LIBRARY's, not a fixed "ns": sky130 Liberty is
        # 1ns and ASAP7 is 1ps, so labelling both "ns" understates ASAP7 by
        # 1000x -- 154 ps would read as 154 ns, i.e. slower than 130nm.
        head.append(
            f'<th class="g">delay {_e(unit)}</th><th>area µm²</th><th>cells</th><th>depth</th>'
            "<th>time s</th><th>mem MB</th>"
        )
    head.append("</tr>")

    body, ratios = [], defaultdict(list)
    for key in keys:
        _, test, config, _tech = key
        flows = index[key]
        base = flows.get(base_flow, {})
        cells = [
            f'<td class="l">{_e(test)} {_tags(flows)}</td>'
            f'<td class="l muted">{_e(config)}</td>'
        ]
        for flow in _SYN_FLOWS:
            r = flows.get(flow)
            # A failed STA-correlation gate still carries a complete synthesis
            # measurement (area/cells plus the independent OpenSTA delay). Do
            # not turn that useful row into six blank cells; show it with a
            # gate tag and keep the failure in the diagnostics section. A true
            # synthesis failure has no QoR/STA payload and remains a blank
            # failed cell.
            has_measurement = bool(r and (r.get("sta", {}).get("opensta_ns") is not None
                                         or any(r.get("qor", {}).get(field) is not None
                                                for field in ("area_um2", "cells", "lhd_area_um2", "lhd_cells"))))
            if not r or (r.get("status") != "ok" and not has_measurement):
                note = (r or {}).get("status", "-")
                cells.append(f'<td class="muted g" colspan="6">{_e(note)}</td>')
                continue
            q = r.get("qor", {})
            area, ncells = q.get("area_um2"), q.get("cells")
            # OpenSTA for EVERY flow: one timer, one netlist shape, one number
            # people can compare. lhd's own OpenTimer figure is what gets
            # CHECKED against it, in its own section -- never quoted here.
            delay = r.get("sta", {}).get("opensta_ns")
            secs = r.get("time_ms", {}).get("total", 0) / 1000
            mem = r.get("peak_rss_kb", {}).get("max", 0) / 1024
            stale = ' <span class="tag">cached</span>' if r.get("cached") else ""
            norm = ' <span class="tag">norm</span>' if q.get("normalized") else ""
            gate = ' <span class="tag">STA gate</span>' if r.get("status") == "failed" else ""
            if r.get("measured_lec_verified") is False:
                gate += ' <span class="tag" title="No unbounded proof for this run; excluded from averages">LEC unverified</span>'
            display, scopes = _synth_display_values(r)
            digits = (2 if unit != "ns" else 3, 2, 0, 0, 1, 0)
            cells.extend(
                f'<td{' class="g"' if i == 0 else ""}>{_fmt(value, precision)}'
                f'{_synth_scope_tag(scope)}{norm + stale + gate if i == 1 else ""}</td>'
                for i, (value, precision, scope) in enumerate(zip(display, digits, scopes)))
            # Only comparable rows feed the geomean. The Pyrope flow is the one
            # that has to earn its place: an `auto` seed or an unproven pair is
            # shown in the table and left out of the aggregate.
            if _eligible(r, flow, base, headline, "syn_lhd_pyrope"):
                # Every flow is now measured by the SAME counter (yosys stat
                # over a structural netlist) and the SAME timer (OpenSTA), so
                # all four of these are honest ratios.
                for metric, val, b in (
                    ("delay", delay, base.get("sta", {}).get("opensta_ns")),
                    ("area", area, base.get("qor", {}).get("area_um2")),
                    ("depth", q.get("logic_depth"), base.get("qor", {}).get("logic_depth")),
                    ("time", secs, base.get("time_ms", {}).get("total", 0) / 1000),
                    ("mem", mem, base.get("peak_rss_kb", {}).get("max", 0) / 1024),
                ):
                    gain = _gain(val, b)
                    if gain:
                        ratios[(flow, metric)].append(gain)
        body.append("<tr>" + "".join(cells) + "</tr>")

    foot = ['<tr><td class="l" colspan="2">geomean vs yosys+slang+abc</td>']
    for flow in _SYN_FLOWS:
        foot.append('<td class="g">' + _ratio(ratios[(flow, "delay")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "area")]) + "</td>")
        foot.append("<td class=\'muted\'>-</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "depth")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "time")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "mem")]) + "</td>")
    foot.append("</tr>")

    note = (
        "Every ratio is <b>baseline &divide; measured</b>, so <b>higher is always "
        "better</b>. Depth counts combinational cell levels (buffers included), with "
        "flop/latch outputs as sources. 2.00&times; means twice as fast, or half the area, or half the "
        "memory. Named snapshots require unbounded netlist LEC from the same run; "
        "Pyrope also requires a language-equivalence proof and hand-written source. "
        "<span class=\'tag\'>norm</span> marks a netlist whose behavioural registers "
                "yosys mapped, so it could be counted and timed exactly like the others. "
                "<span class=\'tag\'>STA gate</span> keeps a complete synthesis measurement "
                "visible when LiveHD OpenTimer disagrees with OpenSTA; it is excluded from "
                "the geomean and detailed in STA accuracy below."
    )

    heading = f"<h2>{_e(title)}</h2>" if title else ""
    return f"""{heading}
<div class="scroll"><table><thead>{''.join(head)}</thead>
<tbody>{''.join(body)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>
<p class="sub muted">{note}</p>
"""


_SIM_FLOWS = ("sim_verilator", "sim_lhd_verilog", "sim_lhd_pyrope")
_SIM_LABELS = ("verilator", "lhd·verilog", "lhd·pyrope")


def _sim_cost_ms(row: dict, metric: str) -> float | None:
    """Combine stages without treating unrecorded build times as zero."""
    execution = row.get("sim", {}).get("exec_ms")
    if metric == "exec":
        values = [execution]
    elif metric in ("prepare", "total"):
        stages = row.get("time_ms", {})
        values = [stages.get("setup"), stages.get("cc")]
        if metric == "total":
            values.extend((stages.get("elab", 0), execution))
    else:
        raise ValueError(f"unknown simulation cost: {metric}")
    if not all(isinstance(v, (float, int)) and math.isfinite(v) and v >= 0 for v in values):
        return None
    return sum(values)


def _llvm_gain(llvm: dict, slop: dict, metric: str = "exec") -> float | None:
    if (llvm.get("status") != "ok" or slop.get("status") != "ok"
            or slop.get("sim", {}).get("backend") != "slop"
            or llvm.get("sim", {}).get("backend") != "llvm"
            or not _same_netlist_measurement(llvm, slop)):
        return None
    for field in ("host", "host_class", "run_id"):
        if llvm.get(field) != slop.get(field):
            return None
    if llvm.get("versions", {}).get("lhd") != slop.get("versions", {}).get("lhd"):
        return None
    a, b = llvm.get("sim", {}), slop.get("sim", {})
    if a.get("measurement_jobs") != b.get("measurement_jobs"):
        return None
    if a.get("build_jobs") != b.get("build_jobs"):
        return None
    if (not a.get("cycles") or a.get("cycles") != b.get("cycles")
            or a.get("checksum") is None or a.get("checksum") != b.get("checksum")):
        return None
    execution = (_sim_cost_ms(llvm, "exec"), _sim_cost_ms(slop, "exec"))
    if not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0 for v in execution):
        return None
    measured, base = _sim_cost_ms(llvm, metric), _sim_cost_ms(slop, metric)
    if not all(isinstance(v, (float, int)) and math.isfinite(v) and v > 0
               for v in (measured, base)):
        return None
    return base / measured


def _same_netlist_measurement(row: dict, base: dict) -> bool:
    """Retained-netlist timings must describe the same artifact and run."""
    a, b = row.get("sim", {}), base.get("sim", {})
    if not (a.get("source_run") or b.get("source_run")):
        return True
    for field in ("source_run", "netlist_sha256", "liberty_sha256", "cycles", "checksum",
                  "build_jobs", "measurement_jobs", "exec_repetitions"):
        if a.get(field) is None or a.get(field) != b.get(field):
            return False
    return (not a.get("validation_only") and not b.get("validation_only")
            and bool(row.get("versions", {}).get("lhd"))
            and bool(row.get("versions", {}).get("cxx"))
            and all(row.get(field) and row.get(field) == base.get(field)
                    for field in ("run_id", "host", "host_class", "versions")))


def _llvm_chart_data(keys, index) -> dict | None:
    groups = []
    for key in keys:
        values, times = {}, {}
        for metric in ("exec", "prepare", "total"):
            gains, costs = {}, {}
            for language in ("verilog", "pyrope"):
                source = f"sim_lhd_{language}"
                llvm, slop = index[key].get(source + "_llvm", {}), index[key].get(source, {})
                gain = _llvm_gain(llvm, slop, metric)
                if gain is not None:
                    gains[language] = gain
                    costs[language] = {"baseline_ms": _sim_cost_ms(slop, metric),
                                       "measured_ms": _sim_cost_ms(llvm, metric)}
            values[metric], times[metric] = gains, costs
        if any(values.values()):
            groups.append({"test": key[1], "config": key[2], "solid": True,
                           "values": values, "times": times})
    if not groups:
        return None
    return {"baseline": "Slop for the same source language",
            "flows": [{"key": "verilog", "label": "Verilog LLVM / Slop", "series": "a"},
                      {"key": "pyrope", "label": "Pyrope LLVM / Slop", "series": "b"}],
            "metrics": [{"key": "exec", "label": "LLVM simulation speed", "unit": ""},
                        {"key": "prepare", "label": "Setup + compile", "unit": ""},
                        {"key": "total", "label": "Total incl. one simulation", "unit": ""}],
            "sortFlow": "verilog", "sortLabel": "Verilog LLVM / Slop",
            "solidNote": "matched successful checksums; same run and compiler build",
            "groups": groups}


def _llvm_timing_summary(data: dict) -> str:
    rows = []
    for language in ("verilog", "pyrope"):
        for metric in data["metrics"]:
            key = metric["key"]
            pairs = [group["times"][key][language] for group in data["groups"]
                     if language in group["times"][key]]
            if not pairs:
                continue
            gains = [pair["baseline_ms"] / pair["measured_ms"] for pair in pairs]
            slop = median(pair["baseline_ms"] for pair in pairs) / 1000
            llvm = median(pair["measured_ms"] for pair in pairs) / 1000
            label = "Simulation execution" if key == "exec" else metric["label"]
            rows.append(f'<tr><td class="l">{language.title()}</td>'
                        f'<td class="l">{_e(label)}</td>'
                        f'<td>{_fmt(slop)}</td><td>{_fmt(llvm)}</td>'
                        f'<td>{_ratio(gains)}</td><td>{len(pairs)}</td></tr>')
    return ('<div class="scroll"><table><thead><tr><th class="l">Source</th>'
            '<th class="l">Metric</th><th>Slop median s</th><th>LLVM median s</th>'
            '<th>Geomean Slop time ÷ LLVM time</th><th>Pairs</th></tr></thead>'
            f'<tbody>{"".join(rows)}</tbody></table></div>'
            '<p class="sub">Above 1× favors LLVM for every metric; below 1× favors Slop. '
            'The geomean uses per-test ratios, rather than the ratio of the medians.</p>')


def _sim_table(title, keys, index, base_flow, headline, *,
               languages=("verilog", "pyrope")) -> str:
    flows_to_show = ("sim_verilator", *(f"sim_lhd_{lang}" for lang in languages))
    labels = ("Verilator", *(f"LHD {lang.title()}" for lang in languages))
    metric_columns = 4 * len(flows_to_show)
    head = ['<tr><th class="l" rowspan="2">test</th><th class="l" rowspan="2">config</th>',
            '<th rowspan="2">cycles</th>']
    for flow, label in zip(flows_to_show, labels):
        backends = {index[key].get(flow, {}).get("sim", {}).get("backend")
                    for key in keys if index[key].get(flow, {}).get("status") == "ok"}
        if backends == {"slop"}:
            label += " · Slop"
        head.append(f'<th class="g" colspan="4">{_e(label)}</th>')
    head.append(f'<th class="g" colspan="{len(languages)}">LLVM speed / Slop</th></tr><tr>')
    for _ in flows_to_show:
        head.append('<th class="g">setup s</th><th>c++ s</th><th>exec s</th><th>Mcyc/s</th>')
    head.extend(f'<th class="g">{_e(lang.title())}</th>' for lang in languages)
    head.append("</tr>")

    body, ratios = [], defaultdict(list)
    for key in keys:
        _, test, config, _ = key
        flows = index[key]
        base = flows.get(base_flow, {})
        tags = _tags(flows) if "pyrope" in languages else ""
        original_cycles = base.get("sim", {}).get("original_cycles")
        cycle_note = (f' title="Original full validation: {_e(original_cycles)} cycles"'
                      if original_cycles else "")
        cells = [f'<td class="l sim-test">{_e(test)} {tags}</td>'
                 f'<td class="l muted sim-config">{_e(config)}</td>',
                 f'<td{cycle_note}>{_fmt(base.get("sim", {}).get("cycles"))}</td>']
        for flow in flows_to_show:
            r = flows.get(flow)
            # Checksum disagreement fails the cross-simulator correctness gate,
            # but each simulator did run and its speed is still a measurement.
            # Show it with a warning while keeping it out of the geomean. A
            # process/setup failure has no sim payload and remains blank.
            has_measurement = bool(r and r.get("sim", {}).get("exec_ms") is not None)
            if not r or (r.get("status") != "ok" and not has_measurement):
                reason = _e(_problem_note(r or {}))
                status = _e((r or {}).get("status", "—"))
                cells.append(f'<td class="muted g" colspan="4" title="{reason}">{status}</td>')
                continue
            t = r.get("time_ms", {})
            s = r.get("sim", {})
            rate = s.get("cycles_per_s", 0) / 1e6
            gate = ' <span class="tag">checksum</span>' if r.get("status") == "failed" else ""
            if s.get("cycles") != base.get("sim", {}).get("cycles"):
                gate += ' <span class="tag">cycles differ</span>'
            cells.append(
                f'<td class="g">{_fmt(t.get("setup", 0)/1000, 1)}{gate}</td>'
                f'<td>{_fmt(t.get("cc", 0)/1000, 1)}</td>'
                f'<td>{_fmt(s.get("exec_ms", 0)/1000, 2)}</td>'
                f"<td>{_fmt(rate, 2)}</td>"
            )
            if _eligible(r, flow, base, headline, "sim_lhd_pyrope"):
                bs, bt = base.get("sim", {}), base.get("time_ms", {})
                for metric, val, b in (
                    ("setup", t.get("setup"), bt.get("setup")),
                    ("cc", t.get("cc"), bt.get("cc")),
                    ("exec", s.get("exec_ms"), bs.get("exec_ms")),
                ):
                    gain = _gain(val, b)
                    if gain:
                        ratios[(flow, metric)].append(gain)
        for language in languages:
            source = f"sim_lhd_{language}"
            llvm, slop = flows.get(source + "_llvm", {}), flows.get(source, {})
            gain = _llvm_gain(llvm, slop)
            if gain is not None:
                ratios[(language, "llvm")].append(gain)
                label = _ratio([gain])
                note = (f'Slop {slop["sim"]["exec_ms"] / 1000:.4f}s / '
                        f'LLVM {llvm["sim"]["exec_ms"] / 1000:.4f}s')
                for metric, cost_label in (("prepare", "setup + compile"), ("total", "total")):
                    if _llvm_gain(llvm, slop, metric) is not None:
                        note += (f'; {cost_label}: Slop {_sim_cost_ms(slop, metric) / 1000:.4f}s / '
                                 f'LLVM {_sim_cost_ms(llvm, metric) / 1000:.4f}s')
            else:
                status = llvm.get("status", "—")
                color = "bad" if status == "failed" else "muted"
                label = (f'<span class="tag {color}">{_e(status)}</span>'
                         if status in ("failed", "skipped") else '—')
                note = (_problem_note(llvm)
                        or "No matching successful Slop/LLVM pair in the same run")
            cells.append(f'<td class="{ "g" if language == "verilog" else ""}" '
                         f'title="{_e(note)}">{label}</td>')
        body.append("<tr>" + "".join(cells) + "</tr>")

    foot = ['<tr><td class="l" colspan="3">geomean vs verilator</td>']
    for flow in flows_to_show:
        foot.append('<td class="g">' + _ratio(ratios[(flow, "setup")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "cc")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "exec")]) + "</td>")
        # Mcyc/s is 1/exec by construction; a second ratio for it would just
        # restate the exec column.
        foot.append('<td class="muted">—</td>')
    foot.extend('<td class="g">—</td>' for _ in languages)
    foot.append("</tr>")
    foot.append(f'<tr><td class="l" colspan="{3 + metric_columns}">'
                'LLVM speed / Slop · all matched successful pairs</td>')
    for language in languages:
        gains = ratios[(language, "llvm")]
        foot.append(f'<td>{_ratio(gains)} <small class="muted">(n={len(gains)})</small></td>')
    foot.append('</tr>')

    heading = f"<h2>{_e(title)}</h2>" if title else ""
    return f"""{heading}
<div class="scroll"><table class="sim-table"><colgroup>
<col style="width:210px"><col style="width:140px"><col style="width:90px">
<col span="{metric_columns}"><col span="{len(languages)}" style="width:90px"></colgroup><thead>{''.join(head)}</thead>
<tbody>{''.join(body)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>
<p class="sub muted">Ratios are <b>baseline &divide; measured</b>, so
<b>higher is always better</b> — 2.00&times; means twice as fast.
Ratios require equal cycle counts. All simulators fold the recorded checksum or the test fails.
The LLVM columns show Slop execution time ÷ LLVM execution time for the same source language;
hover for absolute execution, setup+compile and total times. Above 1× means LLVM helps.</p>
"""


def _gate_snapshot_lec(rows: list[dict], run: str) -> list[dict]:
    """A named measurement earns its proof from the same run, not its manifest."""
    proofs = {(r["test"], r.get("config", "default"), r.get("tech"), r["flow"]): r
              for r in rows if r.get("run_id") == run and r.get("kind") == "lec"}

    def proven(row, field):
        block = row.get(field, {})
        return (row.get("status") == "ok" and block.get("verdict") == "proven"
                and block.get("bounded") is False)

    result = []
    for original in rows:
        row = dict(original)
        flow = row.get("flow")
        if row.get("run_id") == run and flow in ("syn_lhd_verilog", "syn_lhd_pyrope"):
            prefix = (row["test"], row.get("config", "default"))
            netlist = proofs.get((*prefix, row.get("tech"), "lec_netlist"), {})
            field = "lec_result" if flow == "syn_lhd_pyrope" else "lec_verilog_result"
            verified = proven(netlist, field)
            if any(netlist.get(f, {}).get("verdict") == "refuted"
                   for f in ("lec_result", "lec_verilog_result", "lec_aux_result")):
                verified = False
            if flow == "syn_lhd_pyrope":
                language = [proofs.get((*prefix, None, f), {}) for f in _LEC_FLOWS]
                language_proven = (any(proven(r, "lec_result") for r in language)
                                   and not any(r.get("lec_result", {}).get("verdict") == "refuted"
                                               for r in language))
                verified = verified and language_proven
                row["comparable"] = bool(row.get("comparable")) and language_proven
                row["lec"] = "proven" if language_proven else "unverified"
            row["measured_lec_verified"] = verified
        result.append(row)
    return result


def _eligible(row: dict, flow: str, base: dict, headline: set, pyrope_flow: str) -> bool:
    """May this row's ratio enter the geomean?

    Every flow needs a baseline to be a ratio of. The Pyrope flow additionally
    needs the test to be comparable at all -- hand-written Pyrope, LEC-proven --
    because a machine-emitted seed enters the same LGraph the Verilog does and
    would report a front-end delta as a language result.
    """
    # The BASELINE row is filtered the same way the measured one is. A
    # syn_yosys_abc row that the STA gate just failed still carries its `qor`
    # block, so without this check a number the runner refused to stand behind
    # became the denominator of the headline geomean.
    if row.get("measured_lec_verified") is False:
        return False
    if row.get("status") != "ok" or not base or base.get("status") != "ok":
        return False
    if flow.startswith("sim_"):
        cycles = base.get("sim", {}).get("cycles")
        if (cycles is None or row.get("sim", {}).get("cycles") != cycles
                or not _same_netlist_measurement(row, base)):
            return False
    if flow != pyrope_flow:
        return True
    return bool(row.get("comparable")) and row.get("pyrope_status", "none") in headline


def _tags(flows: dict) -> str:
    r = next(iter(flows.values()), {})
    out = []
    ps = r.get("pyrope_status", "none")
    if ps != "idiomatic":
        out.append(f'<span class="tag">pyrope:{_e(ps)}</span>')
    if r.get("lec", "none") != "proven":
        out.append(f'<span class="tag">lec:{_e(r.get("lec", "none"))}</span>')
    return "".join(out)


def _problem_note(row: dict) -> str:
    note = row.get("note") or ""
    # Old ledger entries used a literal ns suffix for every technology.
    # Correct their display without rewriting the recorded observations.
    unit = row.get("sta", {}).get("time_unit", "ns")
    if note.startswith("OpenTimer ") and unit != "ns":
        note = note.replace("ns vs OpenSTA ", f"{unit} vs OpenSTA ").replace("ns = ", f"{unit} = ")
    return note


def _problems(rows: list[dict]) -> str:
    bad = [r for r in rows if r.get("status") in ("failed", "skipped") or r.get("note")]
    if not bad:
        return ""
    body = "".join(
        f'<tr><td class="l">{_e(r["test"])}</td><td class="l muted">{_e(r["flow"])}</td>'
        f'<td class="l {"bad" if r.get("status")=="failed" else "warn"}">{_e(r.get("status"))}</td>'
        f'<td class="note">{_e(_problem_note(r))}</td></tr>'
        for r in sorted(bad, key=lambda r: (r.get("status", ""), r["test"]))
    )
    return f"""
<h2>Failures, skips and notes</h2>
<div class="scroll"><table>
<thead><tr><th class="l">test</th><th class="l">flow</th><th class="l">status</th>
<th class="l">note</th></tr></thead><tbody>{body}</tbody></table></div>
"""


_LEC_FLOWS = ("lec_lgyosys", "lec_lhd")
_LEC_LABELS = ("lgcheck (yosys)", "lhd (cvc5)")

# `proven` and `refuted` are answers; `timeout`, `inconclusive`, `unsupported`
# and `error` are the absence of one. Colouring any of those like a refutation
# would report "this design is wrong" when the honest answer is "no proof".
_VERDICT_CLASS = {
    "proven": "good",
    "refuted": "bad",
    "timeout": "warn",
    "inconclusive": "warn",
    "unsupported": "muted",
    "error": "warn",
    "none": "muted",
    "not-measured": "muted",
}


def _language_lec_section(rows: list[dict]) -> str:
    index = defaultdict(dict)
    for row in rows:
        if row.get("flow") in _LEC_FLOWS:
            key = row["suite"], row["test"], row.get("config", "default"), None
            index[key][row["flow"]] = row
    if not index:
        return ""
    keys = sorted(index, key=lambda key: (key[1], key[2]))
    paired = []
    for key in keys:
        verdicts = [_display_lec_verdict(index[key].get(flow, {}).get("lec_result", {}))
                    for flow in _LEC_FLOWS]
        if verdicts[0] in ("proven", "refuted") and verdicts[0] == verdicts[1]:
            paired.append(key)
    data = _chart_data(
        paired, index, "lec_lgyosys", ["lec_lhd"],
        [("prove", "Proof time", "s", lambda r: r.get("lec_result", {}).get("ms"))],
    )
    chart = _chart_block("chart-lec", data) if data else ""
    return '<h2>Equivalence — Pyrope vs Verilog</h2>' + chart + _lec_section(keys, index)


def _lec_section(keys, index) -> str:
    """Equivalence: is `pyrope/` the same circuit as `verilog/`?

    Two backends prove the IDENTICAL obligation -- lgcheck driving yosys, and
    lhd's in-process cvc5 -- so their times compare directly and their verdicts
    check each other. cvc5 reasons over the LGraph while lgyosys reasons over
    the cgen-emitted Verilog, so a code-generation bug surfaces as one backend
    proving what the other refutes: a discrepancy no single-engine run can find.

    This section is why any of the numbers above mean anything. Until a test is
    proven equivalent, a Pyrope area win might just be a smaller, different
    circuit.
    """
    counts: dict[str, Counter] = {f: Counter() for f in _LEC_FLOWS}
    times: dict[str, list[float]] = {f: [] for f in _LEC_FLOWS}
    splits, rows_html, speedups = [], [], []

    for key in keys:
        by_flow = index[key]
        cells = [
            f'<td class="l">{_e(key[1])}</td><td class="l muted">{_e(key[2])}</td>'
        ]
        verdicts = {}
        for flow in _LEC_FLOWS:
            r = by_flow.get(flow)
            block = (r or {}).get("lec_result", {})
            verdict = _display_lec_verdict(block) or (r or {}).get("status", "—")
            counts[flow][verdict] += 1
            verdicts[flow] = verdict
            # A MEASURED ZERO IS NOT AN ABSENCE. `if secs` printed the dash for
            # `ms == 0` as well as for a missing block, so the fastest proof on
            # the page rendered as "no result" -- and now that the column sorts,
            # it would rank below every slower one instead of first.
            ms = block.get("ms")
            if ms:
                times[flow].append(ms / 1000)
            cells.append(
                f'<td class="l g {_VERDICT_CLASS.get(verdict, "muted")}">{_e(verdict)}</td>'
                f"<td>{_fmt(ms / 1000, 2) if ms is not None else '—'}</td>"
            )
        # Compare definitive answers with the same scope. A bounded proof is
        # less work than an unbounded proof, even though both store `proven`.
        a = (by_flow.get("lec_lgyosys") or {}).get("lec_result", {})
        b = (by_flow.get("lec_lhd") or {}).get("lec_result", {})
        gain = None
        same_answer = (
            verdicts["lec_lgyosys"] in ("proven", "refuted")
            and verdicts["lec_lgyosys"] == verdicts["lec_lhd"]
        )
        if a.get("ms") and b.get("ms") and same_answer:
            gain = a["ms"] / b["ms"]
            speedups.append(gain)
        cells.append(f"<td>{_ratio([gain]) if gain else '<span class=\'muted\'>—</span>'}</td>")

        real = {v for v in verdicts.values() if v in ("proven", "refuted")}
        if len(real) > 1:
            splits.append((key[1], verdicts))
            cells.append('<td class="l bad">backends disagree</td>')
        else:
            cells.append('<td class="l muted"></td>')
        rows_html.append("<tr>" + "".join(cells) + "</tr>")

    summary = " · ".join(
        f"<b>{_e(label)}</b>: "
        + ", ".join(f"{n} {_e(v)}" for v, n in counts[flow].most_common())
        for flow, label in zip(_LEC_FLOWS, _LEC_LABELS)
    )
    geo = _geomean(speedups)
    speed = (
        f" lhd is <b>{geo:.2f}×</b> the speed of lgcheck over the "
        f"{len(speedups)} test(s) where both reached the same definitive verdict."
        if geo else ""
    )
    split_note = (
        f" <span class='bad'><b>{len(splits)} backend disagreement(s)</b></span> — "
        "one engine proved what the other refuted, which is a code-generation or "
        "encoding bug rather than a design result."
        if splits else ""
    )

    head = (
        '<tr><th class="l" rowspan="2">test</th><th class="l" rowspan="2">config</th>'
        + "".join(f'<th class="g" colspan="2">{_e(l)}</th>' for l in _LEC_LABELS)
        + '<th rowspan="2">lhd speedup</th><th class="l" rowspan="2"></th></tr><tr>'
        + '<th class="l g">verdict</th><th>s</th>' * len(_LEC_FLOWS)
        + "</tr>"
    )
    # The same geomean footer the synthesis tables carry. The `s` cells are the
    # geomean of the ABSOLUTE proof times, not a ratio: the two backends already
    # have their ratio in `lhd speedup`, so repeating it under `s` would say
    # nothing new, while a typical proof time is the number a reader wants when
    # deciding whether to run the engine at all. A geomean over seconds is the
    # right average here too -- proof times span four orders of magnitude, and an
    # arithmetic mean would just report the slowest test.
    foot = ['<tr><td class="l" colspan="2">geomean</td>']
    for flow in _LEC_FLOWS:
        foot.append(
            f'<td class="l g muted">{counts[flow].get("proven", 0)}/{len(keys)} proven</td>'
            f"<td>{_fmt(_geomean(times[flow]), 2)}</td>"
        )
    foot.append("<td>" + _ratio(speedups) + "</td>")
    foot.append('<td class="l muted"></td></tr>')
    coverage = _coverage_plot([
        (label, Counter(_coverage_verdict(
            (index[key].get(flow) or {}).get("lec_result", {}),
            (index[key].get(flow) or {}).get("status", "not-measured")) for key in keys))
        for flow, label in zip(_LEC_FLOWS, _LEC_LABELS)
    ])
    return f"""
<p class="sub">Both backends prove the same obligation, so the times compare and
the verdicts check each other. {summary}.{speed}{split_note}</p>
{coverage}
<div class="scroll"><table><thead>{head}</thead>
<tbody>{''.join(rows_html)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>
<p class="sub muted">A verdict is not boolean: <span class="good">proven</span> and
<span class="bad">refuted</span> are answers; <span class="warn">timeout</span>,
<span class="warn">inconclusive</span>, <span class="muted">unsupported</span> and
<span class="warn">error</span> are the
absence of one. Until a test is proven, its QoR numbers above are reported but
never aggregated — a Pyrope win might be a different circuit. Bounded checks
and undecided results are excluded from the speedup comparison.</p>
"""


def _netlist_lec_section(rows: list[dict]) -> str:
    """Report both source obligations and two Verilog proof backends."""
    items = [
        r for r in rows
        if r.get("flow") == "lec_netlist" and r.get("lec_result")
    ]
    if not items:
        return ""

    counts_lhd = Counter(_display_lec_verdict(r["lec_result"]) for r in items)
    counts_yosys = Counter(
        _display_lec_verdict(r.get("lec_aux_result", {})) or "not-measured" for r in items
    )
    counts_verilog_cvc5 = Counter(
        _display_lec_verdict(r.get("lec_verilog_result", {})) or "not-measured" for r in items
    )
    checker_conflicts = [
        r for r in items
        if _display_lec_verdict(r.get("lec_aux_result", {})) == "refuted"
        and _display_lec_verdict(r.get("lec_verilog_result", {})) == "proven"
    ]
    # A Yosys-only refutation is not evidence that synthesis changed the
    # circuit when cvc5 proves the identical Verilog-vs-netlist obligation.
    # Keep both raw verdicts in the table, but report that row as a checker
    # disagreement rather than a confirmed netlist failure.  This occurs when
    # lgcheck's bounded miter gives independently initialized, resetless state
    # to the two sides; cvc5 can instead pair the corresponding machine state.
    refuted = [
        r for r in items
        if _display_lec_verdict(r["lec_result"]) == "refuted"
        or _display_lec_verdict(r.get("lec_verilog_result", {})) == "refuted"
        or (
            _display_lec_verdict(r.get("lec_aux_result", {})) == "refuted"
            and _display_lec_verdict(r.get("lec_verilog_result", {})) != "proven"
        )
    ]
    skipped = sum(
        1 for r in rows
        if r.get("flow") == "lec_netlist" and r["status"] == "skipped"
    )

    body_rows = []
    secs: dict[str, list[float]] = {"py": [], "vr": [], "vc": []}
    # PAIRED, not zipped: the two Verilog-vs-netlist engines skip different rows
    # (one times out where the other answers), so pairing by list position would
    # divide one design's seconds by another's.
    engine_pairs: list[float] = []
    for r in sorted(items, key=lambda r: (r["test"], r.get("tech") or "")):
        py = r["lec_result"]
        vr = r.get("lec_aux_result", {})
        vc = r.get("lec_verilog_result", {})
        py_verdict = _display_lec_verdict(py) or "error"
        vr_verdict = _display_lec_verdict(vr) or "not-measured"
        vr_ms = vr.get("ms")
        vc_verdict = _display_lec_verdict(vc) or "not-measured"
        vc_ms = vc.get("ms")
        note = (
            py.get("counterexample", "")
            or vr.get("counterexample", "")
            or vc.get("counterexample", "")
            or py.get("reason", "")
            or vr.get("reason", "")
            or vc.get("reason", "")
            or r.get("note", "")
        )
        for bucket, ms in (("py", py.get("ms")), ("vr", vr_ms), ("vc", vc_ms)):
            if ms:
                secs[bucket].append(ms / 1000)
        if vr_ms and vc_ms and vr_verdict in ("proven", "refuted") and vr_verdict == vc_verdict:
            engine_pairs.append(vr_ms / vc_ms)
        if vr_verdict == "refuted" and vc_verdict == "proven":
            disagreement = (
                "checker disagreement: lgyosys refuted while cvc5 proved the "
                "same Verilog-vs-netlist obligation"
            )
            note = f"{disagreement}; {note}" if note else disagreement
        body_rows.append(
            f'<tr><td class="l">{_e(r["test"])}</td>'
            f'<td class="l muted">{_e(r.get("config"))}</td>'
            f'<td class="l muted">{_e(r.get("tech"))}</td>'
            f'<td class="l {_VERDICT_CLASS.get(py_verdict, "muted")}">'
            f'{_e(py_verdict)}</td>'
            f'<td>{_fmt(py.get("ms", 0) / 1000, 2)}</td>'
            f'<td class="l {_VERDICT_CLASS.get(vr_verdict, "muted")}">'
            f'{_e(vr_verdict)}</td>'
            f'<td>{_fmt(vr_ms / 1000, 2) if vr_ms is not None else "—"}</td>'
            f'<td class="l {_VERDICT_CLASS.get(vc_verdict, "muted")}">'
            f'{_e(vc_verdict)}</td>'
            f'<td>{_fmt(vc_ms / 1000, 2) if vc_ms is not None else "—"}</td>'
            f'<td class="note">{_e(note)}</td></tr>'
        )
    summary_lhd = ", ".join(f"{n} {_e(v)}" for v, n in counts_lhd.most_common())
    summary_yosys = ", ".join(f"{n} {_e(v)}" for v, n in counts_yosys.most_common())
    summary_verilog_cvc5 = ", ".join(
        f"{n} {_e(v)}" for v, n in counts_verilog_cvc5.most_common()
    )
    alarm = (
        f" <span class='bad'><b>{len(refuted)} netlist row(s) refuted</b></span> — "
        "synthesis or one source description changed the circuit."
        if refuted else ""
    )
    conflict_alarm = (
        f" <span class='warn'><b>{len(checker_conflicts)} checker disagreement(s)</b></span> — "
        "lgyosys found a counterexample but cvc5 proved the identical "
        "Verilog-vs-netlist obligation; this is not counted as a confirmed "
        "synthesis failure."
        if checker_conflicts else ""
    )
    # The geomean row the synthesis and simulation tables carry. Proof times span
    # orders of magnitude, so the geomean is the honest average; an arithmetic
    # one would just report the slowest design. Absolute seconds, not ratios:
    # only the two Verilog-vs-netlist columns prove the SAME obligation, and the
    # `lhd` column proves a different one (Pyrope vs netlist), so a single
    # baseline column to divide by does not exist. The one ratio that IS
    # meaningful goes in the caption instead.
    counts_by_col = (
        ("py", counts_lhd), ("vr", counts_yosys), ("vc", counts_verilog_cvc5),
    )
    foot = '<tr><td class="l" colspan="3">geomean</td>' + "".join(
        f'<td class="l muted">{c.get("proven", 0)}/{len(items)} proven</td>'
        f"<td>{_fmt(_geomean(secs[k]), 2)}</td>"
        for k, c in counts_by_col
    ) + '<td class="l muted"></td></tr>'
    engine_gain = _geomean(engine_pairs)
    engine_note = (
        f" On the {len(engine_pairs)} row(s) where both Verilog-vs-netlist engines "
        f"reached the same definitive verdict, cvc5 is <b>{engine_gain:.2f}×</b> the speed of "
        "the Yosys-backed one."
        if engine_gain else ""
    )
    return f"""
<h2>Equivalence — sources vs synthesized netlist</h2>
<p class="sub">Each source is checked against its own measured synthesis output,
including preserved native state. LiveHD checks Pyrope vs its netlist; both the
Yosys-backed engine and LiveHD/cvc5 check Verilog vs its netlist. Pyrope/cvc5: {summary_lhd}. Verilog/Yosys:
{summary_yosys}. Verilog/cvc5: {summary_verilog_cvc5}.
{f"{skipped} skipped. " if skipped else ""}{engine_note}{alarm}{conflict_alarm} A timeout exhausted the
budget; an inconclusive result returned earlier without either a proof or a
counterexample. The table keeps all three checks explicit.</p>
<div class="scroll"><table>
<thead><tr><th class="l">test</th><th class="l">config</th><th class="l">tech</th>
<th class="l">lhd: Pyrope vs netlist</th><th>s</th>
<th class="l">yosys: Verilog vs netlist</th><th>s</th>
<th class="l">cvc5: Verilog vs netlist</th><th>s</th><th class="l">note</th></tr></thead>
<tbody>{''.join(body_rows)}</tbody><tfoot>{foot}</tfoot></table></div>
"""


# ------------------------------------------------------------- gain chart ---
# The chart is DATA + one inline renderer, not SVG baked per chart in Python.
# Everything is still self-contained -- the script is embedded, nothing is
# fetched -- but the reader gets a metric switcher and real hover tooltips, and
# re-sorting per metric happens in the browser instead of producing four
# near-identical static images.
#
# One colour per FLOW, not per verdict: the reader is comparing two front ends
# and which one a bar belongs to has to be readable at a glance. Better/worse is
# carried by DIRECTION -- up is better, down is worse -- the same "higher is
# better" convention the tables use.
_FLOW_SERIES = {
    "lec_lhd": ("lhd (cvc5)", "b"),
    "syn_lhd_verilog": ("lhd·verilog", "a"),
    "syn_lhd_pyrope": ("lhd·pyrope", "b"),
    "syn_lhd_verilog_usyn": ("lhd·verilog·usyn", "c"),
    "sim_lhd_verilog": ("lhd·verilog", "a"),
    "sim_lhd_pyrope": ("lhd·pyrope", "b"),
}


def _chart_data(keys, index, base_flow, flows, metrics) -> dict | None:
    """Per-test ratios for every metric, ready for the browser to sort and draw.

    `metrics` is [(key, label, unit, getter)]. Everything is computed here so
    the page never has to know how a gain is defined; the script only sorts,
    scales and draws.
    """
    groups = []
    for key in keys:
        by_flow = index[key]
        base = by_flow.get(base_flow, {})
        if not base or base.get("status") != "ok":
            continue
        values = {}
        for mkey, _label, _unit, get in metrics:
            per_flow = {}
            for flow in flows:
                r = by_flow.get(flow)
                same_workload = (not flow.startswith("sim_") or
                                 (base.get("sim", {}).get("cycles") is not None and
                                  (r or {}).get("sim", {}).get("cycles") ==
                                  base.get("sim", {}).get("cycles")
                                  and _same_netlist_measurement(r or {}, base)))
                if r and r.get("status") == "ok" and same_workload:
                    gain = _gain(get(r), get(base))
                    if gain:
                        per_flow[flow] = round(gain, 4)
            if per_flow:
                values[mkey] = per_flow
        if not values:
            continue
        first = next((r for r in by_flow.values() if r), {})
        groups.append({
            "test": key[1],
            "config": key[2],
            # A test whose Pyrope is machine-emitted or unproven is drawn faded:
            # shown, because hiding it would misrepresent coverage, but visually
            # not part of the claim the solid bars make.
            "solid": bool(first.get("comparable")) and not any(
                r.get("measured_lec_verified") is False for r in by_flow.values()),
            "values": values,
        })
    if not groups:
        return None
    return {
        "baseline": base_flow.replace("syn_", "").replace("sim_", ""),
        "flows": [{"key": f, "label": _FLOW_SERIES.get(f, (f, "a"))[0],
                   "series": _FLOW_SERIES.get(f, (f, "a"))[1]} for f in flows],
        "metrics": [{"key": k, "label": l, "unit": u} for k, l, u, _ in metrics],
        # Sorting is by the PYROPE result: regressions left, wins right, so the
        # shape of the chart is the answer rather than something the reader has
        # to reconstruct from an alphabetical axis.
        "sortFlow": next((f for f in flows if f.endswith("pyrope")), flows[-1]),
        "groups": groups,
    }


def _chart_block(chart_id: str, data: dict) -> str:
    buttons = "".join(
        f'<button type="button" data-metric="{_e(m["key"])}"'
        f'{" class=\'on\'" if i == 0 else ""}>{_e(m["label"])}</button>'
        for i, m in enumerate(data["metrics"])
    )
    payload = json.dumps(data).replace("<", "\\u003c")
    return (
        f'<div class="chart" id="{_e(chart_id)}">'
        f'<div class="switch">{buttons}</div>'
        '<div class="switch chart-view"><button type="button" class="on" '
        'data-view="overview">Overview</button><button type="button" '
        'data-view="tests">Per test</button></div>'
        '<label class="caption">Filter tests '
        '<input type="search" class="chart-search" placeholder="Test or config" '
        'aria-label="Filter chart by test or configuration"></label>'
        f'<div class="plot"></div>'
        f'<div class="legend"></div>'
        f'<script type="application/json" class="chart-data">'
        f'{payload}</script></div>'
    )


CHART_CSS = """
.chart{margin:0 0 1.75rem}
.switch{display:flex;gap:.3rem;flex-wrap:wrap;margin:0 0 .5rem}
.switch button{font:inherit;font-size:12px;padding:.2rem .7rem;border-radius:999px;
cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--muted)}
.switch button:hover{border-color:var(--muted)}
.switch button.on{background:var(--chip);color:var(--fg);border-color:var(--muted)}
.plot{position:relative;overflow-x:auto}
.chart svg{display:block}
.chart .bar{transition:opacity .12s}
.chart .bar:hover{opacity:1!important;stroke:var(--fg);stroke-width:1}
.tip{position:absolute;pointer-events:none;opacity:0;transition:opacity .1s;
background:var(--fg);color:var(--bg);font-size:12px;padding:.3rem .5rem;
border-radius:5px;width:max-content;max-width:min(600px,calc(100% - 16px));
white-space:normal;overflow-wrap:anywhere;transform:translateY(-100%);z-index:5}
.caption{color:var(--muted);font-size:12px;margin:.4rem 0 0}
.chart-search{font:inherit;margin:0 0 .6rem .4rem;padding:.25rem .5rem;
border:1px solid var(--line);border-radius:4px;background:var(--bg);color:var(--fg)}
.sim-table{table-layout:fixed;min-width:1340px}
.sim-table td,.sim-table th{padding:.35rem;font-size:12px}
.sim-table td.sim-test,.sim-table td.sim-config{white-space:normal;overflow-wrap:anywhere}
"""

CHART_JS = r"""
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var COL = { a: 'var(--series-a)', b: 'var(--series-b)', c: 'var(--series-c)' };
  // Only ratios that mean something get a gridline: 0.5x, 1x, 2x are readable
  // landmarks, 1.37x is noise.
  var GRID = [0.1, 0.2, 0.25, 0.33, 0.5, 0.75, 1, 1.5, 2, 3, 5, 10];

  function el(tag, attrs, text) {
    var n = document.createElementNS(NS, tag);
    for (var k in attrs) n.setAttribute(k, attrs[k]);
    if (text != null) n.textContent = text;
    return n;
  }

  function draw(root, data, metricKey) {
    var plot = root.querySelector('.plot');
    plot.innerHTML = '';
    var tip = document.createElement('div');
    tip.className = 'tip';
    plot.appendChild(tip);

    var metric = data.metrics.filter(function (m) { return m.key === metricKey; })[0];
    var flows = data.flows;

    // Keep only tests that have a value for THIS metric, then sort by the
    // Pyrope ratio ascending -- worst on the left, best on the right.
    var query = root.querySelector('.chart-search').value.trim().toLowerCase();
    var groups = data.groups.filter(function (g) {
      return g.values[metricKey] && (g.test + ' ' + g.config).toLowerCase().indexOf(query) >= 0;
    });
    groups = groups.slice().sort(function (x, y) {
      function k(g) {
        var v = g.values[metricKey];
        if (v[data.sortFlow] != null) return v[data.sortFlow];
        var all = Object.keys(v).map(function (f) { return v[f]; });
        return Math.max.apply(null, all);
      }
      return k(x) - k(y);
    });
    if (!groups.length) { plot.textContent = 'No comparable results for this metric.'; return; }

    var vals = [1];
    groups.forEach(function (g) {
      var v = g.values[metricKey];
      Object.keys(v).forEach(function (f) { vals.push(v[f]); });
    });
    var lo = Math.min.apply(null, vals) / 1.35, hi = Math.max.apply(null, vals) * 1.35;
    // LOG scale about 1.00x. On a linear axis "twice as good" sits a whole unit
    // above the line while "twice as bad" sits half a unit below it, which makes
    // every regression look smaller than the equivalent win.
    var llo = Math.log(lo), lhi = Math.log(hi);

    var padL = 54, padR = 16, padT = 20, padB = 56;
    var outer = plot.clientWidth || 900;
    var overview = root.dataset.view !== 'tests';
    var W = overview ? outer : Math.max(outer, padL + padR + groups.length * 74);
    var H = 320, plotH = H - padT - padB, plotW = W - padL - padR;
    var gw = plotW / groups.length;
    var bw = Math.min(gw / (flows.length + 0.8), 44);

    var svg = el('svg', { viewBox: '0 0 ' + W + ' ' + H, width: W, height: H,
                          role: 'img', 'aria-label': metric.label + ' versus baseline per test' });
    function y(v) { return padT + (lhi - Math.log(v)) / Math.max(lhi - llo, 1e-9) * plotH; }

    GRID.forEach(function (gv) {
      if (gv < lo || gv > hi) return;
      var base = Math.abs(gv - 1) < 1e-9;
      svg.appendChild(el('line', {
        x1: padL, x2: W - padR, y1: y(gv).toFixed(1), y2: y(gv).toFixed(1),
        stroke: base ? 'var(--fg)' : 'var(--line)',
        'stroke-dasharray': base ? '' : '3 3'
      }));
      svg.appendChild(el('text', {
        x: padL - 8, y: (y(gv) + 4).toFixed(1), 'text-anchor': 'end',
        'font-size': 11, fill: 'var(--muted)'
      }, gv + '×'));
    });

    groups.forEach(function (g, gi) {
      var gx = padL + gi * gw, span = bw * flows.length, x0 = gx + (gw - span) / 2;
      flows.forEach(function (f, fi) {
        var val = g.values[metricKey][f.key];
        if (val == null) return;
        var bx = x0 + fi * bw;
        var top = Math.min(y(val), y(1)), bot = Math.max(y(val), y(1));
        var rect = el('rect', {
          x: (bx + (overview ? 0.2 : 1)).toFixed(1), y: top.toFixed(1),
          width: Math.max(bw - (overview ? 0.4 : 2), 0.5).toFixed(1),
          height: Math.max(bot - top, 1.5).toFixed(1),
          fill: COL[f.series] || 'var(--accent)', rx: 2,
          opacity: g.solid ? 0.9 : 0.4, class: 'bar'
        });
        rect.addEventListener('mousemove', function (e) {
          var r = plot.getBoundingClientRect();
          tip.style.opacity = 1;
          tip.textContent = g.test + '#' + g.config + '  ' + f.label + '  ' +
            val.toFixed(2) + '× baseline / measured' +
            (g.solid ? '' : '  (' +
              (data.solidNote || 'not LEC-proven or not hand-written Pyrope') + ')');
          var times = g.times && g.times[metricKey] && g.times[metricKey][f.key];
          if (times) {
            tip.textContent += '  baseline ' + (times.baseline_ms / 1000).toFixed(3) +
              's / measured ' + (times.measured_ms / 1000).toFixed(3) + 's';
          }
          var left = e.clientX - r.left + plot.scrollLeft - tip.offsetWidth / 2;
          left = Math.max(plot.scrollLeft + 8,
            Math.min(left, plot.scrollLeft + plot.clientWidth - tip.offsetWidth - 8));
          tip.style.left = left + 'px';
          tip.style.top = Math.max(tip.offsetHeight + 8, e.clientY - r.top - 8) + 'px';
        });
        rect.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
        svg.appendChild(rect);
        if (!overview) svg.appendChild(el('text', {
          x: (bx + bw / 2).toFixed(1),
          y: (val >= 1 ? top - 5 : bot + 12).toFixed(1),
          'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)'
        }, val.toFixed(2)));
      });
      if (!overview || gi % Math.max(1, Math.ceil(groups.length / 10)) === 0) {
        svg.appendChild(el('text', {
        x: (gx + gw / 2).toFixed(1), y: (H - padB + 19).toFixed(1),
        'text-anchor': 'middle', 'font-size': 11, fill: 'var(--fg)'
        }, overview ? String(gi + 1) : g.test));
      }
      if (!overview && g.config && g.config !== 'default') {
        svg.appendChild(el('text', {
          x: (gx + gw / 2).toFixed(1), y: (H - padB + 32).toFixed(1),
          'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)'
        }, g.config));
      }
    });
    plot.appendChild(svg);

    var cap = document.createElement('p');
    cap.className = 'caption';
    cap.textContent = metric.label + (metric.unit ? ' (' + metric.unit + ')' : '') +
      ' relative to ' + data.baseline + ' — higher is better, ' +
      'sorted worst → best by ' + (data.sortLabel || data.sortFlow) + '. ' +
      groups.length + ' of ' + data.groups.length + ' matched tests shown.' +
      (overview ? ' Horizontal axis: test rank; hover for names and values.' : '');
    plot.appendChild(cap);
  }

  function legend(root, data) {
    var box = root.querySelector('.legend');
    box.innerHTML = data.flows.map(function (f) {
      return '<span><i style="background:' + (COL[f.series] || 'var(--accent)') + '"></i>' +
        f.label + '</span>';
    }).join('');
    var note = document.createElement('span');
    note.textContent = data.solidNote || 'faded = not LEC-proven or not hand-written Pyrope';
    box.appendChild(note);
  }

  document.querySelectorAll('.chart').forEach(function (root) {
    var data = JSON.parse(root.querySelector('.chart-data').textContent);
    var current = data.metrics[0].key;
    legend(root, data);
    draw(root, data, current);
    root.querySelector('.chart-search').addEventListener('input', function () {
      draw(root, data, current);
    });
    root.querySelectorAll('button[data-metric]').forEach(function (b) {
      b.addEventListener('click', function () {
        root.querySelectorAll('button[data-metric]').forEach(function (o) {
          o.classList.remove('on');
        });
        b.classList.add('on');
        current = b.dataset.metric;
        draw(root, data, current);
      });
    });
    root.querySelectorAll('button[data-view]').forEach(function (b) {
      b.addEventListener('click', function () {
        root.dataset.view = b.dataset.view;
        root.querySelectorAll('button[data-view]').forEach(function (o) {
          o.classList.remove('on');
        });
        b.classList.add('on');
        draw(root, data, current);
      });
    });
    var t;
    window.addEventListener('resize', function () {
      clearTimeout(t);
      t = setTimeout(function () { draw(root, data, current); }, 150);
    });
  });
})();
"""


# ------------------------------------------------------------ timeseries ----
def write_timeseries(
    root: Path, out: Path | None = None, cfg: dict | None = None, host: str | None = None
) -> Path:
    """Geomean-per-flow over time for ONE machine, segmented at every tool change."""
    cfg = cfg or {}
    host = host or host_name()
    rows = Ledger(root).load(host)
    out = out or root / TARGET_DIR / f"timeseries-{slug(host)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        out.write_text(
            _page(
                "lhdtrack — history",
                f"<h1>lhdtrack history</h1><p class='sub'>No history on "
                f"<b>{_e(host)}</b> yet.</p>",
            )
        )
        return out

    base = cfg.get("report", {}).get("baseline_flow", "syn_yosys_abc")
    # run -> flow -> [ratio vs the baseline of the SAME run]
    per_run: dict[str, dict] = defaultdict(lambda: {"flows": defaultdict(list), "seg": "", "date": ""})
    by_run_key: dict[tuple, dict] = defaultdict(dict)
    for r in rows:
        if r.get("kind") != "synth" or r.get("status") != "ok":
            continue
        by_run_key[(r["run_id"], r["test"], r.get("config"), r.get("tech"))][r["flow"]] = r

    for (run_id, *_), flows in by_run_key.items():
        b = flows.get(base, {}).get("qor", {}).get("area_um2")
        if not b:
            continue
        for flow, r in flows.items():
            area = r.get("qor", {}).get("area_um2")
            if not area or not (r.get("comparable") or flow != "syn_lhd_pyrope"):
                continue
            # `_gain`, i.e. baseline / measured -- the SAME orientation as every
            # other ratio on the site. A raw area/baseline plots an improvement
            # as a downward step here and an upward one on the report page.
            gain = _gain(area, b)
            if gain:
                per_run[run_id]["flows"][flow].append(gain)
                per_run[run_id]["seg"] = Ledger.segment_id(r)
                per_run[run_id]["date"] = r.get("date", "")

    points = sorted(
        (
            {"run": k, "date": v["date"], "seg": v["seg"],
             "flows": {f: _geomean(vals) for f, vals in v["flows"].items()}}
            for k, v in per_run.items()
        ),
        key=lambda p: p["run"],
    )
    history = root / TARGET_DIR / f"history-{slug(host)}.json"
    history.write_text(json.dumps(points, indent=2) + "\n")

    chart = _chart(points, [f for f in _SYN_FLOWS if f != base])
    out.write_text(
        _page(
            "lhdtrack — history",
            f"<h1>lhdtrack history</h1>"
            f"<p class='sub'><b>{_e(host)}</b> · geomean area, <b>baseline &divide; measured</b> "f"against <code>{_e(base)}</code>, so higher is better. One point per run. "
            f"Only this machine's runs are plotted, and the series "
            f"BREAKS at any tool-version change — a step caused by different hardware or a "
            f"different yosys is worse than no chart.</p>{chart}",
        )
    )
    return out


def _chart(points: list[dict], flows: list[str], w=1100, h=380) -> str:
    if not points:
        return "<p class='sub'>Not enough history to plot.</p>"
    pad = {"l": 55, "r": 20, "t": 20, "b": 46}
    vals = [v for p in points for v in p["flows"].values() if v]
    lo, hi = (min(vals + [1.0]) * 0.95, max(vals + [1.0]) * 1.05) if vals else (0, 2)
    span = max(hi - lo, 1e-6)

    def x(i):
        return pad["l"] + (i / max(len(points) - 1, 1)) * (w - pad["l"] - pad["r"])

    def y(v):
        return h - pad["b"] - ((v - lo) / span) * (h - pad["t"] - pad["b"])

    parts = [f'<svg viewBox="0 0 {w} {h}" role="img" aria-label="geomean area over time">']
    for frac in (0, 0.25, 0.5, 0.75, 1.0):
        v = lo + span * frac
        parts.append(
            f'<line x1="{pad["l"]}" x2="{w-pad["r"]}" y1="{y(v):.1f}" y2="{y(v):.1f}" '
            f'stroke="var(--line)"/><text x="{pad["l"]-8}" y="{y(v)+4:.1f}" '
            f'text-anchor="end" font-size="11" fill="var(--muted)">{v:.2f}×</text>'
        )
    parts.append(
        f'<line x1="{pad["l"]}" x2="{w-pad["r"]}" y1="{y(1.0):.1f}" y2="{y(1.0):.1f}" '
        f'stroke="var(--muted)" stroke-dasharray="4 3"/>'
    )

    for n, flow in enumerate(flows):
        color = SERIES_COLORS[n % len(SERIES_COLORS)]
        run = []  # one polyline per contiguous segment
        for i, p in enumerate(points):
            v = p["flows"].get(flow)
            broken = i > 0 and points[i - 1]["seg"] != p["seg"]
            if v is None or broken:
                if len(run) > 1:
                    parts.append(_poly(run, color))
                run = []
            if v is not None:
                run.append((x(i), y(v)))
                parts.append(f'<circle cx="{x(i):.1f}" cy="{y(v):.1f}" r="3" fill="{color}"/>')
        if len(run) > 1:
            parts.append(_poly(run, color))

    step = max(1, len(points) // 10)
    for i in range(0, len(points), step):
        parts.append(
            f'<text x="{x(i):.1f}" y="{h-pad["b"]+18}" text-anchor="middle" font-size="11" '
            f'fill="var(--muted)">{_e(points[i]["date"])}</text>'
        )
    parts.append("</svg>")

    legend = "".join(
        f'<span><i style="background:{SERIES_COLORS[n % len(SERIES_COLORS)]}"></i>{_e(f)}</span>'
        for n, f in enumerate(flows)
    )
    return f'<div class="scroll">{"".join(parts)}</div><div class="legend">{legend}</div>'


def _poly(pts, color):
    d = " ".join(f"{px:.1f},{py:.1f}" for px, py in pts)
    return f'<polyline points="{d}" fill="none" stroke="{color}" stroke-width="2"/>'


# The indicator is ABSOLUTELY POSITIONED, inside the cell's own right padding,
# so it costs no layout width. An inline glyph plus a margin is ~16px per
# header, and the synthesis table is 17 columns already at the edge of its
# 1400px column -- paying 270px of forced horizontal scroll for an arrow is the
# wrong trade on the one table people read most. `th` is `position:sticky`, so
# it is already a containing block; no extra `position` rule is needed.
SORT_CSS = """
th.sortable{cursor:pointer;user-select:none;-webkit-user-select:none}
th.sortable::after{content:"↕";position:absolute;right:.15em;top:50%;
transform:translateY(-50%);font-size:.75em;opacity:.35;pointer-events:none}
th.sortable:hover{color:var(--accent)}
th.sortable:hover::after{opacity:.8}
th.sortable[aria-sort=ascending]::after{content:"↑";opacity:1;color:var(--accent)}
th.sortable[aria-sort=descending]::after{content:"↓";opacity:1;color:var(--accent)}
th.sortable:focus-visible{outline:2px solid var(--accent);outline-offset:-2px}
"""


# CLICK A COLUMN HEADER TO SORT BY IT. Injected into every page by _page(),
# so it reaches the report, the timeseries page and the index alike.
SORT_JS = r"""
(function () {
  // Click a column header to sort by it. Every table on this page is a GRID
  // WITH MERGED CELLS, not a rectangle of <td>s: the synthesis tables carry a
  // two-row header (a `rowspan="2"` name column, then `colspan="5"` flow
  // groups over five leaf headers each), and a body row whose flow was skipped
  // collapses those five columns into one `<td colspan="5">skipped</td>`.
  // Indexing `row.cells[i]` would therefore read the wrong column for exactly
  // the rows a reader most wants to see -- an ASAP7 table where two of three
  // flows skipped is the common case, not the corner one. So the grid is
  // resolved the way the HTML spec defines it, once per table.
  //
  // `getAttribute('colspan')` rather than the `.colSpan` property: identical in
  // a browser, and it keeps this logic runnable in a headless DOM.

  var MISSING = /^(|-|–|—|n\/a)$/i;
  // A number, once the presentation is stripped: thousands separators, the
  // leading + of a signed error, and the trailing unit glyph of a ratio (2.00x)
  // or a percentage. Anything else is text.
  var NUMBER = /^\+?(-?\d+(?:\.\d+)?)\s*[×%]?$/;

  // `numeric: true` so w8 sorts before w32 and blk_a2 before blk_a10 -- a
  // report full of width- and depth-suffixed names is unreadable in codepoint
  // order. No `sensitivity` override: folding case would make distinct names
  // compare equal and leave their order to the tiebreaker.
  var collator = typeof Intl !== 'undefined' && Intl.Collator
    ? new Intl.Collator(undefined, { numeric: true })
    : { compare: function (a, b) { return a < b ? -1 : a > b ? 1 : 0; } };

  // Direct children only: `querySelector('tbody')` would reach into a nested
  // table and start reordering somebody else's rows.
  function section(table, tag) {
    var kids = table.children, i;
    for (i = 0; i < kids.length; i++) {
      if (kids[i].tagName && kids[i].tagName.toLowerCase() === tag) return kids[i];
    }
    return null;
  }

  function rowsOf(section) {
    var out = [], kids = section ? section.children : [], i;
    for (i = 0; i < kids.length; i++) {
      if (kids[i].tagName && kids[i].tagName.toLowerCase() === 'tr') out.push(kids[i]);
    }
    return out;
  }

  function span(cell, attr) {
    var v = parseInt(cell.getAttribute(attr), 10);
    return v > 0 ? v : 1;
  }

  // The HTML table model: place each cell at the first free slot of its row and
  // mark every slot its rowspan/colspan covers. grid[r][c] is {cell, w}, where
  // w is that cell's colspan -- w > 1 means column c is covered by a merge and
  // has no value of its own.
  function gridOf(rows) {
    var occ = [], r, i, c, cs, rs, dr, dc, cells;
    for (r = 0; r < rows.length; r++) {
      if (!occ[r]) occ[r] = [];
      cells = rows[r].children;
      c = 0;
      for (i = 0; i < cells.length; i++) {
        if (!cells[i].tagName) continue;
        while (occ[r][c]) c++;
        cs = span(cells[i], 'colspan');
        rs = span(cells[i], 'rowspan');
        for (dr = 0; dr < rs; dr++) {
          if (!occ[r + dr]) occ[r + dr] = [];
          for (dc = 0; dc < cs; dc++) occ[r + dr][c + dc] = { cell: cells[i], w: cs };
        }
        c += cs;
      }
    }
    return occ;
  }

  // A BADGE IS NOT DATA. `<td>246.49<span class="tag">norm</span></td>` has a
  // textContent of "246.49 norm", which parses as no number at all -- so the
  // area column would be typed as text and 246.49 would sort before 60.06.
  // That is the very column the report is read for, and the tags appear on
  // exactly the rows worth comparing. Skip them and read the value.
  function ownText(node) {
    if (node.nodeType === 3) return node.nodeValue || '';
    if (node.nodeType !== 1) return '';
    if (node.classList && node.classList.contains('tag')) return '';
    var out = '', kids = node.childNodes, i;
    for (i = 0; i < kids.length; i++) out += ownText(kids[i]);
    return out;
  }

  function textOf(entry) {
    // A merged cell is not this column's value. `skipped` spanning five columns
    // says nothing about area, so it sorts with the other blanks rather than
    // landing under "s" among the numbers.
    if (!entry || entry.w > 1) return null;
    var t = ownText(entry.cell).replace(/\s+/g, ' ').trim();
    return MISSING.test(t) ? null : t;
  }

  function numberOf(t) {
    if (t === null) return null;
    var m = NUMBER.exec(t.replace(/,/g, ''));
    return m ? parseFloat(m[1]) : null;
  }

  // Can sorting this column change anything? Only if two rows differ there.
  // A column that is `skipped` in every row -- ten of the seventeen in the
  // asap7 table -- has one key and no order, and an arrow over it would offer
  // a click that does nothing. Blank counts as its own key, so a column with
  // one measurement and five blanks is still worth sorting.
  function orderable(grid, col) {
    var first, j, t;
    for (j = 0; j < grid.length; j++) {
      t = textOf(grid[j][col]);
      if (j === 0) first = t;
      else if (t !== first) return true;
    }
    return false;
  }

  function enhance(table) {
    var thead = section(table, 'thead');
    var tbody = section(table, 'tbody');
    if (!thead || !tbody) return;
    var headRows = rowsOf(thead);
    var bodyRows = rowsOf(tbody);
    if (!headRows.length || bodyRows.length < 2) return;

    // A BODY `rowspan` MAKES THE TABLE UNSORTABLE, so leave it alone rather
    // than corrupt it. A merged cell belongs to the <tr> it is written in and
    // covers whatever rows follow it; move that row and the merge lands on
    // different data. No table this page emits has one -- this is the guard
    // that keeps that true if one ever appears.
    for (var b = 0; b < bodyRows.length; b++) {
      var bc = bodyRows[b].children;
      for (var k = 0; k < bc.length; k++) {
        if (bc[k].tagName && span(bc[k], 'rowspan') > 1) return;
      }
    }

    // The leaf header of a column is whatever occupies the LAST header row
    // there -- which is the group's own header for a `colspan` group, and the
    // `rowspan="2"` cell itself for a name column. One rule, both shapes.
    var hgrid = gridOf(headRows);
    var last = hgrid[headRows.length - 1] || [];
    var bgrid = gridOf(bodyRows);
    var seen = [], columns = [], c, e;
    for (c = 0; c < last.length; c++) {
      e = last[c];
      if (!e || seen.indexOf(e.cell) >= 0) continue;
      seen.push(e.cell);
      if (orderable(bgrid, c)) columns.push({ th: e.cell, col: c });
    }
    if (!columns.length) return;

    // `bodyRows` is the order the generator wrote -- grouped by test, or by
    // signed STA error, or refuted-first. That is a real ordering, so it is
    // kept as the third click state rather than lost at the first sort.
    var original = bodyRows.slice();
    var state = null;   // {col: n, dir: 1|-1}

    function apply(col, dir) {
      var rows = rowsOf(tbody);
      var grid = gridOf(rows);
      var keyed = [], numeric = true, any = false, j, t, n;
      for (j = 0; j < rows.length; j++) {
        t = textOf(grid[j][col]);
        n = numberOf(t);
        if (t !== null) { any = true; if (n === null) numeric = false; }
        keyed.push({ row: rows[j], t: t, n: n, i: j });
      }
      if (!any) numeric = false;
      keyed.sort(function (a, b) {
        // MISSING ALWAYS LAST, in both directions: a blank is the absence of a
        // value, not a very small one, and burying the rows you can read under
        // the rows you cannot is the opposite of what a sort is for.
        if (a.t === null || b.t === null) {
          if (a.t === null && b.t === null) return a.i - b.i;
          return a.t === null ? 1 : -1;
        }
        var d = numeric ? a.n - b.n : collator.compare(a.t, b.t);
        return d ? dir * d : a.i - b.i;   // ties keep their previous order
      });
      var frag = table.ownerDocument.createDocumentFragment();
      for (j = 0; j < keyed.length; j++) frag.appendChild(keyed[j].row);
      tbody.appendChild(frag);
    }

    function restore() {
      var frag = table.ownerDocument.createDocumentFragment(), j;
      for (j = 0; j < original.length; j++) frag.appendChild(original[j]);
      tbody.appendChild(frag);
    }

    function paint() {
      for (var k = 0; k < columns.length; k++) {
        var th = columns[k].th;
        if (state && state.col === columns[k].col) {
          th.setAttribute('aria-sort', state.dir > 0 ? 'ascending' : 'descending');
        } else {
          th.removeAttribute('aria-sort');
        }
      }
    }

    columns.forEach(function (entry) {
      var label = (entry.th.textContent || '').trim();
      entry.th.classList.add('sortable');
      entry.th.setAttribute('tabindex', '0');
      entry.th.setAttribute('title', label ? 'Sort by ' + label : 'Sort by this column');
      if (!label) entry.th.setAttribute('aria-label', 'sort by this column');
      function click() {
        // Three states, so a reader can always get back to the order the page
        // was written in -- which is the one grouped by test name.
        if (!state || state.col !== entry.col) state = { col: entry.col, dir: 1 };
        else if (state.dir > 0) state = { col: entry.col, dir: -1 };
        else state = null;
        if (state) apply(state.col, state.dir); else restore();
        paint();
      }
      entry.th.addEventListener('click', click);
      entry.th.addEventListener('keydown', function (ev) {
        if (ev.key === 'Enter' || ev.key === ' ' || ev.key === 'Spacebar') {
          ev.preventDefault();
          click();
        }
      });
    });
  }

  function init() {
    var tables = document.querySelectorAll('table'), i;
    for (i = 0; i < tables.length; i++) enhance(tables[i]);
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
"""


def _page(title: str, body: str) -> str:
    stamp = _dt.datetime.now().strftime("%Y-%m-%d %H:%M")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_e(title)}</title><style>{CSS}{CHART_CSS}{SORT_CSS}</style></head>
<body><div class="wrap">{body}
<p class="sub muted" style="margin-top:3rem">Generated {stamp} from <code>data/</code>.
This page is a pure rendering of the ledger — nothing is recorded only in
<code>target/</code>, which is regenerated on every run.</p>
</div>
<script>{CHART_JS}</script>
<script>{SORT_JS}</script>
</body></html>
"""


# ----------------------------------------------------------------- index ----
def write_index(root: Path) -> Path:
    """One page per machine, listed from the ledger.

    Machines are listed separately rather than merged because a merged table
    would be a table of hardware differences: `uname -n` is the boundary of
    what is comparable.
    """
    rows = Ledger(root).load()
    out = root / TARGET_DIR / "index.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    by_host: dict[str, dict] = {}
    for r in rows:
        h = r.get("host", "unknown")
        e = by_host.setdefault(h, {"runs": set(), "last": "", "class": "", "n": 0})
        e["runs"].add(r.get("run_id", ""))
        e["last"] = max(e["last"], r.get("date", ""))
        e["class"] = r.get("host_class", "")
        e["n"] += 1

    if not by_host:
        out.write_text(_page("lhdtrack", "<h1>lhdtrack</h1><p class='sub'>No runs yet.</p>"))
        return out

    body = "".join(
        f'<tr><td class="l"><a href="results-syn-{slug(h)}.html">{_e(h)}</a></td>'
        f'<td class="l muted">{_e(e["class"])}</td>'
        f'<td>{len(e["runs"])}</td><td>{e["n"]:,}</td>'
        f'<td class="l muted">{_e(e["last"])}</td>'
        + ''.join(f'<td class="l"><a href="results-{suffix}-{slug(h)}.html">{label}</a></td>'
                  for suffix, label in RESULT_KINDS.values())
        +
        f'<td class="l"><a href="timeseries-{slug(h)}.html">history</a></td></tr>'
        for h, e in sorted(by_host.items())
    )
    out.write_text(
        _page(
            "lhdtrack",
            f"""<h1>lhdtrack</h1>
<p class="sub">Daily QoR regression for LiveHD. Separate result pages per machine — wall clock
and peak memory do not travel between hosts, so <code>uname -n</code> is the
boundary of what may be compared.</p>
<div class="scroll"><table>
<thead><tr><th class="l">machine</th><th class="l">platform</th><th>runs</th>
<th>rows</th><th class="l">last run</th><th class="l">Synthesis</th>
<th class="l">Simulation</th><th class="l">LEC</th><th class="l">History</th></tr></thead>
<tbody>{body}</tbody></table></div>""",
        )
    )
    return out


def write_all(root: Path, cfg: dict | None = None, only: str | None = None) -> list[Path]:
    """Render every machine's pages, then the index.

    ALL hosts, not just this one. `target/` is a pure function of `data/`, and
    the index links to every machine that has ever reported -- rendering only
    the local host would leave those links pointing at files that do not exist.
    Another machine's committed ledger is enough to rebuild its page here.
    """
    hosts = [only] if only else (Ledger(root).hosts() or [host_name()])
    if not only and host_name() not in hosts:
        hosts.append(host_name())  # this machine's page exists even before its first run

    out: list[Path] = []
    for h in hosts:
        out.extend(write_results(root, cfg=cfg, host=h))
        out.append(write_timeseries(root, cfg=cfg, host=h))
    out.append(write_index(root))
    return out
