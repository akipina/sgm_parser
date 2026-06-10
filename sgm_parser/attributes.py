"""Read, edit, build and write a creature's ``<Name>.lua`` controller/attribute file.

In Impossible Creatures every combinable creature has a ``limbattributes`` Lua table next to its
``.sgm``/``.ebp``. The engine *requires* it: without one the combiner logs ``Failed to load
controller lua`` / ``Could not find size`` and crashes. Each entry is ``key = {group, value}``
where ``group`` is the body part the attribute applies to (1 = whole creature, 2 = front legs,
3 = back legs, 4 = head, 5 = tail, 6 = torso, 7 = wings, 0 = creature-wide flags) and ``value``
is the number (hit points, armour, a speed, an ability on/off, or a ``$``-less locale id).

Usage::

    from sgm_parser import Attributes
    a = Attributes.default(size=4)          # complete, valid baseline
    a["hitpoints-torso"] = 200              # tweak by key (keeps the body-part group)
    a.save("MyCreature.lua")

    b = Attributes.load("cheetah.lua")      # or start from a stock creature
    b.comment = "MyCreature (from cheetah)"
    b.save("MyCreature.lua")
"""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
from typing import Dict, List

from .luatable import parse_assignment, dump_assignment

__all__ = ["Attributes"]

# A complete, valid baseline (the standard attribute set every creature carries). Each row is
# (key, body-part group, value). Override what you need; the engine needs the whole set present.
_BASELINE: List = [
    ("isFromExcel", 1, 1.0), ("endurance_bonus", 1, 0), ("size", 1, 3),
    ("sight_radius1", 4, 25), ("night_sight_radius", 4, 0), ("stocktype", 1, 5),
    ("armour-head", 4, 0.08), ("armour-front", 2, 0.0), ("armour-back", 3, 0.0),
    ("armour-torso", 6, 0.16), ("exp_armour", 1, 0), ("exp_dodge", 1, -0.125),
    ("hitpoints-head", 4, 45), ("hitpoints-front", 2, 0), ("hitpoints-back", 3, 0),
    ("hitpoints-torso", 6, 114), ("exp_hitpoints", 1, 1.4),
    ("speed_mid-front", 2, 0), ("speed_mid-torso", 6, 0), ("speed_mid-back", 3, 0),
    ("exp_speed_mid", 1, 0.025), ("speed_max-front", 2, 0), ("speed_max-torso", 6, 0),
    ("speed_max-back", 3, 0), ("exp_speed_max", 1, -0.18),
    ("airspeed_max-torso", 6, 0), ("airspeed_max-wings", 7, 0), ("exp_airspeed_max", 1, -0.16),
    ("waterspeed_max-torso", 6, 0), ("waterspeed_max-tail", 5, 0), ("exp_waterspeed_max", 1, -0.04),
    ("melee4_number", 4, 1), ("melee4_dmgtype", 4, 0), ("melee4_damage", 4, 10),
    ("melee4_rate", 4, 1.25), ("melee4_contact", 4, 0.48), ("melee4_name", 4, 5573),
    ("melee4_shortdesc", 4, 36591), ("melee4_longdesc", 4, 36496),
    ("melee_damage", 1, 9), ("exp_melee4_damage", 1, 0.8),
    ("stink_attack", 5, 0), ("electric_burst", 5, 0), ("charge_attack", 3, 0),
    ("frenzy_attack", 0, 0), ("plague_attack", 4, 0), ("quill_burst", 6, 0),
    ("web_throw", 5, 0), ("assassinate", 5, 0), ("flash", 5, 0), ("infestation", 4, 0),
    ("can_dig", 2, 0), ("can_SRF", 6, 0), ("pack_hunter", 0, 0), ("herding", 0, 0),
    ("loner", 5, 0), ("leap_attack", 5, 0), ("poison_touch", 5, 0), ("is_stealthy", 5, 0),
    ("is_immune", 0, 0), ("regeneration", 0, 0), ("deflection_armour", 6, 0),
    ("AutoDefense", 5, 0), ("soiled_land", 3, 0), ("keen_sense", 4, 0), ("sonar_pulse", 4, 0),
    ("end_bonus", 6, 0), ("front_foot_type", 2, 0), ("rear_foot_type", 3, 0),
    ("vocal_type", 4, 38), ("Hide_type", 6, 4), ("actbeselect", 0, 1),
    ("selection_sloppyness", 6, 2), ("boneblobshadow", 0, 1), ("simvis_occludee", 0, 1),
    ("singleselectonly", 0, 0), ("isvisible", 0, 1), ("fadeAndDeleteWhenDead", 0, 1),
    ("stayInPathfindingAfterDead", 0, 0), ("min_triangle_count", 1, 175),
    ("simterrain", 0, 0), ("simcollides", 0, 1), ("simfogged", 0, 1),
    ("combinervisible", 0, 1), ("minimap_enable", 0, 1), ("minimap_teamcolour", 0, 1),
    ("ghost_enable", 0, 0), ("tag_desc", 1, 36590),
    ("lefthalf_name", 1, 36588), ("righthalf_name", 1, 36589),
]


class Attributes:
    """A creature's ``limbattributes`` table: an ordered map of ``key -> [group, value]``."""

    def __init__(self, entries: "OrderedDict[str, List]", comment: str = ""):
        self.entries: "OrderedDict[str, List]" = entries
        self.comment = comment

    # ---- construct ----
    @classmethod
    def default(cls, size: int = 3, comment: str = "") -> "Attributes":
        """A complete, valid baseline attribute set; pass ``size`` (1..9) for the creature size."""
        entries = OrderedDict((k, [g, v]) for k, g, v in deepcopy(_BASELINE))
        entries["size"][1] = size
        return cls(entries, comment)

    @classmethod
    def from_lua(cls, text: str) -> "Attributes":
        name, table = parse_assignment(text)
        if name != "limbattributes" or not isinstance(table, dict):
            raise ValueError("not a limbattributes file")
        entries = OrderedDict((k, list(v)) for k, v in table.items())
        comment = ""
        for line in text.splitlines():
            s = line.strip()
            if s.startswith("--"):
                comment = s[2:].strip()
                break
            if s:
                break
        return cls(entries, comment)

    @classmethod
    def load(cls, path: str) -> "Attributes":
        with open(path, "r", encoding="latin1") as fh:
            return cls.from_lua(fh.read())

    # ---- edit ----
    def __getitem__(self, key: str):
        return self.entries[key][1]

    def __setitem__(self, key: str, value) -> None:
        if key in self.entries:
            self.entries[key][1] = value
        else:
            self.entries[key] = [1, value]

    def __contains__(self, key: str) -> bool:
        return key in self.entries

    def set(self, key: str, value, group: int = None) -> "Attributes":
        """Set ``key``'s value (and optionally its body-part group); chainable."""
        if key in self.entries:
            self.entries[key][1] = value
            if group is not None:
                self.entries[key][0] = group
        else:
            self.entries[key] = [1 if group is None else group, value]
        return self

    # ---- write ----
    def to_lua(self) -> str:
        return dump_assignment("limbattributes", self.entries, comment=self.comment)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="latin1", newline="\n") as fh:
            fh.write(self.to_lua())
