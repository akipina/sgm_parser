"""A minimal reader/writer for the Lua *data-table* subset Impossible Creatures uses.

This is **not** a Lua interpreter. IC's creature definition files —  ``.lua`` (``limbattributes``)
and ``.lsc`` (``LimbScale``) — and the combiner's ``stock.lua`` are all a single top-level
``name = { ... }`` assignment whose tables hold only numbers, strings and nested tables, keyed
(``k = v`` or ``["k"] = v``) or positional (``{1, 2.0}`` / ``{"A", "B"}``), with ``--`` line
comments. This module parses exactly that into plain Python (``dict`` / ``list`` / ``int`` /
``float`` / ``str``) and serialises valid Lua back, so the higher-level creature-file modules
can load a stock file, edit it, and write one the game parses.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

__all__ = ["parse", "parse_assignment", "dump", "dump_assignment"]

_TOKEN = re.compile(
    r"""
      [ \t\r\n]+                       # whitespace
    | --[^\n]*                         # line comment
    | (?P<longstr>\[\[[\s\S]*?\]\])    # [[long string]] (Lua), may be empty / span lines
    | (?P<str>"(?:[^"\\]|\\.)*")       # "string"
    | (?P<num>-?\d+\.?\d*(?:[eE][-+]?\d+)?)
    | (?P<name>[A-Za-z_]\w*)           # bare identifier
    | (?P<punc>[{}\[\]=,])
    """,
    re.VERBOSE,
)


def _tokenize(text: str) -> List[Tuple[str, str]]:
    out: List[Tuple[str, str]] = []
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            raise ValueError(f"lua parse error near {text[pos:pos + 30]!r}")
        pos = m.end()
        kind = m.lastgroup
        if kind:                       # skip whitespace/comments (no capture group)
            out.append((kind, m.group()))
    out.append(("eof", ""))
    return out


def _number(tok: str):
    return float(tok) if ("." in tok or "e" in tok or "E" in tok) else int(tok)


def _unquote(tok: str) -> str:
    return tok[1:-1].encode("latin1", "ignore").decode("unicode_escape")


class _Parser:
    def __init__(self, toks: List[Tuple[str, str]]):
        self.toks, self.i = toks, 0

    def _peek(self):
        return self.toks[self.i]

    def _next(self):
        t = self.toks[self.i]
        self.i += 1
        return t

    def _expect(self, val: str):
        k, v = self._next()
        if v != val:
            raise ValueError(f"expected {val!r}, got {v!r}")

    def value(self) -> Any:
        kind, val = self._next()
        if kind == "str":
            return _unquote(val)
        if kind == "longstr":
            return val[2:-2]               # [[...]] -> raw inner text (no escape processing)
        if kind == "num":
            return _number(val)
        if val == "{":
            return self._table()
        raise ValueError(f"unexpected token {val!r}")

    def _table(self):
        items: List[Tuple[Any, Any]] = []     # (key_or_None, value)
        while True:
            kind, val = self._peek()
            if val == "}":
                self._next()
                break
            key = None
            if val == "[":                     # ["key"] = v  or  [1] = v
                self._next()
                kk, kv = self._next()
                key = _unquote(kv) if kk == "str" else _number(kv)
                self._expect("]")
                self._expect("=")
            elif kind == "name" and self.toks[self.i + 1][1] == "=":
                key = val
                self._next()                   # name
                self._next()                   # '='
            items.append((key, self.value()))
            if self._peek()[1] == ",":
                self._next()
        if items and all(k is None for k, _ in items):
            return [v for _, v in items]        # positional -> list
        return {(i if k is None else k): v for i, (k, v) in enumerate(items)}


def parse(text: str) -> Any:
    """Parse a single Lua value (a ``{...}`` table or a scalar) into Python."""
    return _Parser(_tokenize(text)).value()


def parse_assignment(text: str) -> Tuple[str, Any]:
    """Parse a ``name = <value>`` file; return ``(name, python_value)``. Leading ``--`` comment
    lines are ignored."""
    toks = _tokenize(text)
    p = _Parser(toks)
    kind, name = p._next()
    if kind != "name":
        raise ValueError("expected a top-level 'name = ...' assignment")
    p._expect("=")
    return name, p.value()


# --------------------------------------------------------------------------- #
# Serialisation
# --------------------------------------------------------------------------- #
def _fmt_scalar(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float):
        s = repr(v)
        return s
    if isinstance(v, int):
        return str(v)
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _fmt_key(k: Any) -> str:
    if isinstance(k, int):
        return f"[{k}]"
    if re.fullmatch(r"[A-Za-z_]\w*", str(k)):
        return str(k)                          # bare identifier
    return f'["{k}"]'


def dump(value: Any, indent: int = 0, _level: int = 0) -> str:
    """Serialise a Python value to Lua source. ``indent`` is spaces per level (0 = compact).
    A "leaf" table (no nested table inside) is always written inline, so an attribute pair
    ``{1, 3}`` or a single LimbScale rule stays on one line while the outer table is laid out."""
    if not isinstance(value, (dict, list)):
        return _fmt_scalar(value)
    members = value if isinstance(value, list) else list(value.values())
    leaf = not any(isinstance(v, (dict, list)) for v in members)
    block = bool(indent) and not leaf
    pad = " " * (indent * (_level + 1)) if block else ""
    end = " " * (indent * _level) if block else ""
    nl = "\n" if block else ""
    sep = ("," + nl) if block else ", "
    if isinstance(value, list):
        parts = [pad + dump(v, indent, _level + 1) for v in value]
    else:
        parts = [pad + _fmt_key(k) + " = " + dump(v, indent, _level + 1)
                 for k, v in value.items()]
    if not parts:
        return "{}"
    return "{" + nl + sep.join(parts) + nl + end + "}"


def dump_assignment(name: str, value: Any, indent: int = 2, comment: str = "") -> str:
    """Serialise ``name = <value>`` as a complete file (optional leading ``-- comment``)."""
    head = f"-- {comment}\n" if comment else ""
    return f"{head}{name} = {dump(value, indent)}\n"
