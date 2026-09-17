#!/usr/bin/env python3
"""Render this machine's report from a run that is STILL GOING.

`lhdtrack run` can only write the ledger after gating -- the checksum and STA
gates are cross-flow, so no row's verdict is final until its siblings exist --
which means a 2236-job regression shows nothing at all for hours. The runner
does stream every finished row to `var/runs/<run_id>/partial.jsonl`, though, so
a readable page is available the whole time if something stamps the identity
block onto those rows and renders them.

That is all this does. It renders into a SHADOW root (`var/live/`) so the
committed `data/ledger-<host>.jsonl` is never touched: the authoritative ledger
still gets exactly one gated append, from the runner, when the run ends.

    tools/live_report.py             # render once from the newest run
    tools/live_report.py --watch 300 # re-render every 5 min until the run ends

The rows are UNGATED, and the page says so: a row here can still be turned into
a failure by a sibling it is waiting on (a simulator checksum that disagrees, an
OpenTimer/OpenSTA delta over the limit). Treat it as progress, not as a verdict.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import platform
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.cli import find_root, load_config  # noqa: E402
from lhdtrack.ledger import slug  # noqa: E402
from lhdtrack.report import write_report  # noqa: E402
from lhdtrack.run import host_name  # noqa: E402


def newest_run(root: Path, run_id: str | None = None) -> Path | None:
    """The run to render: `run_id` if pinned, else the newest.

    PIN IT while debugging. A focused `lhdtrack run --test X --flow Y` creates
    its own `var/runs/<id>/`, and "newest" would then swap a 2000-row page for a
    one-row one halfway through the regression it is supposed to be showing.
    """
    if run_id:
        partial = root / "var" / "runs" / run_id / "partial.jsonl"
        return partial if partial.exists() else None
    runs = list((root / "var" / "runs").glob("*/partial.jsonl"))
    return max(runs, key=lambda p: p.parent.name) if runs else None


def identity(root: Path, run_id: str) -> dict:
    """The block `Runner.identity` stamps on every ledger row.

    Read from the staged toolchain rather than reconstructed, so a live page
    carries the same version chips (and therefore the same timeseries segment)
    as the gated rows that replace it.
    """
    tc = json.loads((root / "var" / "toolchain" / "toolchain.json").read_text())
    return {
        "date": dt.date.today().isoformat(),
        "run_id": run_id,
        "host": host_name(),
        "host_class": tc.get("host_class", ""),
        "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
        "cpus": os.cpu_count(),
        "versions": tc.get("versions", {}),
    }


def render(root: Path, cfg: dict, run_id: str | None = None) -> tuple[Path, int, dict]:
    partial = newest_run(root, run_id)
    if partial is None:
        raise SystemExit(f"no partial.jsonl for run {run_id or '(newest)'} under var/runs/")
    ident = identity(root, partial.parent.name)

    live = root / "var" / "live" / "data"
    live.mkdir(parents=True, exist_ok=True)
    host = ident["host"]
    out_ledger = live / f"ledger-{slug(host)}.jsonl"

    counts = {"ok": 0, "failed": 0, "skipped": 0}
    n = 0
    with out_ledger.open("w") as fh:
        for line in partial.read_text().splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue  # the runner may be mid-write on the last line
            fh.write(json.dumps({**ident, **row}, sort_keys=True) + "\n")
            counts[row.get("status", "ok")] = counts.get(row.get("status", "ok"), 0) + 1
            n += 1

    out = root / "target" / f"report-{slug(host)}.html"
    write_report(root / "var" / "live", out=out, cfg=cfg, host=host)
    _mark_live(out, ident["run_id"], n, counts)
    return out, n, counts


_BANNER = """<p class="sub" style="border-left:4px solid #d08770;padding-left:.7em">
<b>Live &mdash; run {run} is still going.</b> {n} rows so far
({ok} ok, {failed} failed, {skipped} skipped), streamed from
<code>var/runs/{run}/partial.jsonl</code> and <b>ungated</b>: the checksum and
OpenTimer/OpenSTA gates are cross-flow, so a row here can still become a failure
when the sibling it is waiting on finishes. Tests not yet reached are simply
absent. Regenerated every few minutes; replaced by the gated page when the run
ends.</p>"""


def _mark_live(out: Path, run: str, n: int, counts: dict) -> None:
    html = out.read_text()
    banner = _BANNER.format(run=run, n=n, ok=counts.get("ok", 0),
                            failed=counts.get("failed", 0), skipped=counts.get("skipped", 0))
    # After the header block, before the first section.
    marker = "<h2>"
    idx = html.find(marker)
    if idx != -1:
        html = html[:idx] + banner + html[idx:]
    out.write_text(html)


def running(root: Path) -> bool:
    return subprocess.run(["pgrep", "-f", r"lhdtrack\.cli run"],
                          capture_output=True).returncode == 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--watch", type=int, metavar="SEC",
                    help="re-render every SEC seconds until the run exits")
    ap.add_argument("--run", metavar="RUN_ID",
                    help="render this run instead of the newest one; pin it so a "
                         "focused debug run cannot hijack the page")
    ap.add_argument("-C", "--root", type=Path)
    args = ap.parse_args()

    root = args.root or find_root(Path(__file__).resolve().parent)
    cfg = load_config(root)

    while True:
        out, n, counts = render(root, cfg, args.run)
        print(f"{dt.datetime.now():%H:%M} {out} <- {n} rows "
              f"({counts.get('ok', 0)} ok, {counts.get('failed', 0)} failed, "
              f"{counts.get('skipped', 0)} skipped)", flush=True)
        if not args.watch:
            return 0
        if not running(root):
            print("run finished; last live render done", flush=True)
            return 0
        time.sleep(args.watch)


if __name__ == "__main__":
    raise SystemExit(main())
