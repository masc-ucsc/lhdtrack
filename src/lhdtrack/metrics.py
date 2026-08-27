"""Measurement primitives: wall clock, peak RSS, and the best-of-N rule.

Every number lhdtrack publishes comes through `measure()`. Two decisions are
baked in here rather than left to each flow:

PEAK RSS COMES FROM wait4, NOT /usr/bin/time. `os.wait4` hands back the rusage
of exactly the child that was waited on, which is correct under the runner's
thread pool; `resource.getrusage(RUSAGE_CHILDREN)` is cumulative across every
child the process has ever reaped and would attribute one flow's memory to
another. The GNU-vs-BSD `time` split that ../lhdsuite/bench/common.sh has to
work around disappears too -- only the KiB-vs-bytes normalization survives.

THE EXEC LEG IS BEST-OF-N, NEVER ONE SAMPLE. A single simulation timing is a
sample of the machine, not of the simulator; a background compile or a
filesystem stall lands squarely in the interval. The minimum over N runs is the
one order statistic that noise can only push upward, so it is the honest floor.
"""

from __future__ import annotations

import os
import platform
import subprocess
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

# Linux reports ru_maxrss in KiB; the BSDs (macOS included) report bytes.
_RSS_TO_KB = 1 if platform.system() == "Linux" else 1024


@dataclass
class Measured:
    """One timed, memory-tracked subprocess invocation."""

    label: str
    argv: list[str]
    rc: int
    ms: int
    peak_rss_kb: int
    log: Path
    cwd: Path
    # The watchdog killed it. A NON-ZERO rc alone cannot say this: SIGKILL and a
    # tool that exited angrily look identical from the exit code, and calling a
    # tool that ran out of time an "error" reports a design as broken when the
    # honest answer is that nobody waited long enough.
    timed_out: bool = False

    @property
    def ok(self) -> bool:
        return self.rc == 0

    def tail(self, lines: int = 25) -> str:
        """The last few log lines -- what a failure diagnosis quotes."""
        try:
            return "\n".join(self.log.read_text(errors="replace").splitlines()[-lines:])
        except OSError:
            return "(no log)"


@dataclass
class Stage:
    """Accumulates the per-stage timing and memory of one flow invocation.

    Peak RSS is kept per stage, not per flow: a whole-flow number hides which
    stage is the hog, and that is the only thing the number is useful for.
    """

    time_ms: dict[str, int] = field(default_factory=dict)
    peak_rss_kb: dict[str, int] = field(default_factory=dict)

    def add(self, m: Measured) -> Measured:
        self.time_ms[m.label] = m.ms
        self.peak_rss_kb[m.label] = m.peak_rss_kb
        return m

    def finish(self) -> tuple[dict[str, int], dict[str, int]]:
        time_ms = dict(self.time_ms)
        time_ms["total"] = sum(self.time_ms.values())
        rss = dict(self.peak_rss_kb)
        rss["max"] = max(self.peak_rss_kb.values(), default=0)
        return time_ms, rss


def measure(
    label: str,
    argv: list[str],
    cwd: Path,
    log_dir: Path,
    env: dict[str, str] | None = None,
    timeout: float | None = None,
) -> Measured:
    """Run `argv`, returning its wall clock in ms and peak RSS in KiB.

    stdout and stderr are merged into `log_dir/<label>.log` rather than
    captured in memory: a synthesis log can be tens of MB, and it has to
    survive the run anyway so a failure is diagnosable without re-running.
    """
    log_dir.mkdir(parents=True, exist_ok=True)
    log = log_dir / f"{label}.log"

    full_env = {**os.environ, **(env or {})}
    with log.open("wb") as fh:
        fh.write(f"$ {' '.join(map(str, argv))}\n".encode())
        fh.flush()
        t0 = time.monotonic()
        proc = subprocess.Popen(  # noqa: S603 -- argv is built from toolchain.json, never a shell string
            [str(a) for a in argv],
            stdout=fh,
            stderr=subprocess.STDOUT,
            cwd=str(cwd),
            env=full_env,
        )
        # A tool that never returns must not hang the nightly. `os.wait4` has no
        # timeout of its own, so the kill is a watchdog; the process still ends
        # up reaped by the wait4 below, rusage intact.
        fired = threading.Event()

        def _kill() -> None:
            fired.set()
            proc.kill()

        watchdog = threading.Timer(timeout, _kill) if timeout else None
        if watchdog:
            watchdog.daemon = True
            watchdog.start()
        try:
            # wait4 gives us THIS child's rusage. Popen.wait() would reap the
            # process first and leave nothing to ask about.
            _, status, rusage = os.wait4(proc.pid, 0)
        except ChildProcessError:  # already reaped (only reachable on a signal race)
            # KEEP THE REAL EXIT CODE. Substituting 0 here turned a command that
            # had actually failed into a successful measurement -- `ok` is
            # `rc == 0`, so the flow carried on with whatever the tool did not
            # produce.
            proc.wait()
            status, rusage = None, None
        finally:
            if watchdog:
                watchdog.cancel()
        ms = int((time.monotonic() - t0) * 1000)

    if status is not None:
        proc.returncode = os.waitstatus_to_exitcode(status)
    peak = int(rusage.ru_maxrss // _RSS_TO_KB) if rusage else 0

    return Measured(
        label=label,
        argv=[str(a) for a in argv],
        rc=proc.returncode or 0,
        ms=ms,
        peak_rss_kb=peak,
        log=log,
        cwd=cwd,
        timed_out=fired.is_set(),
    )


def best_of(
    reps: int,
    label: str,
    argv: list[str],
    cwd: Path,
    log_dir: Path,
    env: dict[str, str] | None = None,
) -> tuple[Measured, list[int]]:
    """Run `argv` `reps` times and return the FASTEST measurement plus every sample.

    All samples are returned so the report can show the spread. A wide spread on
    an unchanged binary means the box is too noisy to be measuring on, and that
    is worth seeing rather than hiding behind the minimum.
    """
    runs: list[Measured] = []
    for i in range(max(1, reps)):
        m = measure(f"{label}_{i}" if reps > 1 else label, argv, cwd, log_dir, env)
        runs.append(m)
        if not m.ok:  # a failing command is not worth re-timing
            break
    best = min(runs, key=lambda m: m.ms)
    return best, [m.ms for m in runs]
