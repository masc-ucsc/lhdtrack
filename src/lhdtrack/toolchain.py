"""The runner's only view of the outside world.

lhdtrack resolves every tool from `var/toolchain/toolchain.json`, written by
`bazel run //:sync-toolchain`. It never reads PATH.

That is not fussiness. The baseline cache reuses a yosys result until something
in its key changes, and the key includes the yosys version string from this
file. If the runner could pick up a different yosys from PATH, a cached number
could silently become a number from a different tool -- which is exactly the
class of error a QoR tracker exists to prevent.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

TOOLCHAIN_REL = Path("var/toolchain/toolchain.json")

_SYNC_HINT = (
    "run `bazel run //:sync-toolchain` first -- lhdtrack resolves tools only from "
    "var/toolchain/toolchain.json, never from PATH"
)


class ToolchainError(RuntimeError):
    pass


@dataclass(frozen=True)
class Tech:
    name: str
    liberty: list[Path]
    sha256: str
    # The Liberty's own time unit ("ns" for sky130, "ps" for ASAP7). Every delay
    # measured against this library -- and the SDC period -- is in it, so the
    # report has to label the column with it rather than assuming ns.
    time_unit: str = "ns"


@dataclass(frozen=True)
class Toolchain:
    root: Path
    host_class: str
    generated: str
    _bin: dict[str, Path]
    _env: dict[str, dict[str, str]]
    _versions: dict[str, str]
    _tech: dict[str, Tech]

    @classmethod
    def load(cls, root: Path) -> Toolchain:
        path = root / TOOLCHAIN_REL
        if not path.exists():
            raise ToolchainError(f"{path} not found -- {_SYNC_HINT}")
        doc = json.loads(path.read_text())
        return cls(
            root=root,
            host_class=doc.get("host_class", "unknown"),
            generated=doc.get("generated", ""),
            _bin={k: Path(v) for k, v in doc.get("bin", {}).items()},
            _env={
                name: {str(k): str(v) for k, v in values.items()}
                for name, values in doc.get("env", {}).items()
            },
            _versions=dict(doc.get("versions", {})),
            _tech={
                name: Tech(
                    name,
                    [Path(p) for p in t["liberty"]],
                    t["sha256"],
                    t.get("time_unit") or "ns",
                )
                for name, t in doc.get("tech", {}).items()
            },
        )

    # -- binaries ----------------------------------------------------------
    def has(self, name: str) -> bool:
        return name in self._bin

    def bin(self, name: str) -> Path:
        try:
            return self._bin[name]
        except KeyError:
            raise ToolchainError(
                f"tool '{name}' is not in the toolchain -- {_SYNC_HINT}"
            ) from None

    def version(self, name: str) -> str:
        return self._versions.get(name, "missing")

    def env_for(self, executable: str | Path) -> dict[str, str]:
        """Runtime environment belonging to a staged tool executable."""
        path = Path(executable)
        for name, tool_path in self._bin.items():
            if path == tool_path:
                return dict(self._env.get(name, {}))
        return {}

    @property
    def versions(self) -> dict[str, str]:
        return dict(self._versions)

    # -- technology --------------------------------------------------------
    def tech(self, name: str) -> Tech:
        try:
            return self._tech[name]
        except KeyError:
            known = ", ".join(sorted(self._tech)) or "(none staged)"
            raise ToolchainError(f"unknown tech '{name}'; staged: {known}") from None

    @property
    def techs(self) -> list[str]:
        return sorted(self._tech)

    def missing(self, names: list[str]) -> list[str]:
        """Which of `names` are absent, so a flow can be SKIPPED and reported.

        A missing tool is never a silent pass. ../lhdsuite learned this with
        verilator: the target skips, but it still emits `verilator_present 0`
        so the report says "skipped" instead of leaving a blank that reads like
        the comparison was run and found nothing.
        """
        return [n for n in names if not self.has(n)]
