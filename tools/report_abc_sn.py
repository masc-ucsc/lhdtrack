#!/usr/bin/env python3
"""Rank outliers from a named full ABC/SN versus fixed LHD ABC regression."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from lhdtrack.report.abc_sn import write_outliers
from lhdtrack.run import host_name


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True)
    parser.add_argument("--host", default=host_name())
    args = parser.parse_args()
    for path in write_outliers(ROOT, args.host, args.run):
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
