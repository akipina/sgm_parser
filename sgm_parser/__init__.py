"""
A small, modular reader/writer for Impossible Creatures ``.sgm`` files.

Quick start::

    from sgm_parser import Sgm

    model = Sgm.load("Bullgator.sgm")
    print(model.format)            # 'BOBJ' (creature) or 'NOBS' (scene)
    print(model.dump())            # the whole chunk tree
    for c in model.connections:    # combine seams
        print(c.name, c.slot_label, c.host_bone)

    model.materials[0].texture = "bullgator_torso.bmp"   # edit
    model.save("fixed.sgm")        # untouched chunks stay byte-identical

Layers (low to high): :mod:`sgm_parser.binary` -> :mod:`sgm_parser.chunks` ->
:mod:`sgm_parser.parser` -> :mod:`sgm_parser.model`. See the architecture/schema
docs for the design.
"""
from __future__ import annotations

from .binary import BinaryReader, BinaryWriter
from .chunks import REGISTRY, FormChunk, FourCCChunk, RawChunk
from .chunks.creature import Connection, Limb
from .constants import SlotType
from .model import Sgm
from .parser import ChunkParser
from .creature import (
    SgmModel, Patch, Bone, Material, Texture, LimbConnection,
    Animation, BoneTrack, V1Channel, read_sgm, write_sgm, write_sgm_file,
)
from . import anim, creature

__all__ = [
    "Sgm",
    "ChunkParser",
    "FourCCChunk",
    "FormChunk",
    "RawChunk",
    "Connection",
    "Limb",
    "SlotType",
    "REGISTRY",
    "BinaryReader",
    "BinaryWriter",
    # high-level creature model (the add-on's I/O API)
    "SgmModel", "Patch", "Bone", "Material", "Texture", "LimbConnection",
    "Animation", "BoneTrack", "V1Channel",
    "read_sgm", "write_sgm", "write_sgm_file",
    "creature", "anim",
]
