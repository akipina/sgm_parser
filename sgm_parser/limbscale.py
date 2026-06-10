"""Read, edit, build and write a creature's ``<Name>.lsc`` limb-scale file.

The ``.lsc`` is a Lua ``LimbScale`` table of per-partner combine-scaling rules: when this
creature is combined with another, the matching rule says how much to scale a part and how to
attach it. Each rule (named ``InfoN`` — the number is just a unique key) carries:

* ``OtherCreatureName`` — the partner's short name (e.g. ``"ELEPHANT"``),
* ``Scale`` / ``Multiplier`` — size class and scale factor,
* ``IsTorsoCreature`` / ``IsLimbCreature`` — which role this rule applies to,
* ``Attach`` / ``Class`` and the optional ``MismatchedLegs`` flag.

It's not strictly required to launch (the Object Editor only *warns* ``couldn't read <NAME>.lsc``
and the creature still combines with default scaling), but shipping one removes the warning and
lets you tune how the creature combines. ``LimbScale()`` starts empty; ``.add(...)`` appends rules.
"""

from __future__ import annotations

from collections import OrderedDict
from typing import List, Optional

from .luatable import parse_assignment, dump_assignment

__all__ = ["LimbScale"]

_FIELD_ORDER = ("Scale", "OtherCreatureName", "Multiplier", "IsLimbCreature",
                "IsTorsoCreature", "MismatchedLegs", "Attach", "Class")


class LimbScale:
    """A creature's ``LimbScale`` table: ordered ``InfoN -> {field: value}`` combine rules."""

    def __init__(self, rules: "OrderedDict[str, OrderedDict]" = None, comment: str = ""):
        self.rules: "OrderedDict[str, OrderedDict]" = rules if rules is not None else OrderedDict()
        self.comment = comment

    # ---- construct ----
    @classmethod
    def from_lsc(cls, text: str) -> "LimbScale":
        name, table = parse_assignment(text)
        if name != "LimbScale" or not isinstance(table, dict):
            raise ValueError("not a LimbScale file")
        rules = OrderedDict((k, OrderedDict(v.items())) for k, v in table.items())
        return cls(rules)

    @classmethod
    def load(cls, path: str) -> "LimbScale":
        with open(path, "r", encoding="latin1") as fh:
            return cls.from_lsc(fh.read())

    # ---- edit ----
    def add(self, other_creature: str, scale: int = 3, multiplier: float = 1.0,
            is_torso: int = 0, is_limb: int = 1, attach: int = 4, class_: int = 32,
            mismatched_legs: Optional[int] = None) -> "LimbScale":
        """Append a combine-scaling rule against ``other_creature`` (its short name); chainable."""
        rule = OrderedDict()
        rule["Scale"] = scale
        rule["OtherCreatureName"] = other_creature.upper()
        rule["Multiplier"] = float(multiplier)
        rule["IsLimbCreature"] = is_limb
        rule["IsTorsoCreature"] = is_torso
        if mismatched_legs is not None:
            rule["MismatchedLegs"] = mismatched_legs
        rule["Attach"] = attach
        rule["Class"] = class_
        self.rules[f"Info{len(self.rules)}"] = rule
        return self

    def __len__(self) -> int:
        return len(self.rules)

    # ---- write ----
    def to_lsc(self) -> str:
        return dump_assignment("LimbScale", self.rules, comment=self.comment)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="latin1", newline="\n") as fh:
            fh.write(self.to_lsc())
