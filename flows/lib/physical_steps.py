#!/usr/bin/env python3
"""Run resolved physical tools and export portable views in the measured interval."""
from __future__ import annotations

import json
from pathlib import Path
import shutil
import subprocess
import sys
import time


def main() -> int:
    spec = json.loads(Path(sys.argv[1]).read_text())
    phases = []
    for step in spec["steps"]:
        start = time.monotonic()
        result = subprocess.run(step["argv"], check=False)
        phases.append({"name": step["name"], "ms": (time.monotonic() - start) * 1000,
                       "exit_code": result.returncode})
        Path(spec["phases"]).write_text(json.dumps(phases, indent=2) + "\n")
        if result.returncode:
            return result.returncode
    directory = Path(spec["physical"])
    (directory / "lef").mkdir(parents=True, exist_ok=True)
    lefs = []
    for index, source in enumerate(spec["lefs"]):
        name = f"lef/{index}_{Path(source).name}"
        shutil.copy2(source, directory / name)
        lefs.append(name)
    lefs.append("cells.lef")
    (directory / "lefs.json").write_text(json.dumps(lefs, indent=2) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
