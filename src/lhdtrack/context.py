"""What a flow is handed, and the contract it implements.

A flow is a plain Python module under `flows/` exposing:

    NAME       str          matches the filename, and is what design.toml names
    KIND       str          "synth" | "sim" | "gate"
    NEEDS      list[str]    tools that must be in the toolchain, else SKIP
    USES_TECH  bool         does this flow depend on the Liberty?
    run(ctx)   -> dict      the measurement

The module's FILE CONTENT is hashed into the cache key, so editing how yosys is
invoked correctly invalidates every yosys baseline. That is why flows are files
on disk rather than classes in the package.

A flow measures; it does not decide. Gates (checksum agreement, STA delta,
LEC status) live in run.py so one policy applies to every flow rather than each
one inventing its own.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass, field
from pathlib import Path

from .corpus import Config, Test
from .metrics import Measured, Stage, best_of, measure
from .toolchain import Tech, Toolchain


class FlowError(RuntimeError):
    """A flow could not produce a measurement. Reported red, never silently dropped."""


class FlowSkip(RuntimeError):
    """A flow cannot run here (missing tool, unsupported test).

    Skips are REPORTED, never silent. ../lhdsuite learned this with verilator:
    a blank cell reads as "the comparison ran and found nothing", which is a
    very different claim from "verilator is not installed".
    """


@dataclass
class FlowContext:
    test: Test
    config: Config
    tech: Tech | None
    tc: Toolchain
    work: Path
    logs: Path
    sim_reps: int = 3
    # A solver that gives up is a THIRD state, not a failure -- but it has to be
    # bounded or a nightly never finishes. Policy, so it lives in lhdtrack.toml.
    lec_timeout_s: int = 300
    stage: Stage = field(default_factory=Stage)
    cmds: list[str] = field(default_factory=list)

    # -- helpers -----------------------------------------------------------
    def tool(self, name: str) -> Path:
        return self.tc.bin(name)

    def run(self, label: str, argv: list, check: bool = True,
            timeout: float | None = None) -> Measured:
        """Measure one command, recording its time and peak RSS under `label`.

        `timeout` is a WALL-CLOCK watchdog, and it is not the same thing as a
        tool's own budget. `lhd lec --set formal.timeout=300` does not bound the
        lgyosys backend at all -- it shells out to yosys and waits -- so a flow
        that trusts the tool's flag has no bound, and one hard design stalls the
        nightly `make run` forever. Pass this wherever the tool's own limit is
        not evidence that it will return.
        """
        argv = [str(a) for a in argv]
        self.cmds.append(f"{label}: {' '.join(shlex.quote(a) for a in argv)}")
        m = self.stage.add(
            measure(
                label,
                argv,
                self.work,
                self.logs,
                env=self.tc.env_for(argv[0]),
                timeout=timeout,
            )
        )
        if check and not m.ok:
            if m.timed_out:
                raise FlowError(f"{label} timed out after {timeout:g}s\n{m.tail()}")
            raise FlowError(f"{label} exited {m.rc}\n{m.tail()}")
        return m

    def run_best(self, label: str, argv: list, reps: int | None = None) -> tuple[Measured, list[int]]:
        """Measure best-of-N. Only the exec leg of a simulation uses this."""
        argv = [str(a) for a in argv]
        self.cmds.append(f"{label}: {' '.join(shlex.quote(a) for a in argv)}  (x{reps or self.sim_reps}, best)")
        best, samples = best_of(
            reps or self.sim_reps,
            label,
            argv,
            self.work,
            self.logs,
            env=self.tc.env_for(argv[0]),
        )
        if not best.ok:
            raise FlowError(f"{label} exited {best.rc}\n{best.tail()}")
        self.stage.time_ms[label] = best.ms
        self.stage.peak_rss_kb[label] = best.peak_rss_kb
        return best, samples

    def write(self, name: str, text: str) -> Path:
        """Materialize a generated script (yosys .ys, OpenSTA .tcl) in the workdir.

        Written to disk rather than piped, so a failing run leaves behind the
        exact script that failed next to the log that shows how.
        """
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text)
        return path

    # -- convenience -------------------------------------------------------
    @property
    def top(self) -> str:
        return self.test.top

    @property
    def liberty(self) -> list[Path]:
        if self.tech is None:
            raise FlowError("flow requires a technology but none was selected")
        return self.tech.liberty

    def verilog_sources(self) -> list[Path]:
        return self.test.verilog_sources()

    @property
    def sdc(self) -> Path:
        """This job's constraints -- per technology; see Test.sdc_for."""
        return self.test.sdc_for(self.tech.name if self.tech else None)

    def require_sdc(self) -> Path:
        """The constraints, or a SKIP naming what is missing.

        Every test declares synthesis, because that is what the corpus is for.
        A test whose ports no front end could resolve has no generated SDC, and
        that is a gap in the RUN, not a category the test was never meant to
        have -- so it is reported as a skip with a reason rather than by
        quietly deleting the flow from the manifest.
        """
        sdc = self.sdc
        if not sdc.exists():
            raise FlowSkip(
                f"no constraints for {self.tech.name if self.tech else 'this tech'} "
                "-- the design's ports could not be resolved, so no SDC was generated"
            )
        return sdc

    def abc_delay_ps(self) -> str:
        """Return this job's SDC clock period in ABC's picosecond unit.

        OpenSTA reads the SDC in the Liberty's native unit (ps for ASAP7, ns
        for sky130), but both Yosys's ``abc -D`` and LiveHD's ``abc.delay``
        ultimately constrain ABC in picoseconds. Derive one value from the
        shared SDC so synthesis and STA cannot silently target different clocks.
        """
        sdc = self.require_sdc()
        match = re.search(
            r"^\s*create_clock\b[^\n]*?\s-period\s+([0-9]+(?:\.[0-9]*)?(?:[eE][+-]?[0-9]+)?)",
            sdc.read_text(errors="replace"),
            re.MULTILINE,
        )
        if match is None:
            raise FlowError(f"{sdc}: create_clock has no literal -period for ABC")
        period = float(match.group(1))
        scale = {"fs": 0.001, "ps": 1.0, "ns": 1_000.0, "us": 1_000_000.0}.get(
            self.tech.time_unit if self.tech else "ns"
        )
        if scale is None:
            raise FlowError(f"unsupported Liberty time unit for ABC: {self.tech.time_unit!r}")
        return f"{period * scale:.12g}"

    def chparams(self) -> dict[str, int | str]:
        return dict(self.config.params)
