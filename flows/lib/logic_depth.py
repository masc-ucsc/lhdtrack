"""Mapped combinational cell levels, with flop/latch outputs as depth-zero sources."""

from __future__ import annotations

from collections import defaultdict, deque
from functools import lru_cache
import re
from pathlib import Path

_CELL = re.compile(r'"(?:\\.|[^"\\])*"|\bcell\s*\(\s*(?:"([^"\n]+)"|([^\s)]+))\s*\)\s*\{')
_TEXT = re.compile(r'"(?:\\.|[^"\\])*"|/\*.*?\*/|//[^\n]*', re.S)


@lru_cache(maxsize=16)
def _library_cells(path: str, modified: int, size: int) -> tuple[frozenset, frozenset]:
    # Keep quoted names, but remove comments before indexing cells. Quoted
    # attribute values are then hidden before looking for ff/latch groups.
    text = _TEXT.sub(lambda m: m[0] if m[0].startswith('"') else ' ',
                     Path(path).read_text())
    headers = [match for match in _CELL.finditer(text) if match[1] or match[2]]
    known, sequential = set(), set()
    for i, header in enumerate(headers):
        name = header[1] or header[2]
        known.add(name)
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = _TEXT.sub('""', text[header.end():end])
        if re.search(r'\b(?:ff|ff_bank|latch|latch_bank)\s*\(', body):
            sequential.add(name)
    return frozenset(known), frozenset(sequential)


def liberty_cells(paths: list[Path]) -> tuple[set[str], set[str]]:
    known, sequential = set(), set()
    for path in paths:
        stat = path.stat()
        names, state = _library_cells(str(path.resolve()), stat.st_mtime_ns, stat.st_size)
        known.update(names)
        sequential.update(state)
    return known, sequential


def mapped_depth(module: dict, known: set[str], sequential: set[str]) -> int:
    """Maximum cell levels. Buffers/inverters count; wires and registers do not.

    Reject unknown cells, multiple drivers and combinational cycles rather than
    treating them as zero-delay boundaries. The caller reports a missing metric.
    """
    producers, comb = {}, {}
    for name, cell in module['cells'].items():
        kind = cell['type']
        # Yosys flatten retains hierarchy provenance as a portless metadata cell.
        if kind == '$scopeinfo' and not cell.get('connections'):
            continue
        if kind not in known:
            raise ValueError(f'not a mapped Liberty cell: {kind}')
        if kind not in sequential:
            comb[name] = cell
        for port, bits in cell['connections'].items():
            direction = cell['port_directions'][port]
            if direction == 'inout':
                raise ValueError(f'bidirectional cell port: {name}.{port}')
            if direction != 'output':
                continue
            for bit in bits:
                if not isinstance(bit, int):
                    continue
                if bit in producers:
                    raise ValueError(f'multiple drivers for net bit {bit}')
                producers[bit] = None if kind in sequential else name

    dependencies, users = {}, defaultdict(list)
    for name, cell in comb.items():
        dependencies[name] = {
            producers[bit]
            for port, bits in cell['connections'].items()
            if cell['port_directions'][port] == 'input'
            for bit in bits
            if bit in producers and producers[bit] is not None
        }
        for driver in dependencies[name]:
            users[driver].append(name)
    queue = deque(name for name in comb if not dependencies[name])
    depths = {name: 1 for name in queue}
    while queue:
        name = queue.popleft()
        for user in users[name]:
            depths[user] = max(depths.get(user, 1), depths[name] + 1)
            dependencies[user].remove(name)
            if not dependencies[user]:
                queue.append(user)
    if any(dependencies.values()):
        raise ValueError('combinational cycle in mapped netlist')
    return max(depths.values(), default=0)
