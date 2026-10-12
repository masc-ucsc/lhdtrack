"""Physical combinational cones ending at register data pins and primary outputs."""
from __future__ import annotations

from collections import Counter, defaultdict, deque
from dataclasses import dataclass
import hashlib
import json
import re


def register_group(name: str) -> str:
    return re.sub(r"(?:\[\d+\])+$", "", name)


def indices(mask: int):
    while mask:
        low = mask & -mask
        yield low.bit_length() - 1
        mask ^= low


@dataclass(frozen=True)
class Cone:
    group: str
    cells: int
    sources: int
    levels: int


@dataclass
class ConeIndex:
    endpoints: dict[str, Cone]
    cell_types: list[str]
    source_names: list[str]
    source_groups: list[str]
    sequential_count: int

    def metrics(self, cones: list[Cone]) -> dict:
        cells = sources = levels = shared = references = 0
        for cone in cones:
            shared |= cells & cone.cells
            cells |= cone.cells
            sources |= cone.sources
            levels = max(levels, cone.levels)
            references += cone.cells.bit_count()
        return {"cells": cells.bit_count(), "levels": levels,
                "endpoint_cell_references": references,
                "cells_shared_by_endpoints": shared.bit_count(),
                "cells_private_to_one_endpoint": cells.bit_count() - shared.bit_count(),
                "source_bits": sources.bit_count(),
                "source_sha256": hashlib.sha256(json.dumps(sorted(
                    self.source_names[i] for i in indices(sources))).encode()).hexdigest(),
                "cell_types": dict(sorted(Counter(self.cell_types[i]
                                                 for i in indices(cells)).items())),
                "source_groups": dict(sorted(Counter(self.source_groups[i]
                                                    for i in indices(sources)).items()))}

    def summary(self) -> dict:
        union = self.metrics(list(self.endpoints.values()))
        return {"combinational_cells": len(self.cell_types),
                "sequential_cells": self.sequential_count,
                "endpoints": len(self.endpoints),
                "endpoint_union_cells": union["cells"],
                "cells_outside_data_output_cones": len(self.cell_types) - union["cells"],
                "max_levels": union["levels"]}

    def group_memberships(self) -> dict[tuple[str, ...], int]:
        """Disjoint accounting by the complete set of endpoint groups using a cell."""
        groups = defaultdict(int)
        for cone in self.endpoints.values():
            groups[cone.group] |= cone.cells
        segments = {(): (1 << len(self.cell_types)) - 1}
        for group, mask in sorted(groups.items()):
            updated = {}
            for signature, cells in segments.items():
                overlap, remainder = cells & mask, cells & ~mask
                if overlap:
                    updated[(*signature, group)] = overlap
                if remainder:
                    updated[signature] = remainder
            segments = updated
        return {signature: mask.bit_count() for signature, mask in segments.items()}


def index_cones(module: dict, known: set[str], sequential: set[str],
                data_ports: frozenset[str] = frozenset({"D"})) -> ConeIndex:
    """Stop at every sequential output; count each physical gate once per cone.

    Cell levels include buffers and inverters, and are not timing estimates.
    Only specified register data pins are endpoints; clock/reset networks are
    not data cones. Portless Yosys hierarchy metadata is ignored.
    """
    cells, state = {}, {}
    for name, cell in module["cells"].items():
        kind = cell["type"]
        if kind == "$scopeinfo" and not cell.get("connections"):
            continue
        if kind not in known:
            raise ValueError(f"not a mapped Liberty cell: {kind}")
        (state if kind in sequential else cells)[name] = cell
    cell_ids = {name: i for i, name in enumerate(cells)}
    drivers: dict[int, tuple[str, int]] = {}
    source_names, source_groups = [], []

    def add_driver(bit, driver):
        if not isinstance(bit, int):
            return
        if bit in drivers:
            raise ValueError(f"multiple drivers for net bit {bit}")
        drivers[bit] = driver

    def add_source(bit, name, group):
        add_driver(bit, ("source", len(source_names)))
        source_names.append(name)
        source_groups.append(group)

    for name, port in module.get("ports", {}).items():
        if port["direction"] == "inout":
            raise ValueError(f"bidirectional module port: {name}")
        if port["direction"] == "input":
            for i, bit in enumerate(port["bits"]):
                add_source(bit, f"input:{name}[{i}]", f"input:{name}")
    for name, cell in {**cells, **state}.items():
        for port, bits in cell["connections"].items():
            direction = cell["port_directions"][port]
            if direction == "inout":
                raise ValueError(f"bidirectional cell port: {name}.{port}")
            if direction != "output":
                continue
            for i, bit in enumerate(bits):
                if name in state:
                    add_source(bit, f"register:{name}.{port}[{i}]",
                               f"register:{register_group(name)}")
                else:
                    add_driver(bit, ("cell", cell_ids[name]))

    def driver(bit):
        if not isinstance(bit, int):
            return None
        if bit not in drivers:
            raise ValueError(f"undriven net bit {bit}")
        return drivers[bit]

    dependencies, users = {}, defaultdict(list)
    masks, supports, levels = {}, {}, {}
    for name, cell in cells.items():
        i = cell_ids[name]
        dependencies[i], supports[i] = set(), 0
        for port, bits in cell["connections"].items():
            if cell["port_directions"][port] != "input":
                continue
            for bit in bits:
                origin = driver(bit)
                if origin is None:
                    continue
                kind, j = origin
                if kind == "source":
                    supports[i] |= 1 << j
                else:
                    dependencies[i].add(j)
        for j in dependencies[i]:
            users[j].append(i)
        masks[i], levels[i] = 1 << i, 1
    queue = deque(i for i in dependencies if not dependencies[i])
    processed = 0
    while queue:
        i = queue.popleft()
        processed += 1
        for j in users[i]:
            masks[j] |= masks[i]
            supports[j] |= supports[i]
            levels[j] = max(levels[j], levels[i] + 1)
            dependencies[j].remove(i)
            if not dependencies[j]:
                queue.append(j)
    if processed != len(cells):
        raise ValueError("combinational cycle in mapped netlist")

    endpoints = {}

    def add_endpoint(name, group, bit):
        origin = driver(bit)
        mask = support = depth = 0
        if origin:
            kind, i = origin
            if kind == "source":
                support = 1 << i
            else:
                mask, support, depth = masks[i], supports[i], levels[i]
        endpoints[name] = Cone(group, mask, support, depth)

    for name, cell in state.items():
        for port, bits in cell["connections"].items():
            if port in data_ports and cell["port_directions"][port] == "input":
                for i, bit in enumerate(bits):
                    add_endpoint(f"register:{name}.{port}[{i}]",
                                 f"register:{register_group(name)}", bit)
    for name, port in module.get("ports", {}).items():
        if port["direction"] == "output":
            for i, bit in enumerate(port["bits"]):
                add_endpoint(f"output:{name}[{i}]", f"output:{name}", bit)
    return ConeIndex(endpoints, [c["type"] for c in cells.values()],
                     source_names, source_groups, len(state))


def compare_cones(baseline: ConeIndex, measured: ConeIndex, detail_limit: int = 50) -> dict:
    matched = sorted(baseline.endpoints.keys() & measured.endpoints.keys())
    baseline_only = sorted(baseline.endpoints.keys() - measured.endpoints.keys())
    measured_only = sorted(measured.endpoints.keys() - baseline.endpoints.keys())
    groups = defaultdict(list)
    rows = []
    for name in matched:
        b, m = baseline.endpoints[name], measured.endpoints[name]
        groups[b.group].append(name)
        rows.append({"endpoint": name, "group": b.group,
                     "baseline_cells": b.cells.bit_count(), "measured_cells": m.cells.bit_count(),
                     "cell_delta": m.cells.bit_count() - b.cells.bit_count(),
                     "baseline_levels": b.levels, "measured_levels": m.levels,
                     "baseline_source_bits": b.sources.bit_count(),
                     "measured_source_bits": m.sources.bit_count()})
    rows.sort(key=lambda r: (-r["cell_delta"], r["endpoint"]))
    details = []
    for row in rows[:detail_limit]:
        name = row["endpoint"]
        details.append({**row, "baseline": baseline.metrics([baseline.endpoints[name]]),
                        "measured": measured.metrics([measured.endpoints[name]])})
    group_rows = []
    for group, names in groups.items():
        b = baseline.metrics([baseline.endpoints[name] for name in names])
        m = measured.metrics([measured.endpoints[name] for name in names])
        group_rows.append({"group": group, "matched_endpoints": len(names),
                           "baseline": b, "measured": m,
                           "same_source_bits": b["source_sha256"] == m["source_sha256"],
                           "cell_delta": m["cells"] - b["cells"]})
    group_rows.sort(key=lambda r: (-r["cell_delta"], r["group"]))
    b_members, m_members = baseline.group_memberships(), measured.group_memberships()
    membership_rows = [{"groups": list(signature), "baseline_cells": b_members.get(signature, 0),
                        "measured_cells": m_members.get(signature, 0),
                        "cell_delta": m_members.get(signature, 0) - b_members.get(signature, 0)}
                       for signature in sorted(b_members.keys() | m_members.keys())]
    membership_rows.sort(key=lambda r: (-r["cell_delta"], r["groups"]))
    return {"baseline": baseline.summary(), "measured": measured.summary(),
            "matched_endpoints": len(matched), "baseline_only": baseline_only,
            "measured_only": measured_only, "groups": group_rows,
            "endpoints": rows, "largest_growth_details": details,
            "disjoint_group_memberships": membership_rows,
            "scope": "Named register data pins and primary outputs; sequential outputs are "
                     "boundaries. Groups are unions of matched endpoint cones. Cells shared "
                     "between groups or endpoints appear in each cone, so deltas cannot be "
                     "summed. Structural source support is connectivity, not Boolean influence. "
                     "Levels count physical cells and do not estimate delay. Name matching is "
                     "not an equivalence proof."}
