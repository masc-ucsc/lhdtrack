"""Post-process a machine-emitted Pyrope tree so `lhd` can read back what it wrote.

`lhd compile verilog --emit-dir pyrope:` names a Pyrope net after the Verilog net
it came from. When that name is a Pyrope KEYWORD the emitter has to backtick it,
and its escape list is incomplete: `in` comes out as `` `in` ``, but `stage` --
bedrock's name for a delay-line register -- comes out bare, and the reader then
parses `stage[0] = x` as the `mod`-only `stage[N]` declaration modifier and
reports `expected an expression` at the `=`. One emitted br_delay_valid.prp
blocked LEC on 22 tests this way.

This is an lhd emitter bug, not a corpus fact, so the fix is mechanical and
reversible: escape the identifier, change nothing else. LEC then proves the
result against the same Verilog, which is what makes the rewrite safe to trust
rather than merely plausible. Delete this file once lhd's writer escapes the
full keyword set.

    python3 tools/prp_fixups.py                 # every test
    python3 tools/prp_fixups.py br_delay_valid

`scaffold.seed_test` runs it on every tree it emits, so a re-seed self-heals.

The sibling `manifest.json` keeps the emitter's own `content_hash`, which the
rewrite makes stale. That is deliberate: nothing in lhdtrack reads it, and `lhd
compile` demonstrably reads the file itself -- the 22 tests this unblocked went
from a syntax error to a proven equivalence with no other change. Recomputing a
hash whose algorithm is the emitter's private business would be the more
fragile choice.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Pyrope keywords the emitter does NOT escape but which are legal Verilog net
# names. Only add a word here after seeing `lhd compile` reject the emission:
# every entry makes a generated file differ from what the tool wrote.
RESERVED = ("stage",)

# An identifier, not a field (`x.stage`), not already escaped, not a prefix
# (`stage_valid`, `out_stages`).
_IDENT = {w: re.compile(rf"(?<![`\w.]){w}(?![`\w])") for w in RESERVED}


def fix_text(text: str) -> str:
    """Backtick-escape reserved words, leaving comments alone."""
    out = []
    for line in text.split("\n"):
        code, sep, comment = line.partition("//")
        for word, pat in _IDENT.items():
            code = pat.sub(f"`{word}`", code)
        out.append(code + sep + comment)
    return "\n".join(out)


def fix_dir(pyrope_dir: Path) -> list[Path]:
    """Rewrite every .prp under `pyrope_dir` that needs it. Returns what changed."""
    changed = []
    for prp in sorted(pyrope_dir.glob("*.prp")):
        text = prp.read_text()
        fixed = fix_text(text)
        if fixed != text:
            prp.write_text(fixed)
            changed.append(prp)
    return changed


def main() -> int:
    names = sys.argv[1:] or sorted(p.name for p in (ROOT / "tests").iterdir() if p.is_dir())
    total = 0
    for name in names:
        d = ROOT / "tests" / name / "pyrope"
        if not d.is_dir():
            continue
        for p in fix_dir(d):
            print(f"  escaped {p.relative_to(ROOT)}")
            total += 1
    print(f"{total} file(s) rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
