"""Shared importer machinery.

An importer's job is to make a test directory that is INDEPENDENT of the
upstream checkout: sources copied in, `include`d headers copied beside them,
licence carried verbatim, and the exact revision recorded in both README.md and
design.toml. A daily regression that reaches into a sibling git tree is a
regression that changes when somebody else runs `git pull`.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

INCLUDE_RE = re.compile(r'^\s*`include\s+"([^"]+)"', re.M)


def git_rev(repo: Path) -> str:
    try:
        out = subprocess.run(  # noqa: S603
            ["git", "-C", str(repo), "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True, timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, OSError):
        return "unknown"


def resolve_includes(source: Path, search: list[Path], seen: set[Path] | None = None) -> list[Path]:
    """Every header `source` pulls in, transitively.

    They must travel with the source: slang resolves an include relative to the
    including file while verilator only searches its -I list, so headers left
    behind in the upstream tree make the two front ends see different files --
    and the whole comparison assumes they see the same ones.
    """
    seen = seen if seen is not None else set()
    found: list[Path] = []
    try:
        text = source.read_text(errors="replace")
    except OSError:
        return found
    for name in INCLUDE_RE.findall(text):
        for d in search:
            cand = d / name
            if cand.exists() and cand not in seen:
                seen.add(cand)
                found.append(cand)
                found.extend(resolve_includes(cand, search, seen))
                break
    return found


def stage(dest: Path, sources: list[Path], headers: list[Path], licence: Path | None) -> list[str]:
    """Copy sources + headers into `dest/verilog/` and write filelist.f."""
    vdir = dest / "verilog"
    vdir.mkdir(parents=True, exist_ok=True)
    # bedrock's closure spans many packages, and two of them can hold a file of
    # the same name. Copying by basename let the second one overwrite the first
    # silently -- the filelist still named it once and the test compiled against
    # a source nobody chose.
    claimed: dict[str, Path] = {}
    for f in [*sources, *headers]:
        prior = claimed.get(f.name)
        if prior is not None and prior != f:
            raise FileExistsError(
                f"two different upstream files are both named {f.name}: {prior} and {f}"
            )
        claimed[f.name] = f
        shutil.copy(f, vdir / f.name)
    if licence and licence.exists():
        shutil.copy(licence, dest / "LICENSE")

    header_note = ""
    if headers:
        header_note = (
            "# Macro headers travel with the source: slang resolves an include relative\n"
            "# to the including file, verilator only searches its -I list, and both front\n"
            "# ends must see the same files.\n"
        )
    (vdir / "filelist.f").write_text(
        "# One source per line, in compile order. Read by slang, verilator and yosys.\n"
        + header_note
        + "".join(f"{f.name}\n" for f in sources)
    )
    return [f.name for f in [*sources, *headers]]


def write_provenance(dest: Path, upstream: str, rev: str, path: str, licence: str) -> None:
    """Stamp the exact revision into design.toml, replacing the empty block."""
    toml = dest / "design.toml"
    s = toml.read_text()
    s = re.sub(r'upstream = ""', f'upstream = "{upstream}"', s, count=1)
    s = re.sub(r'rev      = ""', f'rev      = "{rev}"', s, count=1)
    s = re.sub(r'path     = ""', f'path     = "{path}"', s, count=1)
    s = re.sub(r'license  = ""', f'license  = "{licence}"', s, count=1)
    toml.write_text(s)
