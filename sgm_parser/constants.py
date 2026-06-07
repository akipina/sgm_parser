"""
Format constants recovered by reverse-engineering (see SGM_SCHEMA.md).

Kept in one place so nothing else carries magic numbers. Confidence on each value
is documented in the schema; the slot codes below are verified byte-exact.
"""
from __future__ import annotations

from enum import IntEnum


class SlotType(IntEnum):
    """``LCON.flags2`` — which body-part slot a connection ring belongs to."""

    HEAD = 0x40
    TAIL = 0x80
    L_FRONT_LEG = 0x25
    R_FRONT_LEG = 0x26
    L_REAR_LEG = 0x29
    R_REAR_LEG = 0x2A
    L_SIDE_WING = 0x501
    R_SIDE_WING = 0x502
    L_TOP_WING = 0x301
    R_TOP_WING = 0x302
    L_ARM = 0x800015
    R_ARM = 0x800016

    @classmethod
    def label(cls, value: int) -> str:
        """Human label for a flags2 value, or a hex fallback for unknown codes."""
        try:
            return cls(value).name
        except ValueError:
            return f"0x{value:x}"


# Default value of the (still not fully understood) LCON.flags1 word.
FLAGS1_DEFAULT = 0x800

# Root form-types that identify the two container conventions.
FORMAT_CREATURE = "BOBJ"   # flat, combinable-creature model
FORMAT_SCENE = "NOBS"      # native Relic scene graph (structures/characters/projectiles)

# FourCC tags we have *fully decoded* (the creature/combine path).
DECODED_CREATURE_TAGS = {"VERS", "NAME", "VERT", "MTRL", "TXMP", "LIMB", "LCON"}

# Known-but-undecoded tags. They are registered as named stubs so the tree is
# self-describing and they round-trip losslessly; decoders can be filled in later.
STUB_CREATURE_TAGS = {"MIDX", "PRIM", "INFO", "BONE", "BANM", "CANM"}
STUB_SCENE_TAGS = {
    "SIGM", "SHDR", "NODE", "XFRM", "MTRX", "GGRP", "BIND", "PMSH", "GEOM",
    "BVOL", "CHAN", "DATA", "MRKR", "MRPH", "VTMP", "CMRA", "LGHT", "TEAM",
    "TXRF", "TXTR", "STXT", "IMAG", "ATTR", "XREF", "NOBS",
}
