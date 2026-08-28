"""The scheduler: build the job list, reuse baselines, measure, then gate.

A flow MEASURES; run.py DECIDES. Every gate lives here so one policy applies to
all of them instead of each flow inventing its own notion of "passed":

  checksum agreement  all three simulators must fold the same value
  STA correlation     LiveHD OpenTimer vs OpenSTA, past gates.sta_delta_pct_max
  LEC drift           a manifest claiming `proven` while the prover disagrees
  stale baseline      a reused number older than cache.stale_after_days

The checksum gate is cross-flow, so it can only be applied once every sim row of
a (test, config) exists -- which is why gating is a pass over the finished rows
rather than something a flow does to itself.
"""

from __future__ import annotations

import datetime as _dt
import importlib.util
import json
import os
import platform
import shutil
import sys
import traceback
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from types import ModuleType

from .cache import Cache
from .context import FlowContext, FlowError, FlowSkip
from .corpus import Config, Test
from .keys import cache_key
from .toolchain import Toolchain

FLOWS_DIR = "flows"


def host_name() -> str:
    """`uname -n` -- the machine identity every row is stamped with."""
    try:
        return os.uname().nodename
    except AttributeError:  # non-POSIX
        return platform.node() or "unknown"


@dataclass
class Job:
    test: Test
    config: Config
    tech: str | None
    flow: str
    module: ModuleType
    flow_file: Path

    @property
    def label(self) -> str:
        parts = [self.test.name, self.config.id, self.flow]
        if self.tech:
            parts.insert(2, self.tech)
        return "/".join(parts)


@dataclass
class Row:
    """One ledger row: a single (test, config, tech, flow) measurement."""

    test: str
    config: str
    suite: str
    tech: str | None
    flow: str
    kind: str
    passed: bool
    cached: bool
    measured: str
    status: str = "ok"  # ok | failed | skipped
    note: str = ""
    pyrope_status: str = "none"
    lec: str = "none"
    comparable: bool = False
    qor: dict = field(default_factory=dict)
    sta: dict = field(default_factory=dict)
    # The equivalence measurement. Distinct from `lec`, which is the status the
    # MANIFEST declares -- keeping them apart is what lets the runner notice
    # the two disagreeing.
    lec_result: dict = field(default_factory=dict)
    # A second, independently named netlist obligation may share the same
    # expensive mapped design with `lec_result`.
    lec_aux_result: dict = field(default_factory=dict)
    sim: dict = field(default_factory=dict)
    time_ms: dict = field(default_factory=dict)
    peak_rss_kb: dict = field(default_factory=dict)
    cmds: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if v not in ({}, [], "")}


# ------------------------------------------------------------ flow loading --
def load_flows(root: Path) -> dict[str, tuple[ModuleType, Path]]:
    """Import every `flows/*.py` as a module.

    `flows/` goes on sys.path so a flow can `from lib.simgate import ...` and
    `from qor_endpoint import ...` -- flows are peers of each other, not of the
    lhdtrack package, which keeps the recipe layer editable without touching
    the runner.
    """
    flows_dir = root / FLOWS_DIR
    if str(flows_dir) not in sys.path:
        sys.path.insert(0, str(flows_dir))

    out: dict[str, tuple[ModuleType, Path]] = {}
    for path in sorted(flows_dir.glob("*.py")):
        if path.stem.startswith("_") or path.stem == "qor_endpoint":
            continue
        spec = importlib.util.spec_from_file_location(path.stem, path)
        if spec is None or spec.loader is None:
            continue
        mod = importlib.util.module_from_spec(spec)
        sys.modules[path.stem] = mod
        spec.loader.exec_module(mod)
        if hasattr(mod, "run") and hasattr(mod, "NAME"):
            out[mod.NAME] = (mod, path)
    return out


# ---------------------------------------------------------------- planning --
def plan(
    root: Path,
    tests: list[Test],
    tc: Toolchain,
    flows: dict[str, tuple[ModuleType, Path]],
    techs: list[str],
    only_flows: list[str] | None = None,
) -> list[Job]:
    jobs: list[Job] = []
    for test in tests:
        wanted = list(dict.fromkeys([*test.synth_flows, *test.sim_flows, *test.lec_flows]))
        for name in wanted:
            if only_flows and name not in only_flows:
                continue
            if name not in flows:
                continue
            mod, path = flows[name]
            for cfg in test.configs:
                if getattr(mod, "USES_TECH", False):
                    # Every REQUESTED tech gets a job, staged or not. Filtering
                    # here would make an unstaged Liberty look like a flow the
                    # test never declared -- a blank where a skip belongs.
                    jobs.extend(Job(test, cfg, t, name, mod, path) for t in techs)
                else:
                    jobs.append(Job(test, cfg, None, name, mod, path))
    return jobs


# --------------------------------------------------------------- execution --
class Runner:
    def __init__(
        self,
        root: Path,
        tc: Toolchain,
        cache: Cache,
        run_id: str,
        cfg: dict,
        keep_work: bool = False,
    ):
        self.root = root
        self.tc = tc
        self.cache = cache
        self.run_id = run_id
        self.cfg = cfg
        self.keep_work = keep_work
        self.work_root = root / "var" / "work" / run_id
        self.baseline_flows = set(cfg.get("cache", {}).get("baseline_flows", []))

    def execute(self, jobs: list[Job], jobs_parallel: int = 4, on_done=None) -> list[Row]:
        """Run every job, then gate.

        Rows are ALSO written out one at a time, to
        `var/runs/<run_id>/partial.jsonl`. The ledger itself can only be written
        after gating -- the checksum gate is cross-flow, so no row's verdict is
        final until its siblings exist -- but a 1800-job nightly that crashes at
        job 1799 would otherwise lose every measurement it took. The partial
        file is ungated and machine-local; the ledger stays authoritative.
        """
        partial = self.root / "var" / "runs" / self.run_id / "partial.jsonl"
        partial.parent.mkdir(parents=True, exist_ok=True)

        rows: list[Row] = []
        with partial.open("a") as fh, ThreadPoolExecutor(max_workers=max(1, jobs_parallel)) as pool:
            for row in pool.map(self._one, jobs):
                rows.append(row)
                doc = row.to_dict()
                doc.pop("cmds", None)
                fh.write(json.dumps(doc, sort_keys=True) + "\n")
                fh.flush()
                if on_done:
                    on_done(row)
        return self.gate(rows)

    def _one(self, job: Job) -> Row:
        row = Row(
            test=job.test.name,
            config=job.config.id,
            suite=job.test.suite,
            tech=job.tech,
            flow=job.flow,
            kind=getattr(job.module, "KIND", "synth"),
            passed=False,
            cached=False,
            measured=_dt.date.today().isoformat(),
            pyrope_status=job.test.pyrope_status,
            lec=job.test.lec_status,
            comparable=job.test.comparable(),
        )

        if job.tech and job.tech not in self.tc.techs:
            row.status = "skipped"
            row.note = (
                f"Liberty for '{job.tech}' is not staged -- run "
                f"`bazel run //:sync-toolchain` (see tech/{job.tech}/VERSION)"
            )
            return row

        # OPTIONAL tools degrade the row (a note, a missing column); NEEDS
        # tools skip it. Conflating the two is how a whole flow disappears
        # because one auxiliary binary is absent.
        absent = self.tc.missing(list(getattr(job.module, "OPTIONAL", ())))
        if absent:
            row.note = f"degraded: {', '.join(absent)} not staged"

        missing = self.tc.missing(list(getattr(job.module, "NEEDS", ())))
        if missing:
            # A missing tool SKIPS and says so. A blank cell reads as "the
            # comparison ran and found nothing", which is a different claim.
            row.status, row.note = "skipped", f"toolchain missing: {', '.join(missing)}"
            return row

        key = cache_key(job.test, job.config, job.tech, job.flow, job.flow_file, self.tc)
        if job.flow in self.baseline_flows:
            hit = self.cache.get(job.flow, key)
            if hit:
                for field_name in ("qor", "sta", "sim", "time_ms", "peak_rss_kb"):
                    setattr(row, field_name, hit.get(field_name, {}))
                row.cached = True
                row.passed = hit.get("passed", True)
                row.measured = hit.get("measured", row.measured)
                row.status = "ok"
                age = self.cache.age_days(hit)
                limit = self.cfg.get("cache", {}).get("stale_after_days", 30)
                if age is not None and age > limit:
                    row.note = f"baseline reused from {row.measured} ({age}d old)"
                return row

        work = self.work_root / job.test.name / job.config.id / (job.tech or "notech") / job.flow
        shutil.rmtree(work, ignore_errors=True)
        work.mkdir(parents=True, exist_ok=True)
        logs = work / "logs"

        ctx = FlowContext(
            test=job.test,
            config=job.config,
            tech=self.tc.tech(job.tech) if job.tech else None,
            tc=self.tc,
            work=work,
            logs=logs,
            sim_reps=int(self.cfg.get("run", {}).get("sim_reps", 3)),
            lec_timeout_s=int(self.cfg.get("run", {}).get("lec_timeout_s", 300)),
        )

        try:
            result = job.module.run(ctx)
        except FlowSkip as e:
            row.status, row.note = "skipped", str(e)
            return row
        except FlowError as e:
            row.status, row.note, row.passed = "failed", str(e).splitlines()[0], False
            row.time_ms, row.peak_rss_kb = ctx.stage.finish()
            row.cmds = ctx.cmds
            return row
        except Exception as e:  # noqa: BLE001 -- a crashing flow is a red row, not a dead run
            row.status = "failed"
            row.note = f"{type(e).__name__}: {e}"
            (logs / "traceback.txt").parent.mkdir(parents=True, exist_ok=True)
            (logs / "traceback.txt").write_text(traceback.format_exc())
            return row

        row.qor = result.get("qor", {})
        row.sta = result.get("sta", {})
        row.sim = result.get("sim", {})
        if "lec" in result:
            row.lec_result = result["lec"]
            # `row.lec` mirrors the MANIFEST's status.lec, which is a claim about
            # pyrope-vs-verilog. lec_netlist proves rtl-vs-netlist, so writing
            # its verdict here would answer a question nobody asked -- and
            # _record_lec would then copy it into design.toml.
            if result["lec"].get("obligation", "pyrope-vs-verilog") == "pyrope-vs-verilog":
                row.lec = result["lec"]["verdict"]
            if result.get("lec_drift"):
                row.note = (
                    f"manifest declares lec = \"{result['lec']['declared']}\" but "
                    f"{result['lec']['solver']} says {result['lec']['verdict']}"
                )
        row.lec_aux_result = result.get("lec_aux", {})
        row.pyrope_status = result.get("pyrope_status", row.pyrope_status)
        row.time_ms, row.peak_rss_kb = ctx.stage.finish()
        row.cmds = ctx.cmds
        row.passed = True
        row.status = "ok"
        row.sta.pop("_", None)

        if job.flow in self.baseline_flows:
            self.cache.put(job.flow, key, row.to_dict(), logs=logs)
        if not self.keep_work:
            # Netlists and vobj trees are large and reproducible; logs are not.
            # KEEP A LIST, NOT A DENY-LIST OF NAMES. The old hard-coded set of
            # directory names had drifted from the workdirs the flows actually
            # ask for (`Wemit`, `rw`, `iw`, `Wm`, `LW`, `tw`, `OT` were all
            # missing), so a nightly left most of its scratch trees on disk.
            for child in work.iterdir():
                if child.is_dir() and child != logs:
                    shutil.rmtree(child, ignore_errors=True)
        return row

    # ------------------------------------------------------------- gates --
    def gate(self, rows: list[Row]) -> list[Row]:
        gates = self.cfg.get("gates", {})

        # 1. Checksum agreement. THE tripwire against a miscompile reported as a
        #    speedup: a simulator that runs twice as fast because it computed the
        #    wrong thing must fail, not top the chart.
        if gates.get("require_checksum_match", True):
            groups: dict[tuple[str, str], list[Row]] = {}
            for r in rows:
                if r.kind == "sim" and r.status == "ok" and r.sim.get("checksum"):
                    groups.setdefault((r.test, r.config), []).append(r)
            for (test, config), group in groups.items():
                sums = {r.sim["checksum"] for r in group}
                if len(sums) > 1:
                    detail = ", ".join(f"{r.flow}={r.sim['checksum']}" for r in sorted(group, key=lambda r: r.flow))
                    for r in group:
                        r.passed = False
                        r.status = "failed"
                        r.note = f"simulators disagree on {test}#{config}: {detail}"

        # 2. STA correlation. A LiveHD timing bug should redden a column across
        #    many tests, not silently corrupt one QoR number.
        limit = float(gates.get("sta_delta_pct_max", 10.0))
        for r in rows:
            delta = r.sta.get("delta_pct")
            if delta is not None and delta > limit:
                r.passed = False
                r.status = "failed"
                r.note = (
                    f"OpenTimer {r.sta.get('opentimer_ns')}ns vs OpenSTA "
                    f"{r.sta.get('opensta_ns')}ns = {delta}% apart (limit {limit}%)"
                )

        # 3. LEC drift: a manifest claiming `proven` while the prover disagrees
        #    would let an unverified test into the headline geomean.
        for r in rows:
            if r.kind == "lec" and r.note.startswith("manifest declares"):
                r.passed = False
                r.status = "failed"

        # 4. Backend split: the two solvers prove the SAME obligation, so one
        #    proving what the other refutes is a code-generation or encoding bug
        #    rather than a design result. Neither backend is "right", so both
        #    rows are flagged rather than one failed.
        # GROUPED BY OBLIGATION. Two engines answering the same question must
        # agree; two engines answering different questions have no reason to.
        by_test: dict[tuple[str, str, str], dict[str, Row]] = {}
        for r in rows:
            if r.kind == "lec" and r.lec_result.get("verdict") in ("proven", "refuted"):
                obligation = r.lec_result.get("obligation", "pyrope-vs-verilog")
                by_test.setdefault((r.test, r.config, obligation), {})[r.flow] = r
        for (test, config, _obligation), group in by_test.items():
            if len(group) < 2:
                continue
            verdicts = {r.lec_result["verdict"] for r in group.values()}
            if len(verdicts) > 1:
                detail = ", ".join(
                    f"{f}={r.lec_result['verdict']}" for f, r in sorted(group.items())
                )
                for r in group.values():
                    r.passed = False
                    r.status = "failed"
                    r.note = f"LEC backends disagree on {test}#{config}: {detail}"
        self._record_lec(rows)
        return rows

    def _record_lec(self, rows: list[Row]) -> None:
        """Write a definitive verdict back into design.toml when it was unknown.

        `status.lec = "none"` means UNKNOWN, so filling it in from a run that
        actually proved something is strictly an improvement -- and without it
        nothing ever becomes comparable, because the headline gate reads the
        manifest rather than the ledger. An EXPLICIT claim is never overwritten:
        a manifest saying `proven` while a solver says `refuted` stays a
        failure, which is the whole point of the drift check.

        THREE THINGS DISQUALIFY A ROW from being written back, because
        `status.lec` is what lets a test into the headline geomean:

          a different obligation  lec_netlist proves rtl-vs-netlist. Recording
                                  its verdict as `status.lec` would say the
                                  Pyrope matches the Verilog on a test that may
                                  have no Pyrope at all.
          a failed row            the gates above may have just failed this row
                                  for disagreeing with its sibling backend.
          disagreeing backends    if cvc5 says proven and lgyosys says refuted,
                                  whichever row came first is not the answer.
        """
        import re as _re

        verdicts: dict[str, set[str]] = {}
        for r in rows:
            if r.kind != "lec" or r.status != "ok":
                continue
            if r.lec_result.get("obligation", "pyrope-vs-verilog") != "pyrope-vs-verilog":
                continue
            v = r.lec_result.get("verdict")
            if v in ("proven", "refuted"):
                verdicts.setdefault(r.test, set()).add(v)

        for test, answers in sorted(verdicts.items()):
            if len(answers) != 1:
                continue
            v = next(iter(answers))
            toml = self.root / "tests" / test / "design.toml"
            if not toml.exists():
                continue
            text = toml.read_text()
            if not _re.search(r'^lec\s*=\s*"none"', text, _re.M):
                continue
            toml.write_text(
                _re.sub(r'^lec(\s*=\s*)"none"', f'lec\\g<1>"{v}"', text, count=1, flags=_re.M)
            )

    def identity(self) -> dict:
        """Stamped onto every row. A number without this block is not evidence."""
        return {
            "date": _dt.date.today().isoformat(),
            "run_id": self.run_id,
            # `uname -n`. THE key the report segments on: different machines
            # produce different wall-clock and peak-RSS numbers, and half of
            # what lhdtrack tracks is exactly those two.
            "host": host_name(),
            "host_class": self.tc.host_class,
            "platform": f"{platform.system()} {platform.release()} {platform.machine()}",
            "cpus": os.cpu_count(),
            "versions": self.tc.versions,
        }
