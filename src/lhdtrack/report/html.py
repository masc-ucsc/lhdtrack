"""Render `target/` from `data/`.

SECTION ORDER: synthesis, STA accuracy, simulation. The first and last are the
results; STA accuracy sits between them because it is a diagnostic on the
synthesis numbers above it -- less important than either, but the thing that
tells you when a delay reported above cannot be trusted.

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

from ..ledger import TARGET_DIR, Ledger, slug
from ..run import host_name

CSS = """
:root{--bg:#fff;--fg:#16181d;--muted:#6b7280;--line:#e5e7eb;--head:#f7f8fa;
--good:#0a7c3f;--bad:#b91c1c;--warn:#a16207;--accent:#1d4ed8;--chip:#eef2ff;--series-a:#b45309;--series-b:#1d4ed8}
@media (prefers-color-scheme:dark){:root{--bg:#0f1115;--fg:#e6e8ec;--muted:#9aa1ab;
--line:#262b33;--head:#161a20;--good:#4ade80;--bad:#f87171;--warn:#fbbf24;
--accent:#93b4ff;--chip:#1b2233;--series-a:#fbbf24;--series-b:#93b4ff}}
*{box-sizing:border-box}
body{margin:0;padding:2rem 1.5rem 4rem;background:var(--bg);color:var(--fg);
font:14px/1.5 ui-sans-serif,-apple-system,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1400px;margin:0 auto}
h1{font-size:1.5rem;margin:0 0 .25rem}
h2{font-size:1.05rem;margin:2.5rem 0 .5rem;padding-bottom:.35rem;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);margin:0 0 1.5rem}
.meta{display:flex;flex-wrap:wrap;gap:.4rem;margin:0 0 1.5rem}
.chip{background:var(--chip);border-radius:999px;padding:.15rem .6rem;font-size:12px;
white-space:nowrap}
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


# ---------------------------------------------------------------- report ----
def write_report(
    root: Path, out: Path | None = None, cfg: dict | None = None, host: str | None = None
) -> Path:
    cfg = cfg or {}
    host = host or host_name()
    rcfg = cfg.get("report", {})
    base_syn = rcfg.get("baseline_flow", "syn_yosys_abc")
    base_sim = rcfg.get("baseline_sim_flow", "sim_verilator")
    headline = set(rcfg.get("headline_pyrope_status", ["idiomatic"]))

    # Scoped to ONE machine (`uname -n`). Wall clock and peak RSS are the
    # majority of what is reported here and they are not portable, so a table
    # mixing hosts would be a table of hardware differences.
    rows = Ledger(root).latest_run(host)
    out = out or root / TARGET_DIR / f"report-{slug(host)}.html"
    out.parent.mkdir(parents=True, exist_ok=True)

    if not rows:
        out.write_text(
            _page(
                "lhdtrack",
                f"<h1>lhdtrack</h1><p class='sub'>No runs recorded on "
                f"<b>{_e(host)}</b> yet. Run <code>make run</code>.</p>",
            )
        )
        return out

    ident = rows[0]
    body = [_header(ident, rows)]

    # index: (test, config, tech) -> flow -> row
    syn: dict[tuple, dict[str, dict]] = defaultdict(dict)
    sim: dict[tuple, dict[str, dict]] = defaultdict(dict)
    lec: dict[tuple, dict[str, dict]] = defaultdict(dict)
    for r in rows:
        key = (r["suite"], r["test"], r.get("config", "default"), r.get("tech"))
        if r.get("kind") == "synth":
            syn[key][r["flow"]] = r
        elif r.get("kind") == "sim":
            sim[(r["suite"], r["test"], r.get("config", "default"), None)][r["flow"]] = r
        elif r.get("kind") == "lec":
            # KEYED BY TECH LIKE ANY OTHER ROW. `lec_netlist` sets USES_TECH, so
            # it produces one row per technology; collapsing tech to None made
            # sky130's and asap7's rows overwrite each other. It also proves a
            # DIFFERENT obligation, so it belongs to `_netlist_lec_section`
            # alone -- mixing it in here made `len(...) == 2` below false for
            # every test that ran it, silently emptying the proof-time chart.
            if r.get("flow") == "lec_netlist":
                continue
            lec[(r["suite"], r["test"], r.get("config", "default"), None)][r["flow"]] = r

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
        data = _chart_data(
            keys, syn, base_syn, list(_SYN_FLOWS[1:]),
            [
                ("delay", "Delay", unit, lambda r: r.get("sta", {}).get("opensta_ns")),
                ("area", "Area", "µm²", lambda r: r.get("qor", {}).get("area_um2")),
                ("cells", "Cells", "", lambda r: r.get("qor", {}).get("cells")),
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

    body.append(_sta_section(rows, cfg))

    if sim:
        keys = sorted(sim, key=lambda k: (k[1], k[2]))
        body.append("<h2>Simulation</h2>")
        data = _chart_data(
            keys, sim, base_sim, list(_SIM_FLOWS[1:]),
            [
                ("exec", "Simulation speed", "", lambda r: r.get("sim", {}).get("exec_ms")),
                ("cc", "Host C++ compile", "s", lambda r: r.get("time_ms", {}).get("cc")),
                ("setup", "Front end", "s", lambda r: r.get("time_ms", {}).get("setup")),
                ("mem", "Peak memory", "MB",
                 lambda r: r.get("peak_rss_kb", {}).get("max")),
            ],
        )
        if data:
            body.append(_chart_block("chart-sim", data))
        body.append(_sim_table(None, keys, sim, base_sim, headline))

    if lec:
        # TWO DIFFERENT OBLIGATIONS, never mixed. `pyrope vs verilog` compares
        # two source descriptions; `rtl vs netlist` compares behavioural RTL to
        # a flat sea of mapped cells. Averaging their times or checking their
        # verdicts against each other would be comparing different questions.
        keys = sorted(lec, key=lambda k: (k[1], k[2]))
        body.append("<h2>Equivalence — Pyrope vs Verilog</h2>")
        data = _chart_data(
            keys, lec, "lec_lgyosys", ["lec_lhd"],
            [("prove", "Proof time", "s", lambda r: r.get("lec_result", {}).get("ms"))],
        )
        if data:
            # Only tests where BOTH backends reached the same verdict can be
            # timed against each other: a solver that gave up is faster than one
            # that proved something, and plotting that as a win would be a lie.
            data["groups"] = [
                g for g, key in zip(data["groups"], keys)
                if len({
                    r.get("lec_result", {}).get("verdict")
                    for r in lec[key].values()
                    if r.get("lec_result", {}).get("verdict") in ("proven", "refuted")
                }) == 1
                and len(lec[key]) == 2
            ]
            if data["groups"]:
                body.append(_chart_block("chart-lec", data))
        body.append(_lec_section(keys, lec))
        body.append(_netlist_lec_section(rows))

    body.append(_problems(rows))
    out.write_text(_page("lhdtrack — QoR report", "\n".join(body)))
    return out


def _header(ident: dict, rows: list[dict]) -> str:
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
<h1>lhdtrack</h1>
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
        notes = {r["sta"]["opensta_note"] for r in rows if r.get("sta", {}).get("opensta_note")}
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
            "Until an independent timer runs on the same netlist, LiveHD's own "
            "timing numbers are unchecked.</p>"
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


_SYN_FLOWS = ("syn_yosys_abc", "syn_lhd_verilog", "syn_lhd_pyrope")
_SYN_LABELS = ("yosys+slang+abc", "lhd·verilog", "lhd·pyrope")


def _synth_table(title, keys, index, base_flow, headline, unit="ns") -> str:
    head = ['<tr><th class="l" rowspan="2">test</th><th class="l" rowspan="2">config</th>']
    for label in _SYN_LABELS:
        head.append(f'<th class="g" colspan="5">{_e(label)}</th>')
    head.append("</tr><tr>")
    # Timing first, then area, then what the tool cost to run -- the order the
    # metrics actually matter in.
    for _ in _SYN_FLOWS:
        # The delay unit is the LIBRARY's, not a fixed "ns": sky130 Liberty is
        # 1ns and ASAP7 is 1ps, so labelling both "ns" understates ASAP7 by
        # 1000x -- 154 ps would read as 154 ns, i.e. slower than 130nm.
        head.append(
            f'<th class="g">delay {_e(unit)}</th><th>area µm²</th><th>cells</th>'
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
            if not r or r.get("status") != "ok":
                note = (r or {}).get("status", "-")
                cells.append(f'<td class="muted g" colspan="5">{_e(note)}</td>')
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
            cells.append(
                f'<td class="g">{_fmt(delay, 2 if unit != "ns" else 3)}</td>'
                f"<td>{_fmt(area)}{norm}{stale}</td><td>{_fmt(ncells)}</td>"
                f"<td>{_fmt(secs, 1)}</td><td>{_fmt(mem, 0)}</td>"
            )
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
        foot.append("<td>" + _ratio(ratios[(flow, "time")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "mem")]) + "</td>")
    foot.append("</tr>")

    note = (
        "Every ratio is <b>baseline &divide; measured</b>, so <b>higher is always "
        "better</b>: 2.00&times; means twice as fast, or half the area, or half the "
        "memory. Geomean covers LEC-proven, hand-written Pyrope only. "
        "<span class=\'tag\'>norm</span> marks a netlist whose behavioural registers "
        "yosys mapped, so it could be counted and timed exactly like the others."
    )

    heading = f"<h2>{_e(title)}</h2>" if title else ""
    return f"""{heading}
<div class="scroll"><table><thead>{''.join(head)}</thead>
<tbody>{''.join(body)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>
<p class="sub muted">{note}</p>
"""


_SIM_FLOWS = ("sim_verilator", "sim_lhd_verilog", "sim_lhd_pyrope")
_SIM_LABELS = ("verilator", "lhd·verilog", "lhd·pyrope")


def _sim_table(title, keys, index, base_flow, headline) -> str:
    head = ['<tr><th class="l" rowspan="2">test</th><th class="l" rowspan="2">config</th>']
    for label in _SIM_LABELS:
        head.append(f'<th class="g" colspan="4">{_e(label)}</th>')
    head.append("</tr><tr>")
    for _ in _SIM_FLOWS:
        head.append('<th class="g">setup s</th><th>c++ s</th><th>exec s</th><th>Mcyc/s</th>')
    head.append("</tr>")

    body, ratios = [], defaultdict(list)
    for key in keys:
        _, test, config, _ = key
        flows = index[key]
        base = flows.get(base_flow, {})
        cells = [f'<td class="l">{_e(test)} {_tags(flows)}</td><td class="l muted">{_e(config)}</td>']
        for flow in _SIM_FLOWS:
            r = flows.get(flow)
            if not r or r.get("status") != "ok":
                cells.append(f'<td class="muted g" colspan="4">{_e((r or {}).get("status", "—"))}</td>')
                continue
            t = r.get("time_ms", {})
            s = r.get("sim", {})
            rate = s.get("cycles_per_s", 0) / 1e6
            cells.append(
                f'<td class="g">{_fmt(t.get("setup", 0)/1000, 1)}</td>'
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
        body.append("<tr>" + "".join(cells) + "</tr>")

    foot = ['<tr><td class="l" colspan="2">geomean vs verilator</td>']
    for flow in _SIM_FLOWS:
        foot.append('<td class="g">' + _ratio(ratios[(flow, "setup")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "cc")]) + "</td>")
        foot.append("<td>" + _ratio(ratios[(flow, "exec")]) + "</td>")
        # Mcyc/s is 1/exec by construction; a second ratio for it would just
        # restate the exec column.
        foot.append('<td class="muted">—</td>')
    foot.append("</tr>")

    heading = f"<h2>{_e(title)}</h2>" if title else ""
    return f"""{heading}
<div class="scroll"><table><thead>{''.join(head)}</thead>
<tbody>{''.join(body)}</tbody><tfoot>{''.join(foot)}</tfoot></table></div>
<p class="sub muted">Ratios are <b>baseline &divide; measured</b>, so
<b>higher is always better</b> — 2.00&times; means twice as fast.
All three simulators fold the same checksum or the test fails.</p>
"""


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
    if not base or base.get("status") != "ok":
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


def _problems(rows: list[dict]) -> str:
    bad = [r for r in rows if r.get("status") in ("failed", "skipped") or r.get("note")]
    if not bad:
        return ""
    body = "".join(
        f'<tr><td class="l">{_e(r["test"])}</td><td class="l muted">{_e(r["flow"])}</td>'
        f'<td class="l {"bad" if r.get("status")=="failed" else "warn"}">{_e(r.get("status"))}</td>'
        f'<td class="note">{_e(r.get("note", ""))}</td></tr>'
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

# A verdict is THREE-state. `proven` and `refuted` are answers; `timeout`,
# `unsupported` and `error` are the absence of one, and colouring them like a
# refutation would report "this design is wrong" when the honest answer is
# "the solver gave up".
_VERDICT_CLASS = {
    "proven": "good",
    "refuted": "bad",
    "timeout": "warn",
    "unsupported": "muted",
    "error": "warn",
    "none": "muted",
}


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
            verdict = block.get("verdict") or (r or {}).get("status", "—")
            counts[flow][verdict] += 1
            verdicts[flow] = verdict
            # A MEASURED ZERO IS NOT AN ABSENCE. `if secs` printed the dash for
            # `ms == 0` as well as for a missing block, so the fastest proof on
            # the page rendered as "no result" -- and now that the column sorts,
            # it would rank below every slower one instead of first.
            ms = block.get("ms")
            cells.append(
                f'<td class="l g {_VERDICT_CLASS.get(verdict, "muted")}">{_e(verdict)}</td>'
                f"<td>{_fmt(ms / 1000, 2) if ms is not None else '—'}</td>"
            )
        # Speedup of lhd over the yosys baseline, on tests where BOTH answered.
        a = (by_flow.get("lec_lgyosys") or {}).get("lec_result", {})
        b = (by_flow.get("lec_lhd") or {}).get("lec_result", {})
        gain = None
        if a.get("ms") and b.get("ms") and a.get("verdict") == b.get("verdict"):
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
        f"{len(speedups)} test(s) where both reached the same verdict."
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
    return f"""
<p class="sub">Both backends prove the same obligation, so the times compare and
the verdicts check each other. {summary}.{speed}{split_note}</p>
<div class="scroll"><table><thead>{head}</thead>
<tbody>{''.join(rows_html)}</tbody></table></div>
<p class="sub muted">A verdict is three-state: <span class="good">proven</span> and
<span class="bad">refuted</span> are answers; <span class="warn">timeout</span>,
<span class="muted">unsupported</span> and <span class="warn">error</span> are the
absence of one. Until a test is proven, its QoR numbers above are reported but
never aggregated — a Pyrope win might be a different circuit.</p>
"""


def _netlist_lec_section(rows: list[dict]) -> str:
    """Did synthesis preserve the design?

    A separate section because it is a separate claim. Every area and delay
    number in the synthesis tables is a statement about a NETLIST; this is what
    says the netlist is still the circuit the RTL described. A refutation here
    is far more serious than two source descriptions differing -- it means the
    synthesis flow broke the design.
    """
    items = [
        r for r in rows
        if r.get("flow") == "lec_netlist" and r.get("lec_result")
    ]
    if not items:
        return ""

    counts = Counter(r["lec_result"]["verdict"] for r in items)
    refuted = [r for r in items if r["lec_result"]["verdict"] == "refuted"]
    skipped = sum(1 for r in rows if r.get("flow") == "lec_netlist" and r["status"] == "skipped")

    body = "".join(
        f'<tr><td class="l">{_e(r["test"])}</td>'
        f'<td class="l muted">{_e(r.get("config"))}</td>'
        f'<td class="l muted">{_e(r.get("tech"))}</td>'
        f'<td class="l {_VERDICT_CLASS.get(r["lec_result"]["verdict"], "muted")}">'
        f'{_e(r["lec_result"]["verdict"])}</td>'
        f'<td>{_fmt(r["lec_result"].get("ms", 0) / 1000, 2)}</td>'
        f'<td class="note">{_e(r["lec_result"].get("counterexample", "") or r.get("note", ""))}</td></tr>'
        for r in sorted(items, key=lambda r: (r["lec_result"]["verdict"] != "refuted", r["test"]))
    )
    summary = ", ".join(f"{n} {_e(v)}" for v, n in counts.most_common())
    alarm = (
        f" <span class='bad'><b>{len(refuted)} netlist(s) NOT equivalent to their RTL</b></span> — "
        "synthesis changed the design, so every area and delay number above for "
        "those tests describes a circuit that is not the one written."
        if refuted else ""
    )
    return f"""
<h2>Equivalence — RTL vs synthesized netlist</h2>
<p class="sub">Behavioural RTL against a flat sea of mapped standard cells: no
shared boundaries, no shared names, ABC-rewritten logic. A much harder
obligation than comparing two sources, and the one that says synthesis
preserved the design. {summary}{f", {skipped} skipped" if skipped else ""}.{alarm}</p>
<div class="scroll"><table>
<thead><tr><th class="l">test</th><th class="l">config</th><th class="l">tech</th>
<th class="l">verdict</th><th>s</th><th class="l">note</th></tr></thead>
<tbody>{body}</tbody></table></div>
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
                if r and r.get("status") == "ok":
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
            "solid": bool(first.get("comparable")),
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
    return (
        f'<div class="chart" id="{_e(chart_id)}">'
        f'<div class="switch">{buttons}</div>'
        f'<div class="plot"></div>'
        f'<div class="legend"></div>'
        f'<script type="application/json" class="chart-data">'
        f'{json.dumps(data)}</script></div>'
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
border-radius:5px;white-space:nowrap;transform:translate(-50%,-135%);z-index:5}
.caption{color:var(--muted);font-size:12px;margin:.4rem 0 0}
"""

CHART_JS = r"""
(function () {
  var NS = 'http://www.w3.org/2000/svg';
  var COL = { a: 'var(--series-a)', b: 'var(--series-b)' };
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
    var groups = data.groups.filter(function (g) { return g.values[metricKey]; });
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
    var minGroup = 74;
    var outer = plot.clientWidth || 900;
    var W = Math.max(outer, padL + padR + groups.length * minGroup);
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
          x: (bx + 1).toFixed(1), y: top.toFixed(1),
          width: (bw - 2).toFixed(1), height: Math.max(bot - top, 1.5).toFixed(1),
          fill: COL[f.series] || 'var(--accent)', rx: 2,
          opacity: g.solid ? 0.9 : 0.4, class: 'bar'
        });
        rect.addEventListener('mousemove', function (e) {
          var r = plot.getBoundingClientRect();
          tip.style.left = (e.clientX - r.left + plot.scrollLeft) + 'px';
          tip.style.top = (e.clientY - r.top) + 'px';
          tip.style.opacity = 1;
          tip.textContent = g.test + '#' + g.config + '  ' + f.label + '  ' +
            val.toFixed(2) + '× ' + (val >= 1 ? 'better' : 'worse') +
            (g.solid ? '' : '  (not LEC-proven)');
        });
        rect.addEventListener('mouseleave', function () { tip.style.opacity = 0; });
        svg.appendChild(rect);
        svg.appendChild(el('text', {
          x: (bx + bw / 2).toFixed(1),
          y: (val >= 1 ? top - 5 : bot + 12).toFixed(1),
          'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)'
        }, val.toFixed(2)));
      });
      svg.appendChild(el('text', {
        x: (gx + gw / 2).toFixed(1), y: (H - padB + 19).toFixed(1),
        'text-anchor': 'middle', 'font-size': 11, fill: 'var(--fg)'
      }, g.test));
      if (g.config && g.config !== 'default') {
        svg.appendChild(el('text', {
          x: (gx + gw / 2).toFixed(1), y: (H - padB + 32).toFixed(1),
          'text-anchor': 'middle', 'font-size': 10, fill: 'var(--muted)'
        }, g.config));
      }
    });
    plot.appendChild(svg);

    var cap = document.createElement('p');
    cap.className = 'caption';
    cap.innerHTML = '<b>' + metric.label + '</b>' + (metric.unit ? ' (' + metric.unit + ')' : '') +
      ' relative to <code>' + data.baseline + '</code> — higher is better, ' +
      'sorted worst → best by the Pyrope result.';
    plot.appendChild(cap);
  }

  function legend(root, data) {
    var box = root.querySelector('.legend');
    box.innerHTML = data.flows.map(function (f) {
      return '<span><i style="background:' + (COL[f.series] || 'var(--accent)') + '"></i>' +
        f.label + '</span>';
    }).join('') + '<span>faded = not LEC-proven or not hand-written Pyrope</span>';
  }

  document.querySelectorAll('.chart').forEach(function (root) {
    var data = JSON.parse(root.querySelector('.chart-data').textContent);
    var current = data.metrics[0].key;
    legend(root, data);
    draw(root, data, current);
    root.querySelectorAll('.switch button').forEach(function (b) {
      b.addEventListener('click', function () {
        root.querySelectorAll('.switch button').forEach(function (o) { o.classList.remove('on'); });
        b.classList.add('on');
        current = b.dataset.metric;
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
        f'<tr><td class="l"><a href="report-{slug(h)}.html">{_e(h)}</a></td>'
        f'<td class="l muted">{_e(e["class"])}</td>'
        f'<td>{len(e["runs"])}</td><td>{e["n"]:,}</td>'
        f'<td class="l muted">{_e(e["last"])}</td>'
        f'<td class="l"><a href="timeseries-{slug(h)}.html">history</a></td></tr>'
        for h, e in sorted(by_host.items())
    )
    out.write_text(
        _page(
            "lhdtrack",
            f"""<h1>lhdtrack</h1>
<p class="sub">Daily QoR regression for LiveHD. One page per machine — wall clock
and peak memory do not travel between hosts, so <code>uname -n</code> is the
boundary of what may be compared.</p>
<div class="scroll"><table>
<thead><tr><th class="l">machine</th><th class="l">platform</th><th>runs</th>
<th>rows</th><th class="l">last run</th><th class="l"></th></tr></thead>
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
        out.append(write_report(root, cfg=cfg, host=h))
        out.append(write_timeseries(root, cfg=cfg, host=h))
    out.append(write_index(root))
    return out
