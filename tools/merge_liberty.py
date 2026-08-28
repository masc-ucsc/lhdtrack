#!/usr/bin/env python3
"""Merge split Liberty cell families into one deterministic library."""

from __future__ import annotations

import argparse
import gzip
import re
from pathlib import Path


_GROUP_HEADER = re.compile(r"([A-Za-z_]\w*)\s*(?:\(([^{};]*)\))?\s*$")
_KEEP_GROUPS = {
    "cell",
    "lu_table_template",
    "output_current_template",
    "power_lut_template",
}


def _read(path: Path) -> str:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", errors="replace") as stream:
            return stream.read()
    return path.read_text(errors="replace")


def _structural(text: str):
    """Yield (offset, character) outside comments and quoted strings."""
    i = 0
    while i < len(text):
        if text.startswith("/*", i):
            end = text.find("*/", i + 2)
            i = len(text) if end < 0 else end + 2
            continue
        if text.startswith("//", i):
            end = text.find("\n", i + 2)
            i = len(text) if end < 0 else end + 1
            continue
        if text[i] == '"':
            i += 1
            while i < len(text):
                if text[i] == "\\":
                    i += 2
                elif text[i] == '"':
                    i += 1
                    break
                else:
                    i += 1
            continue
        if text[i] in "{}":
            yield i, text[i]
        i += 1


def _library_bounds(text: str) -> tuple[int, int]:
    depth = 0
    opening = -1
    for offset, char in _structural(text):
        if char == "{":
            if opening < 0:
                opening = offset
            depth += 1
        else:
            depth -= 1
            if opening >= 0 and depth == 0:
                return opening, offset
    raise ValueError("input has no balanced top-level library group")


def _top_groups(text: str) -> list[tuple[tuple[str, str], str]]:
    opening, closing = _library_bounds(text)
    groups = []
    depth = 0
    group_start = -1
    header = None
    boundary = opening + 1
    for offset, char in _structural(text[opening + 1 : closing]):
        offset += opening + 1
        if char == "{":
            if depth == 0:
                match = _GROUP_HEADER.search(text[boundary:offset])
                if match:
                    group_start = boundary + match.start()
                    header = (match.group(1), " ".join((match.group(2) or "").split()))
            depth += 1
        else:
            depth -= 1
            if depth == 0:
                if header is not None and group_start >= 0:
                    groups.append((header, text[group_start : offset + 1]))
                header = None
                group_start = -1
                boundary = offset + 1
        if depth == 0 and char == "}":
            boundary = offset + 1
    return groups


def merge(inputs: list[Path]) -> str:
    if not inputs:
        raise ValueError("at least one input Liberty is required")
    texts = [_read(path) for path in inputs]
    base = texts[0]
    opening, closing = _library_bounds(base)
    prefix = re.sub(
        r"\blibrary\s*\([^)]*\)",
        "library (asap7sc7p5t_RVT_TT_merged)",
        base[: opening + 1],
        count=1,
    )

    seen = {signature for signature, _ in _top_groups(base)}
    additions = []
    for path, text in zip(inputs[1:], texts[1:]):
        for signature, group in _top_groups(text):
            if signature[0] not in _KEEP_GROUPS or signature in seen:
                continue
            seen.add(signature)
            additions.append(f"\n\n  /* merged from {path.name} */\n" + group.rstrip())

    provenance = "\n  /* merged inputs: " + ", ".join(path.name for path in inputs) + " */\n"
    return prefix + provenance + base[opening + 1 : closing].rstrip() + "".join(additions) + "\n}\n"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("inputs", nargs="+", type=Path)
    args = parser.parse_args()
    args.output.write_text(merge(args.inputs))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
