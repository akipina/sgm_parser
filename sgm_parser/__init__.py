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
from .ebp import Ebp, MotionNode, Event, retarget, build_creature_ebp
from .attributes import Attributes
from .limbscale import LimbScale
from .validate import check_model, check_creature_assets, write_torso_bmp
from .scene import Scene, SceneMesh, SceneBone, Submesh, read_scene, is_scene, read_txr
from . import anim, creature, ebp, attributes, limbscale, luatable, validate, scene

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
    # entity blueprint (.ebp) parser + generator
    "Ebp", "MotionNode", "Event", "retarget", "build_creature_ebp",
    # creature definition files (.lua attributes, .lsc limb-scale)
    "Attributes", "LimbScale",
    # validation (every known in-game crash cause)
    "check_model", "check_creature_assets", "write_torso_bmp",
    "creature", "anim", "ebp", "attributes", "limbscale", "luatable", "validate",
]
