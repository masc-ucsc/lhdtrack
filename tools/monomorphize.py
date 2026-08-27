"""Pin every test's TOP module to one parameter set, in the source.

WHY THE TOP CANNOT BE PARAMETERIZED. Every consumer of a test elaborates the
same top module, and each one spells a parameter override differently:

    yosys       hierarchy -chparam NumRequesters 16
    slang/lhd   -GNumRequesters=16
    verilator   -GNumRequesters=16
    harness     br_arb_fixed #(.NumRequesters(16)) dut (...)
    Pyrope      nothing at all -- a .prp has no parameters

Five spellings of one fact, and the Pyrope side cannot spell it. So the moment
any of them is dropped -- a flow that forgets `-G`, a harness regenerated from a
different config -- the two sides of the equivalence check are two different
circuits, and LEC reports a refutation that says nothing about either language.

Pinning the parameters IN THE SOURCE removes the whole class. `parameter`
becomes `localparam` in the top's parameter port list, keeping the exact value
design.toml already chose, so the module elaborates identically with no flags
anywhere and the .prp's fixed widths are the same widths by construction.

INTERMEDIATE MODULES KEEP THEIR PARAMETERS. They are instantiated with explicit
overrides by the top, which is what makes them reusable; it is only the boundary
that the outside world must agree on.

    python3 tools/monomorphize.py            # every test
    python3 tools/monomorphize.py br_arb_fixed add
    python3 tools/monomorphize.py --dry-run

Re-running is safe: a top whose parameter port list holds only localparams is
already pinned and is left alone.
"""

from __future__ import annotations

import argparse
import re
import sys
import textwrap
import tomllib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from svparam import TopModule, freeze, match_paren, strip_comments  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent

# What replaces the `params = {...}` line once the source carries the values.
_NOTE_HEAD = """\
NO PARAMETERS. verilog/{top}.sv declares its parameter port list `localparam`, so
the top elaborates to ONE circuit with no flags anywhere -- no -chparam for yosys,
no -G for slang or verilator, no `#(...)` on the harness instance -- and the .prp,
which cannot take a parameter at all, matches it by construction. Intermediate
modules stay parameterized; the top overrides them."""

_NOTE_CHOSEN = """\
THE CHOSEN POINT IS {pinned}. To move it, edit the localparam in the .sv and
re-seed, so the harness and the emitted Pyrope follow -- not this line."""

_NOTE_DEFAULTS = """\
THE CHOSEN POINT IS the module's own defaults. To move it, edit the localparam in
the .sv and re-seed, so the harness and the emitted Pyrope follow -- not this line."""


def pin_test(test_dir: Path, dry_run: bool = False) -> str:
    """Freeze one test's top. Returns a one-line report."""
    doc = tomllib.loads((test_dir / "design.toml").read_text())
    top = doc["design"]["top"]
    src = test_dir / "verilog" / f"{top}.sv"
    if not src.exists():
        return f"skip: no verilog/{top}.sv"

    text = src.read_text()
    try:
        mod = TopModule(text, top)
    except ValueError as e:
        return f"skip: {e}"

    def strip() -> str:
        """Repair harnesses, and say so. Only ever called once the top is pinned:
        removing a `#(...)` from a still-parameterized top would leave the harness
        elaborating the module's defaults instead of this test's point."""
        n = 0 if dry_run else len(_strip_harness_overrides(test_dir / "sim", top))
        return f"; stripped #() from {n} harness(es)" if n else ""

    items = mod.items() if mod.has_param_list else []
    overridable = [it for it in items if it.kw == "parameter"]
    if not overridable:
        # Already pinned by an earlier run -- but a harness the generator refuses
        # to rewrite can still be carrying a stale override, so repair anyway.
        return ("already parameter-free" if not items else "already pinned") + strip()

    configs = doc.get("config", [])
    if len(configs) > 1:
        # Two configs are two different circuits from one source; pinning would
        # silently collapse them into the same measurement reported twice.
        return f"REFUSED: {len(configs)} configs -- pick one before pinning"
    chosen = dict(configs[0].get("params", {})) if configs else {}
    unknown = sorted(set(chosen) - {it.name for it in items})
    if unknown:
        return f"REFUSED: config sets {unknown}, which {top} does not declare"
    missing = [it.name for it in overridable if it.value_span is None]
    if missing:
        return f"REFUSED: {missing} have no default and no config value"

    new_text, pinned = freeze(text, top, chosen)
    kept = {k: v for k, v in pinned.items() if k in chosen}
    fixed = ""
    if not dry_run:
        src.write_text(new_text)
        _clear_params(test_dir / "design.toml", top, kept)
        fixed = strip()
    shown = ", ".join(f"{k}={v}" for k, v in sorted(kept.items())) or "upstream defaults"
    return f"pinned {len(overridable)} parameter(s) ({shown}){fixed}"


def _strip_harness_overrides(sim_dir: Path, top: str) -> list[Path]:
    """Drop `#(...)` from a harness instance of a now-parameter-free top.

    The generator emits the override from design.toml, so an emptied `params`
    already removes it on the next seed -- but a test whose harness the generator
    REFUSES to rewrite (a duplicate pin it will not guess its way around) keeps a
    stale one, and `#(.MaxCredit(16))` on a localparam is a hard verilator error:
    "attempts to override 'MaxCredit' as a parameter, but it is a local
    parameter". The values there are the ones just pinned, so deleting the block
    changes nothing but whether the file compiles.
    """
    touched = []
    for path in sorted(sim_dir.glob("*_harness.sv")) if sim_dir.is_dir() else []:
        text = path.read_text()
        m = re.search(rf"\b{re.escape(top)}\s*#\s*\(", text)
        if not m:
            continue
        blank = strip_comments(text)
        close = match_paren(blank, blank.index("(", m.end() - 1))
        new = text[:m.start()] + top + text[close + 1:]
        path.write_text(new)
        touched.append(path)
    return touched


def _clear_params(toml: Path, top: str, pinned: dict[str, str]) -> None:
    """Empty `[[config]].params` -- the source is now the single statement."""
    s = toml.read_text()
    values = ", ".join(f"{k}={v}" for k, v in sorted(pinned.items()))
    body = _NOTE_HEAD.format(top=top) + "\n\n"
    body += _NOTE_CHOSEN.format(pinned=values) if values else _NOTE_DEFAULTS
    note = "\n".join(
        ("# " + line).rstrip() for para in body.split("\n\n")
        for line in textwrap.wrap(" ".join(para.split()), 76, break_on_hyphens=False) + [""]
    ).rstrip("\n#").rstrip() + "\nparams = {}"
    new = re.sub(r"^params\s*=\s*\{.*\}[ \t]*$", lambda _m: note, s, count=1, flags=re.M)
    if new != s:
        toml.write_text(new)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("tests", nargs="*", help="test names; default every test")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    names = args.tests or sorted(p.name for p in (ROOT / "tests").iterdir() if p.is_dir())
    refused = 0
    for name in names:
        d = ROOT / "tests" / name
        if not (d / "design.toml").exists():
            print(f"  {name:<52} skip: no design.toml")
            continue
        report = pin_test(d, dry_run=args.dry_run)
        if report.startswith("REFUSED"):
            refused += 1
        print(f"  {name:<52} {report}")
    return 1 if refused else 0


if __name__ == "__main__":
    raise SystemExit(main())
