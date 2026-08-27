"""Parse (and rewrite) a SystemVerilog module's parameter port list.

The corpus is measured through a single top module, and every front end has to
agree on what that module's ports are. A parameter port list makes that agreement
conditional: yosys needs `-chparam`, slang and verilator need `-G`, and the
generated harness needs a matching `#(...)`. Five spellings of one fact.

`localparam` in the parameter port list says the same thing once, in the source,
where every front end already reads it -- so the top elaborates identically with
no flags at all. This module does that rewrite, and nothing else: intermediate
modules keep their parameters, because that is what makes them reusable.
"""

from __future__ import annotations

import re
from dataclasses import dataclass


def strip_comments(text: str) -> str:
    """Blank out comments and strings, preserving offsets.

    Offsets are preserved so a match found in the blanked text indexes straight
    back into the original -- the alternative is two texts that drift apart by
    exactly the number of characters you most need to be right about.
    """
    out = list(text)
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] != "\n":
                out[i] = " "
                i += 1
        elif c == "/" and i + 1 < n and text[i + 1] == "*":
            out[i] = out[i + 1] = " "
            i += 2
            while i < n and not (text[i] == "*" and i + 1 < n and text[i + 1] == "/"):
                if text[i] != "\n":
                    out[i] = " "
                i += 1
            if i < n:
                out[i] = out[i + 1] = " "
                i += 2
        elif c == '"':
            out[i] = " "
            i += 1
            while i < n and text[i] != '"':
                if text[i] == "\\":
                    out[i] = " "
                    i += 1
                if i < n:
                    out[i] = " "
                    i += 1
            if i < n:
                out[i] = " "
                i += 1
        else:
            i += 1
    return "".join(out)


def match_paren(blank: str, open_idx: int) -> int:
    """Index of the ')' closing the '(' at `open_idx`."""
    depth = 0
    for j in range(open_idx, len(blank)):
        if blank[j] == "(":
            depth += 1
        elif blank[j] == ")":
            depth -= 1
            if depth == 0:
                return j
    raise ValueError("unbalanced parentheses")


@dataclass
class ParamItem:
    """One comma-separated entry of a parameter port list, as source slices."""

    start: int          # offset in the ORIGINAL text
    end: int            # exclusive
    kw_span: tuple[int, int] | None   # span of `parameter`/`localparam`, if present
    kw: str | None
    name: str
    value_span: tuple[int, int] | None  # span of the default expression
    is_type: bool


class TopModule:
    """The `module <top> #(...) (...)` header of one file."""

    def __init__(self, text: str, top: str):
        self.text = text
        self.top = top
        self.blank = strip_comments(text)
        m = re.search(r"\bmodule\s+" + re.escape(top) + r"\b", self.blank)
        if not m:
            raise ValueError(f"no `module {top}` in this file")
        self.mod_end = m.end()
        rest = self.blank[self.mod_end:]
        hash_m = re.match(r"\s*#\s*\(", rest)
        if hash_m:
            self.param_open = self.mod_end + rest.index("(", hash_m.start())
            self.param_close = match_paren(self.blank, self.param_open)
        else:
            self.param_open = self.param_close = None

    @property
    def has_param_list(self) -> bool:
        return self.param_open is not None

    def items(self) -> list[ParamItem]:
        if not self.has_param_list:
            return []
        lo, hi = self.param_open + 1, self.param_close
        pieces = _split_top_level(self.blank, lo, hi)
        out: list[ParamItem] = []
        for a, b in pieces:
            out.append(self._parse_item(a, b))
        return out

    def _parse_item(self, a: int, b: int) -> ParamItem:
        seg = self.blank[a:b]
        kw = kw_span = None
        m = re.search(r"\b(parameter|localparam)\b", seg)
        if m:
            kw = m.group(1)
            kw_span = (a + m.start(1), a + m.end(1))
        # `name = value` -- the name is the last identifier before the first
        # top-level '=', with every bracketed group removed first. A declaration
        # can carry identifiers on BOTH sides of the name: a packed range in the
        # type (`logic [W-1:0] Foo`) and an unpacked dimension after it
        # (`int Stages[NumDownstreams]`). Taking the last identifier without
        # dropping the brackets reads the second as the name, and then pinning
        # `NumDownstreams` overwrites the wrong parameter's default -- which is
        # exactly how br_csr_demux ended up declaring `int Stages[N] = 4`.
        eq = _find_top_level(self.blank, a, b, "=")
        is_type = bool(re.search(r"\bparameter\s+type\b|\blocalparam\s+type\b", seg))
        if eq is None:
            ids = re.findall(r"[A-Za-z_]\w*", _drop_brackets(seg))
            return ParamItem(a, b, kw_span, kw, ids[-1] if ids else "", None, is_type)
        ids = re.findall(r"[A-Za-z_]\w*", _drop_brackets(self.blank[a:eq]))
        name = ids[-1] if ids else ""
        vs = eq + 1
        while vs < b and self.blank[vs] in " \t\n":
            vs += 1
        ve = b
        while ve > vs and self.blank[ve - 1] in " \t\n":
            ve -= 1
        return ParamItem(a, b, kw_span, kw, name, (vs, ve), is_type)


def _drop_brackets(text: str) -> str:
    """Blank every `[...]` group -- packed ranges and unpacked dimensions alike."""
    out, depth = [], 0
    for c in text:
        if c == "[":
            depth += 1
        elif c == "]":
            depth = max(0, depth - 1)
            out.append(" ")
            continue
        out.append(" " if depth else c)
    return "".join(out)


def _split_top_level(blank: str, lo: int, hi: int) -> list[tuple[int, int]]:
    depth = 0
    out, start = [], lo
    for i in range(lo, hi):
        c = blank[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == "," and depth == 0:
            out.append((start, i))
            start = i + 1
    out.append((start, hi))
    return [(a, b) for a, b in out if blank[a:b].strip()]


def _find_top_level(blank: str, lo: int, hi: int, ch: str) -> int | None:
    depth = 0
    for i in range(lo, hi):
        c = blank[i]
        if c in "([{":
            depth += 1
        elif c in ")]}":
            depth -= 1
        elif c == ch and depth == 0:
            # `==`, `<=`, `>=`, `!=` are operators, not the assignment.
            if ch == "=" and (blank[i - 1] in "=<>!+-*/%&|^~" or blank[i + 1] == "="):
                continue
            return i
    return None


def freeze(text: str, top: str, values: dict[str, object]) -> tuple[str, dict[str, str]]:
    """Rewrite `top`'s parameter port list into localparams pinned to `values`.

    Returns the new text and the value each parameter ended up pinned to, so the
    caller can record what it chose rather than assume it.
    """
    mod = TopModule(text, top)
    if not mod.has_param_list:
        return text, {}
    edits: list[tuple[int, int, str]] = []
    pinned: dict[str, str] = {}
    for it in mod.items():
        if it.kw == "parameter":
            edits.append((*it.kw_span, "localparam"))
        if it.name in values and it.value_span:
            new = str(values[it.name])
            edits.append((*it.value_span, new))
            pinned[it.name] = new
        elif it.value_span:
            pinned[it.name] = text[it.value_span[0]:it.value_span[1]]
    for a, b, rep in sorted(edits, reverse=True):
        text = text[:a] + rep + text[b:]
    return text, pinned
