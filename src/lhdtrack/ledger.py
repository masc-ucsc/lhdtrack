"""The append-only result ledger under `data/`.

    data/ledger-<host>.jsonl     one file per machine, committed
    target/                      everything rendered from it, gitignored

ONE FILE PER MACHINE, not one shared file. Two machines running cron both append
to the ledger, and a single shared `ledger.jsonl` would conflict on every push --
two appends at the same end of the same file, every night, forever. Splitting by
`uname -n` makes those writes disjoint, so a merge is a fast-forward.

It also matches what the data means. A number is only comparable to another
number from the same host and the same Liberty, so every row carries a full
identity block and the report segments a series wherever any of it changes. A
speedup that is really a new laptop is the most expensive mistake a tracker can
make.

The HTML is a pure rendering of these files and is regenerated on every run, so
a measured regression is visible rather than silently retried. Nothing is ever
recorded only in `target/`.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DATA_DIR = "data"
TARGET_DIR = "target"

# A series is only continuous while all of these hold; any change starts a new
# segment in the timeseries.
SEGMENT_KEYS = ("host_class", "versions")


def slug(host: str) -> str:
    """Filename-safe `uname -n`, so one directory can hold every machine."""
    return re.sub(r"[^A-Za-z0-9._-]", "_", host) or "unknown"


class Ledger:
    def __init__(self, root: Path, host: str | None = None):
        self.root = root
        self.dir = root / DATA_DIR
        self.host = host

    # -- paths -------------------------------------------------------------
    def path_for(self, host: str) -> Path:
        return self.dir / f"ledger-{slug(host)}.jsonl"

    def files(self) -> list[Path]:
        """Every ledger this repository holds, newest naming first.

        Also picks up a legacy single `ledger.jsonl` (from either the old
        `site/` layout or an un-split `data/`) so an existing history is not
        stranded by the move.
        """
        found = sorted(self.dir.glob("ledger-*.jsonl")) if self.dir.is_dir() else []
        for legacy in (self.dir / "ledger.jsonl", self.root / "site" / "ledger.jsonl"):
            if legacy.exists():
                found.append(legacy)
        return found

    # -- writing -----------------------------------------------------------
    def append(self, identity: dict, rows: list) -> int:
        host = identity.get("host") or self.host or "unknown"
        path = self.path_for(host)
        path.parent.mkdir(parents=True, exist_ok=True)
        n = 0
        with path.open("a") as fh:
            for row in rows:
                doc = row.to_dict() if hasattr(row, "to_dict") else dict(row)
                # `cmds` carry absolute paths: useful in the local log, pure
                # host-specific noise in a file meant to be diffed over months.
                doc.pop("cmds", None)
                fh.write(json.dumps({**identity, **doc}, sort_keys=True) + "\n")
                n += 1
        return n

    # -- reading -----------------------------------------------------------
    def load(self, host: str | None = None) -> list[dict]:
        out: list[dict] = []
        for path in self.files():
            for line in path.read_text().splitlines():
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue  # a truncated tail must not make the history unreadable
                if host is None or row.get("host") == host:
                    out.append(row)
        return out

    def latest_run(self, host: str | None = None) -> list[dict]:
        """The newest run, of `host` if given.

        Scoped by host here rather than filtered afterwards: another machine
        finishing an hour later must not blank this machine's page.
        """
        rows = self.load(host)
        if not rows:
            return []
        newest = max(r.get("run_id", "") for r in rows)
        return [r for r in rows if r.get("run_id") == newest]

    def latest_rows(self, host: str | None = None) -> list[dict]:
        """Newest observation for every report slot, for one host if given.

        A focused corrective run must replace the affected rows without hiding
        all of the unaffected rows from the last full matrix.  Start at the
        newest widest run, then overlay only later rows.  This also prevents a
        configuration removed from the current corpus from lingering forever
        merely because it exists in old history.  Append order breaks ties
        within a run, as desired when a slot is deliberately measured twice.
        """
        rows = self.load(host)
        if not rows:
            return []

        run_width: dict[str, int] = {}
        for row in rows:
            run_id = row.get("run_id", "")
            run_width[run_id] = run_width.get(run_id, 0) + 1
        base_run = max(run_width, key=lambda run_id: (run_width[run_id], run_id))

        latest: dict[tuple, dict] = {}
        for row in rows:
            if row.get("run_id", "") < base_run:
                continue
            key = (
                row.get("test"),
                row.get("config", "default"),
                row.get("tech"),
                row.get("flow"),
            )
            previous = latest.get(key)
            if previous is None or row.get("run_id", "") >= previous.get("run_id", ""):
                latest[key] = row
        return list(latest.values())

    def hosts(self) -> list[str]:
        return sorted({r.get("host", "unknown") for r in self.load()})

    @staticmethod
    def segment_id(row: dict) -> str:
        """Rows sharing this may be plotted as one series; others may not."""
        versions = row.get("versions", {})
        tools = ",".join(f"{k}={versions.get(k)}" for k in sorted(versions) if k != "lhd")
        return f"{row.get('host_class')}|{tools}"
