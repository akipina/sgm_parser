"""
fxlua.py -- read/edit/write Impossible Creatures particle-effect definitions (``art/fx/<name>.lua``).

An FX `.lua` is ``fx = { properties = {property_NN = {name,type,value}}, style = "STYLE_*",
frames = {timeStart = {t0, t1, ...}} }``. ``style`` is the particle type (TRAIL, SPRAY, RING, BEAM,
COMBO, CONDITIONAL). Properties are typed:

* VARTYPE_FLOAT / INT / BOOL / STRING  -- scalar
* VARTYPE_COLOUR                        -- [r, g, b, a]
* VARTYPE_ARRAY_FLOAT / ARRAY_COLOUR / ARRAY_VECTOR3 -- KEYFRAMED: one value per ``frames.timeStart``
  entry (so e.g. ``Emitter_Rate = {50, 60, 0, 0}`` is the rate at times {0, 0.5, 0.53, 1}).

Backed by :mod:`sgm_parser.luatable` (semantic round-trip; output is reformatted but re-parses
identically -- fine because the game re-parses the text). Editing keeps the full parsed table as the
source of truth and mutates values in place, so untouched fields are preserved.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, List, Optional

from . import luatable


@dataclass
class FxEffect:
    style: str
    table: dict                       # the full parsed `fx` table (source of truth for write)
    path: str = ""

    @property
    def properties(self):
        """List of ``(slot, name, type, value)`` in slot order (property_00, property_01, ...)."""
        props = self.table.get("properties", {})
        items = sorted(props.items()) if isinstance(props, dict) else list(enumerate(props))
        out = []
        for slot, p in items:
            if isinstance(p, dict) and "name" in p:
                out.append((slot, p["name"], p.get("type", ""), p.get("value")))
        return out

    def _prop(self, name: str):
        props = self.table.get("properties", {})
        seq = props.values() if isinstance(props, dict) else props
        for p in seq:
            if isinstance(p, dict) and p.get("name") == name:
                return p
        return None

    def get(self, name: str, default=None):
        p = self._prop(name)
        return p.get("value") if p else default

    def set(self, name: str, value) -> bool:
        """Set a property's value in place (preserving its slot/type). Returns False if absent."""
        p = self._prop(name)
        if p is None:
            return False
        p["value"] = value
        return True

    @property
    def frame_times(self) -> Optional[List[float]]:
        fr = self.table.get("frames", {})
        ts = fr.get("timeStart") if isinstance(fr, dict) else None
        return list(ts) if isinstance(ts, (list, tuple)) else None

    @property
    def texture(self) -> Optional[str]:
        return self.get("Texture")

    @property
    def mesh(self) -> Optional[str]:
        return self.get("Mesh")


def read_fx(path: str) -> FxEffect:
    """Parse an FX ``.lua`` file into an :class:`FxEffect`."""
    with open(path, "r", encoding="latin1") as fh:
        name, tbl = luatable.parse_assignment(fh.read())
    if not isinstance(tbl, dict):
        raise ValueError("not an fx = {...} table")
    return FxEffect(style=tbl.get("style", ""), table=tbl, path=path)


def read_fx_text(text: str, path: str = "") -> FxEffect:
    name, tbl = luatable.parse_assignment(text)
    return FxEffect(style=tbl.get("style", ""), table=tbl, path=path)


def dump_fx(fx: FxEffect) -> str:
    """Serialize an :class:`FxEffect` back to Lua text (``fx = {...}``)."""
    return luatable.dump_assignment("fx", fx.table)


def write_fx(fx: FxEffect, path: str = None) -> str:
    out = path or fx.path
    with open(out, "w", encoding="latin1") as fh:
        fh.write(dump_fx(fx))
    return out
