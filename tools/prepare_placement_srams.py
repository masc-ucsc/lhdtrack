#!/usr/bin/env python3
"""Generate HighTide-style ASAP7 SRAM views from explicit macro declarations."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from lhdtrack.srams import prepare
from lhdtrack.toolchain import Toolchain

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--config", type=Path, action="append", required=True)
parser.add_argument("--out", type=Path, required=True)
args = parser.parse_args()
tc = prepare(Toolchain.load(ROOT), [p.resolve() for p in args.config], args.out.resolve())
tc.write_snapshot(args.out.resolve())
print(args.out.resolve() / "models.json")
