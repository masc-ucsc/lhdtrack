"""Map yosys' generic latches onto a Liberty's own latch cells.

yosys `dfflibmap` maps flip-flops only; a design with latches (clock-gating
enables) otherwise leaves `$_DLATCH_P_/$_DLATCH_N_` cells that no Liberty
reader or timer knows. Pick, per enable polarity, the smallest cell whose
`latch` group is a plain transparent latch (`data_in` one pin, `enable` one pin
or its negation, output function IQ, no other inputs) and emit a techmap.
"""
from __future__ import annotations

import re
from pathlib import Path

_CELL = re.compile(r'\bcell\s*\(\s*"?([^")\s]+)"?\s*\)\s*\{')
_PIN = re.compile(r'\bpin\s*\(\s*"?([^")\s]+)"?\s*\)\s*\{')


def _groups(pattern: re.Pattern, text: str):
    for m in pattern.finditer(text):
        depth, i = 1, m.end()
        while depth and i < len(text):
            depth += {"{": 1, "}": -1}.get(text[i], 0)
            i += 1
        yield m.group(1), text[m.end():i]


def _cells(text: str):
    return _groups(_CELL, text)


def latch_cells(liberty: Path) -> dict[bool, tuple[str, str, str, str]]:
    """{enable_active_high: (cell, data_pin, enable_pin, q_pin)}, smallest area first."""
    best: dict[bool, tuple[float, tuple[str, str, str, str]]] = {}
    for name, body in _cells(liberty.read_text(errors="replace")):
        if re.search(r'\b(is_isolation_cell|dont_use)\s*:\s*"?true', body):
            continue  # power-intent / reserved cells are not general-purpose latches
        latch = re.search(r'\blatch\s*\(\s*"?(\w+)"?\s*,\s*"?(\w+)"?\s*\)\s*\{(.*?)\}', body, re.S)
        if not latch:
            continue
        state = latch.group(1)
        data = re.search(r'data_in\s*:\s*"\s*(\w+)\s*"', latch.group(3))
        enable = re.search(r'enable\s*:\s*"\s*(!?)\s*(\w+)\s*"', latch.group(3))
        if not data or not enable or re.search(r'\b(clear|preset)\s*:', latch.group(3)):
            continue
        pins = list(_groups(_PIN, body))
        inputs = [p for p, b in pins if re.search(r'direction\s*:\s*"?input', b)]
        outs = [p for p, b in pins if re.search(rf'function\s*:\s*"\s*{state}\s*"', b)]
        if sorted(inputs) != sorted({data.group(1), enable.group(2)}) or not outs:
            continue
        area = float(re.search(r'\barea\s*:\s*([0-9.eE+-]+)', body).group(1))
        high = enable.group(1) != "!"
        entry = (name, data.group(1), enable.group(2), outs[0])
        if high not in best or area < best[high][0]:
            best[high] = (area, entry)
    return {k: v[1] for k, v in best.items()}


def techmap_verilog(liberty: Path) -> str:
    out = []
    for high, gen in ((True, "$_DLATCH_P_"), (False, "$_DLATCH_N_")):
        cell = latch_cells(liberty).get(high)
        if cell:
            name, d, en, q = cell
            out.append(f"module \\{gen} (input E, input D, output Q);\n"
                       f"  \\{name} _TECHMAP_REPLACE_ (.{en}(E), .{d}(D), .{q}(Q));\nendmodule\n")
    return "\n".join(out)
