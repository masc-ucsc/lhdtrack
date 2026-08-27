"""Baseline reuse.

`syn_yosys_abc` and `sim_verilator` read through `var/cache/`; LiveHD flows never
do. That asymmetry is deliberate: the lhd SHA moves daily so a LiveHD key would
always miss, and re-measuring LiveHD is the entire point of the tracker.

A cached entry records `measured`, the date the number was actually taken, and
the report displays it. Without that a six-week-old verilator number silently
becomes today's comparison point with nothing on the page saying so -- the
result still being *valid* (the key says the inputs are unchanged) is exactly
what makes it easy to miss.
"""

from __future__ import annotations

import datetime as _dt
import json
import shutil
from pathlib import Path


class Cache:
    def __init__(self, root: Path, enabled: bool = True, refresh: set[str] | None = None):
        self.dir = root / "var" / "cache"
        self.enabled = enabled
        self.refresh = refresh or set()

    def _entry(self, flow: str, key: str) -> Path:
        return self.dir / flow / key

    def get(self, flow: str, key: str) -> dict | None:
        if not self.enabled or flow in self.refresh:
            return None
        path = self._entry(flow, key) / "result.json"
        if not path.exists():
            return None
        try:
            doc = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            return None
        doc["cached"] = True
        return doc

    def put(self, flow: str, key: str, result: dict, logs: Path | None = None) -> None:
        if not self.enabled:
            return
        entry = self._entry(flow, key)
        entry.mkdir(parents=True, exist_ok=True)
        doc = dict(result)
        doc["cached"] = False
        doc.setdefault("measured", _dt.date.today().isoformat())
        (entry / "result.json").write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
        # Logs travel with the entry: a reused baseline whose logs were thrown
        # away cannot be diagnosed when it later disagrees with a fresh run.
        if logs and logs.is_dir():
            dest = entry / "logs"
            shutil.rmtree(dest, ignore_errors=True)
            shutil.copytree(logs, dest)

    # -- housekeeping ------------------------------------------------------
    def stats(self) -> dict[str, int]:
        out: dict[str, int] = {}
        if not self.dir.is_dir():
            return out
        for flow_dir in sorted(self.dir.iterdir()):
            if flow_dir.is_dir():
                out[flow_dir.name] = sum(1 for _ in flow_dir.glob("*/result.json"))
        return out

    def clear(self, flow: str | None = None) -> int:
        target = self.dir / flow if flow else self.dir
        if not target.exists():
            return 0
        n = sum(1 for _ in target.glob("**/result.json"))
        shutil.rmtree(target)
        return n

    def age_days(self, doc: dict) -> int | None:
        measured = doc.get("measured")
        if not measured:
            return None
        try:
            return (_dt.date.today() - _dt.date.fromisoformat(measured)).days
        except ValueError:
            return None
