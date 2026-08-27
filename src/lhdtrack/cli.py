"""`lhdtrack` -- the runner CLI.

    lhdtrack check      lint the corpus
    lhdtrack run        the daily regression
    lhdtrack report     render site/report.html + site/timeseries.html
    lhdtrack show       the same numbers in the terminal
    lhdtrack cache      inspect or clear the baseline cache
    lhdtrack new        scaffold a test directory
    lhdtrack import     seed a test's Pyrope, testbench pair, harness and SDC

Designed to be invoked by hand or from cron on a server; it holds no state
beyond site/ledger.jsonl and var/.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import sys
import tomllib
from pathlib import Path

from .cache import Cache
from .corpus import CorpusError, discover
from .ledger import Ledger
from .report import write_all
from .run import Runner, load_flows, plan
from .toolchain import Toolchain, ToolchainError

C = {
    "ok": "\033[32m", "bad": "\033[31m", "warn": "\033[33m",
    "dim": "\033[2m", "b": "\033[1m", "0": "\033[0m",
}
if not sys.stdout.isatty():
    C = dict.fromkeys(C, "")


def find_root(start: Path | None = None) -> Path:
    p = (start or Path.cwd()).resolve()
    for cand in [p, *p.parents]:
        if (cand / "lhdtrack.toml").exists():
            return cand
    return p


def load_config(root: Path) -> dict:
    path = root / "lhdtrack.toml"
    return tomllib.loads(path.read_text()) if path.exists() else {}


# ------------------------------------------------------------------ check ---
def cmd_check(args, root: Path, cfg: dict) -> int:
    try:
        tests = discover(root, args.test or None)
    except CorpusError as e:
        print(f"{C['bad']}corpus error:{C['0']} {e}")
        return 1
    if not tests:
        print("no tests found under tests/")
        return 0

    # TWO CLASSES, and only one of them fails the lint.
    #
    #   problem  the corpus is MALFORMED and a human must fix it: a missing
    #            licence, a name that does not match its directory, no sources.
    #   note     a TOOL could not do something -- ports no front end could
    #            resolve, so no SDC and no testbench were generated. The runner
    #            already reports that as a skip with the tool's own error.
    #
    # Conflating them made `check` report 78 problems on a corpus with nothing
    # wrong with it, and a lint that is always red is a lint nobody reads.
    problems = 0
    notes = 0

    # Cross-check the SDC unit declared in lhdtrack.toml against the unit the
    # Liberty actually states. They are two independent claims about the same
    # thing, and a silent disagreement scales every period and every delay for
    # that technology by 1000x -- ASAP7 read as ns once, which made 7nm look
    # slower than 130nm.
    try:
        tc = Toolchain.load(root)
    except ToolchainError:
        tc = None
    if tc:
        for tech, spec in sorted((cfg.get("sdc", {}).get("period", {}) or {}).items()):
            declared = spec.get("unit", "ns") if isinstance(spec, dict) else "ns"
            if tech not in tc.techs:
                continue
            actual = tc.tech(tech).time_unit
            if actual and declared != actual:
                print(
                    f"{C['bad']}✗{C['0']} lhdtrack.toml declares [sdc.period.{tech}] in "
                    f"{declared}, but its Liberty states {actual}"
                )
                problems += 1

    for t in tests:
        issues: list[str] = []
        soft: list[str] = []
        if not t.verilog_sources():
            issues.append("no Verilog sources")
        if not t.filelist.exists():
            issues.append("no verilog/filelist.f")
        if t.pyrope_status != "none" and not t.pyrope_top.exists():
            issues.append(f"status.pyrope={t.pyrope_status} but {t.pyrope_top.name} is missing")
        if t.sim_flows and not (t.sim_dir / f"{t.top}_tb.prp").exists():
            soft.append("no generated testbench -- simulation will skip")
        if t.synth_flows:
            techs = cfg.get("run", {}).get("techs", [])
            missing_sdc = [
                tech for tech in techs
                if not (t.root / "constraints" / f"{tech}.sdc").exists() and not t.sdc.exists()
            ]
            if missing_sdc:
                soft.append(
                    f"no constraints for {', '.join(missing_sdc)} -- synthesis will skip"
                )
        if t.sim_flows and t.sim_cycles <= 0:
            issues.append("sim flows declared but [sim].cycles is unset")
        if (
            t.is_combinational and t.sim_flows
            and (t.sim_dir / f"{t.top}_tb.prp").exists()
            and not (t.sim_dir / f"{t.top}_harness.sv").exists()
        ):
            issues.append("combinational test has a testbench but no registered harness")
        if t.pyrope_binding == "monomorphic" and len(t.configs) > 1 and t.pyrope_status != "none":
            issues.append(
                f"{len(t.configs)} configs but [pyrope].param_binding is monomorphic -- "
                "the .prp pins its parameters, so every config would synthesize the same "
                "design and be reported as different measurements"
            )
        if not (t.root / "LICENSE").exists():
            issues.append("no LICENSE (imported sources must carry their upstream licence)")
        if not (t.root / "README.md").exists():
            issues.append("no README.md")

        mark = (
            f"{C['bad']}✗{C['0']}" if issues
            else (f"{C['warn']}~{C['0']}" if soft else f"{C['ok']}✓{C['0']}")
        )
        tags = []
        if t.pyrope_status != "idiomatic":
            tags.append(f"pyrope:{t.pyrope_status}")
        if t.lec_status != "proven":
            tags.append(f"lec:{t.lec_status}")
        suffix = f" {C['dim']}[{', '.join(tags)}]{C['0']}" if tags else ""
        print(f"{mark} {t.name:<28} {t.suite:<6} {t.kind:<14} {len(t.configs)} cfg{suffix}")
        for i in issues:
            print(f"    {C['bad']}·{C['0']} {i}")
            problems += 1
        for i in soft:
            print(f"    {C['dim']}·{C['0']} {i}")
            notes += 1

    comparable = sum(1 for t in tests if t.comparable())
    print(
        f"\n{len(tests)} tests, {comparable} enter the headline comparison "
        f"(idiomatic Pyrope + LEC-proven)"
    )
    print(
        f"{problems} problem(s) — the corpus is malformed; "
        f"{notes} note(s) — a tool could not elaborate a design, and the run reports it as a skip"
    )
    return 1 if problems else 0


# -------------------------------------------------------------------- run ---
def cmd_run(args, root: Path, cfg: dict) -> int:
    try:
        tc = Toolchain.load(root)
    except ToolchainError as e:
        print(f"{C['bad']}{e}{C['0']}")
        return 2
    try:
        tests = discover(root, args.test or None)
    except CorpusError as e:
        print(f"{C['bad']}corpus error:{C['0']} {e}")
        return 1
    if not tests:
        print("no tests found under tests/")
        return 0

    flows = load_flows(root)
    techs = args.tech or cfg.get("run", {}).get("techs", tc.techs)
    jobs = plan(root, tests, tc, flows, techs, args.flow or None)
    if not jobs:
        print("nothing to run (check design.toml flow lists and --flow/--tech filters)")
        return 0

    run_id = _dt.datetime.now().strftime("%Y%m%dT%H%M%S")
    cache = Cache(root, enabled=not args.no_cache, refresh=set(args.refresh or []))
    runner = Runner(root, tc, cache, run_id, cfg, keep_work=args.keep_work)

    print(f"{C['b']}run {run_id}{C['0']} · {len(jobs)} jobs · host {tc.host_class}")
    if missing := [t for t in ("lhd", "yosys", "abc", "verilator", "sta") if not tc.has(t)]:
        print(f"{C['warn']}toolchain missing {', '.join(missing)} — dependent flows will skip{C['0']}")

    done = [0]

    def progress(row):
        done[0] += 1
        mark = {"ok": f"{C['ok']}ok{C['0']}", "failed": f"{C['bad']}FAIL{C['0']}",
                "skipped": f"{C['dim']}skip{C['0']}"}[row.status]
        cached = f" {C['dim']}(cached {row.measured}){C['0']}" if row.cached else ""
        tech = f"/{row.tech}" if row.tech else ""
        print(f"  [{done[0]:>3}/{len(jobs)}] {mark:<16} {row.test}#{row.config}{tech} {row.flow}{cached}")
        if row.note and row.status != "ok":
            print(f"        {C['dim']}{row.note[:160]}{C['0']}")

    rows = runner.execute(jobs, jobs_parallel=int(args.jobs or cfg.get("run", {}).get("jobs", 4)),
                          on_done=progress)

    ledger = Ledger(root)
    n = ledger.append(runner.identity(), rows)
    ledger_path = ledger.path_for(runner.identity()["host"]).relative_to(root)

    failed = sum(1 for r in rows if r.status == "failed")
    skipped = sum(1 for r in rows if r.status == "skipped")
    cached = sum(1 for r in rows if r.cached)
    print(
        f"\n{n} rows appended to {ledger_path} · "
        f"{len(rows)-failed-skipped} ok, {C['bad'] if failed else ''}{failed} failed{C['0']}, "
        f"{skipped} skipped, {cached} reused"
    )

    if not args.no_report:
        for path in write_all(root, cfg=cfg):
            print(f"wrote {path}")
    return 1 if failed else 0


# ----------------------------------------------------------------- report ---
def cmd_report(args, root: Path, cfg: dict) -> int:
    for path in write_all(root, cfg=cfg, only=getattr(args, "host", None)):
        print(f"wrote {path}")
    return 0


def cmd_show(args, root: Path, cfg: dict) -> int:
    from .run import host_name

    rows = Ledger(root).latest_run(getattr(args, "host", None) or host_name())
    if not rows:
        print(f"no runs recorded on {host_name()} yet")
        return 0
    ident = rows[0]
    print(f"{C['b']}run {ident.get('run_id')}{C['0']} · {ident.get('date')} · host {ident.get('host')}")
    hdr = f"{'test':<24}{'cfg':<10}{'tech':<8}{'flow':<22}{'area':>10}{'ns':>9}{'time s':>9}{'mem MB':>9}"
    print(f"\n{C['dim']}{hdr}{C['0']}")
    for r in sorted(rows, key=lambda r: (r["test"], r.get("tech") or "", r["flow"])):
        if r.get("status") != "ok":
            state = f"{C['bad']}FAIL{C['0']}" if r["status"] == "failed" else f"{C['dim']}skip{C['0']}"
            print(f"{r['test']:<24}{r.get('config',''):<10}{r.get('tech') or '':<8}{r['flow']:<22}{state}")
            continue
        area = r.get("qor", {}).get("area_um2") or 0
        ns = r.get("sta", {}).get("opensta_ns") or r.get("sta", {}).get("opentimer_ns") or 0
        secs = r.get("time_ms", {}).get("total", 0) / 1000
        mem = r.get("peak_rss_kb", {}).get("max", 0) / 1024
        star = "*" if r.get("cached") else " "
        print(
            f"{r['test']:<24}{r.get('config',''):<10}{r.get('tech') or '':<8}{r['flow']:<22}"
            f"{area:>10,.1f}{ns:>9.3f}{secs:>9.1f}{mem:>9.0f}{star}"
        )
    print(f"\n{C['dim']}* reused from the baseline cache{C['0']}")
    return 0


# ------------------------------------------------------------------ cache ---
def cmd_cache(args, root: Path, cfg: dict) -> int:
    cache = Cache(root)
    if args.clear:
        n = cache.clear(None if args.clear == "all" else args.clear)
        print(f"cleared {n} cached results")
        return 0
    stats = cache.stats()
    if not stats:
        print("baseline cache is empty")
        return 0
    for flow, n in stats.items():
        print(f"{flow:<24} {n} entries")
    return 0


# -------------------------------------------------------------------- new ---
def cmd_new(args, root: Path, cfg: dict) -> int:
    sys.path.insert(0, str(root / "tools"))
    from scaffold import new_test  # noqa: PLC0415

    path = new_test(root, args.name, top=args.top or args.name, kind=args.kind, suite=args.suite)
    print(f"created {path}\nnext: drop SystemVerilog into {path/'verilog'}/, then `lhdtrack import seed {args.name}`")
    return 0


def cmd_import(args, root: Path, cfg: dict) -> int:
    sys.path.insert(0, str(root / "tools"))
    from scaffold import seed_test  # noqa: PLC0415

    try:
        tc = Toolchain.load(root)
    except ToolchainError as e:
        print(f"{C['bad']}{e}{C['0']}")
        return 2
    tests = discover(root, args.test or None)
    rc = 0
    for t in tests:
        try:
            made = seed_test(root, t, tc, cfg, force=args.force)
            print(f"{C['ok']}✓{C['0']} {t.name}: " + ", ".join(made) if made else f"  {t.name}: up to date")
        # ONLY the refusals seed_test raises DELIBERATELY. Catching bare
        # `Exception` here once meant any bug in the generator -- a NameError, a
        # bad index -- was swallowed and treated as "this design cannot be
        # seeded". A real bug must crash.
        except (RuntimeError, FileNotFoundError) as e:
            # The manifest is NOT touched. AGENTS.md invariant 2: coverage is a
            # property of the corpus, a tool limitation is a property of the
            # run -- and every flow already skips with the tool's own error,
            # which is the honest record. Rewriting the manifest here destroyed
            # that record and made a tool's limit look like something the test
            # was never meant to do.
            print(f"{C['warn']}·{C['0']} {t.name}: not seeded — {e}")
            rc = 1
    return rc


# ------------------------------------------------------------------- main ---
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="lhdtrack", description=__doc__.splitlines()[0])
    p.add_argument("-C", "--root", type=Path, help="repository root (default: search upward)")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("check", help="lint the corpus")
    c.add_argument("test", nargs="*")
    c.set_defaults(fn=cmd_check)

    r = sub.add_parser("run", help="run the regression")
    r.add_argument("--test", action="append", help="limit to these tests")
    r.add_argument("--tech", action="append", help="limit to these technologies")
    r.add_argument("--flow", action="append", help="limit to these flows")
    r.add_argument("--refresh", action="append", help="force a baseline flow to re-measure")
    r.add_argument("--no-cache", action="store_true", help="ignore the baseline cache entirely")
    r.add_argument("--no-report", action="store_true")
    r.add_argument("--keep-work", action="store_true", help="keep netlists and build trees")
    r.add_argument("-j", "--jobs", type=int)
    r.set_defaults(fn=cmd_run)

    rp = sub.add_parser("report", help="render the HTML")
    rp.add_argument("--host", help="render another machine's page (default: this one)")
    rp.set_defaults(fn=cmd_report)
    sh = sub.add_parser("show", help="print the last run")
    sh.add_argument("--host")
    sh.set_defaults(fn=cmd_show)

    ca = sub.add_parser("cache", help="inspect or clear the baseline cache")
    ca.add_argument("--clear", metavar="FLOW|all")
    ca.set_defaults(fn=cmd_cache)

    n = sub.add_parser("new", help="scaffold a test directory")
    n.add_argument("name")
    n.add_argument("--top")
    n.add_argument("--kind", choices=("combinational", "sequential"), default="sequential")
    n.add_argument("--suite", default="micro")
    n.set_defaults(fn=cmd_new)

    i = sub.add_parser("import", help="seed generated files for a test")
    i.add_argument("what", choices=("seed",))
    i.add_argument("test", nargs="*")
    i.add_argument("--force", action="store_true", help="regenerate files that already exist")
    i.set_defaults(fn=cmd_import)

    args = p.parse_args(argv)
    root = (args.root or find_root()).resolve()
    return args.fn(args, root, load_config(root))


if __name__ == "__main__":
    raise SystemExit(main())
