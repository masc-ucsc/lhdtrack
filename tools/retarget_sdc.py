#!/usr/bin/env python3
"""Retarget generated benchmark SDCs from the latest measured STA results.

ASAP7 gets the closest still-unmet target in 100 ps steps, capped at 400 ps.
The comparison uses the fastest current Yosys/LiveHD-Verilog result so neither
flow already meets the selected target. Sky130 gets one deliberately loose
period shared by the corpus.
"""

from __future__ import annotations

import argparse
import json
import re
import socket
import tomllib
from collections import Counter
from pathlib import Path

GENERATED = "# lhdtrack-generated"
ASAP7_TARGETS = (100.0, 200.0, 300.0, 400.0)
REFERENCE_FLOWS = ("syn_yosys_abc", "syn_lhd_verilog")
NUMBER = r"[0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?"


def _latest_delays(ledger: Path) -> dict[tuple[str, str, str], float]:
    latest: dict[tuple[str, str, str], dict] = {}
    with ledger.open() as rows:
        for line in rows:
            if not line.strip():
                continue
            row = json.loads(line)
            if row.get("kind") != "synth" or row.get("tech") != "asap7":
                continue
            flow = row.get("flow")
            if flow not in REFERENCE_FLOWS:
                continue
            latest[(row.get("test", ""), row.get("config", ""), flow)] = row

    out = {}
    for key, row in latest.items():
        value = row.get("sta", {}).get("opensta_ns")
        if isinstance(value, (int, float)) and value > 0:
            out[key] = float(value)
    return out


def _configs(manifest: Path) -> set[str]:
    doc = tomllib.loads(manifest.read_text())
    return {str(c["id"]) for c in doc.get("config", [])} or {"default"}


def _asap7_target(observed: float | None) -> float:
    if observed is None:
        return ASAP7_TARGETS[0]
    unmet = [target for target in ASAP7_TARGETS if target < observed]
    return max(unmet, default=ASAP7_TARGETS[0])


def _rewrite(path: Path, period: float, unit: str, dry_run: bool) -> bool:
    if not path.exists() or GENERATED not in (text := path.read_text(errors="replace")):
        return False
    updated, comments = re.subn(
        rf"(?m)^# Period {NUMBER} \S+ --",
        f"# Period {period:g} {unit} --",
        text,
        count=1,
    )
    updated, clocks = re.subn(
        rf"(?m)^(\s*create_clock\b[^\n]*?\s-period\s+){NUMBER}",
        lambda match: f"{match.group(1)}{period:g}",
        updated,
        count=1,
    )
    if comments != 1 or clocks != 1:
        raise RuntimeError(f"{path}: generated SDC has no recognizable period")
    if updated == text:
        return False
    if not dry_run:
        path.write_text(updated)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ledger", type=Path)
    parser.add_argument("--sky130-period", type=float, default=20.0, metavar="NS")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    root = args.root.resolve()
    host = socket.gethostname().split(".", 1)[0]
    ledger = args.ledger or root / "data" / f"ledger-{host}.jsonl"
    if not ledger.exists():
        raise SystemExit(f"ledger not found: {ledger}")
    delays = _latest_delays(ledger)

    changed = 0
    bins: Counter[int] = Counter()
    missing = []
    print("test\tobserved_fastest_ps\tasap7_target_ps\tsky130_target_ns")
    for manifest in sorted((root / "tests").glob("*/design.toml")):
        test = manifest.parent.name
        configs = _configs(manifest)
        observed_values = [
            value
            for (row_test, config, _flow), value in delays.items()
            if row_test == test and config in configs
        ]
        observed = min(observed_values, default=None)
        target = _asap7_target(observed)
        bins[int(target)] += 1

        asap_sdc = manifest.parent / "constraints" / "asap7.sdc"
        sky_sdc = manifest.parent / "constraints" / "sky130.sdc"
        if not asap_sdc.exists() or not sky_sdc.exists():
            missing.append(test)
            continue
        changed += int(_rewrite(asap_sdc, target, "ps", args.dry_run))
        changed += int(_rewrite(sky_sdc, args.sky130_period, "ns", args.dry_run))
        observed_text = "n/a" if observed is None else f"{observed:g}"
        print(f"{test}\t{observed_text}\t{target:g}\t{args.sky130_period:g}")

    mode = "would change" if args.dry_run else "changed"
    print(f"{mode} {changed} generated SDC file(s)")
    print("ASAP7 targets: " + ", ".join(f"{key}ps={bins[key]}" for key in sorted(bins)))
    if missing:
        print(f"no generated SDC pair for {len(missing)} design(s): {', '.join(missing)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
