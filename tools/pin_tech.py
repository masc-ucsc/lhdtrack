#!/usr/bin/env python3
"""Fill in the sha256 of a pin in MODULE.bazel.

    tools/pin_tech.py --update sky130
    tools/pin_tech.py --update all

Bazel will fetch an http_archive with an empty sha256 and merely warn. That is
the wrong default here: an unpinned Liberty means today's area number and last
month's were measured against files that may differ, with nothing in the ledger
recording it. This downloads each archive, hashes it, and writes the digest
back, so a pin is either correct or absent -- never silently loose.
"""

from __future__ import annotations

import argparse
import hashlib
import re
import sys
import urllib.request
from pathlib import Path

MODULE = Path(__file__).resolve().parents[1] / "MODULE.bazel"
BLOCK = re.compile(
    r'http_archive\(\s*\n\s*name = "(?P<name>[^"]+)".*?'
    r'sha256 = "(?P<sha>[^"]*)".*?'
    r'urls = \["(?P<url>[^"]+)"\]',
    re.S,
)
ALIASES = {"sky130": "sky130_fd_sc_hd", "asap7": "asap7sc7p5t", "opensta": "opensta"}


def sha256_of(url: str) -> str:
    h = hashlib.sha256()
    with urllib.request.urlopen(url, timeout=300) as r:  # noqa: S310 -- pinned https URLs only
        for chunk in iter(lambda: r.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--update", required=True, help="sky130 | asap7 | opensta | all")
    args = ap.parse_args()

    text = MODULE.read_text()
    wanted = None if args.update == "all" else ALIASES.get(args.update, args.update)

    changed = 0
    for m in BLOCK.finditer(text):
        name, url = m.group("name"), m.group("url")
        if wanted and name != wanted:
            continue
        if "/heads/" in url or "/main." in url:
            print(f"! {name}: url points at a BRANCH, not a commit -- pin a sha first:\n  {url}")
        print(f"  {name}: hashing {url}")
        try:
            digest = sha256_of(url)
        except OSError as e:
            print(f"✗ {name}: {e}", file=sys.stderr)
            continue
        text = text[: m.start("sha")] + digest + text[m.end("sha") :]
        print(f"✓ {name}: {digest}")
        changed += 1

    if changed:
        MODULE.write_text(text)
    print(f"\n{changed} pin(s) updated in {MODULE.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
