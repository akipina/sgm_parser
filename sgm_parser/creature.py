"""High-level creature model — the single editable view the Blender add-on builds and
serialises, layered directly on the byte-faithful chunk model in this package.

This replaces the add-on's former standalone ``sgm_io`` codec. There is now one parser:
``read_sgm`` parses with :class:`~sgm_parser.model.Sgm` and reads the typed chunk fields;
``write_sgm`` constructs the chunk tree from this model (via the chunks' own encoders and
builders) and serialises it. Untouched creatures therefore round-trip byte-for-byte, and
the operators keep the same ``SgmModel`` / ``Patch`` / ``Bone`` / ``Material`` / ``Texture``
/ ``Limb`` / ``LimbConnection`` API they had before.

Animations use :mod:`sgm_parser.anim` (the v1/v7 channel semantics).
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional, Tuple

from .model import Sgm
from .parser import ChunkParser
from .chunks.base import FormChunk, RawChunk
from .chunks.common import NameChunk, VersChunk
from .chunks.creature import (
    VertChunk, MtrlChunk, TxmpChunk, BoneChunk, LimbChunk, LconChunk,
    MidxChunk, InfoChunk, PrimChunk, Limb as _CoreLimb, Connection as _CoreConn,
)
from . import anim as _anim
from .anim import Animation, BoneTrack, V1Channel   # re-exported for the add-on

__all__ = [
    "SgmModel", "Patch", "Bone", "Material", "Texture", "Limb", "LimbConnection",
    "Animation", "BoneTrack", "V1Channel", "read_sgm", "write_sgm", "write_sgm_file",
]

_COLOUR_TAGS = ("AMBC", "DIFC")
_FLAG_TAGS = ("SMTH", "2SID", "TILE")
_TEX_TAGS = ("TXTR", "TXRF", "TXSP")


# --------------------------------------------------------------------------- #
# Operator-facing value types (field-compatible with the former sgm_io).
# --------------------------------------------------------------------------- #
@dataclass
class Bone:
    name: str
    parent: Optional[int]
    start_vertex: int
    vertex_count: int
    transform: List[float]
    tail: List[float] = field(default_factory=list)
    meta0: int = 0
    meta1: int = 0
    meta5: int = 0


@dataclass
class Patch:
    material_index: int
    type: int
    control_points: List[int]
    uvs: List[float]


@dataclass
class Material:
    name: str
    tags: List[Tuple[str, Any]] = field(default_factory=list)


@dataclass
class Texture:
    name: str
    palette_entries: int
    format_code: int
    width: int
    height: int
    mip_count: int
    palette: bytes
    mips: List[bytes]


@dataclass
class Limb:
    name: str
    limb_id: int
    prims: List[int]
    bone_names: List[str]


@dataclass
class LimbConnection:
    name: str
    flags1: int
    flags2: int
    vertex_indices: List[int]
    bone_name: str
    zero_words: int = 2


@dataclass
class SgmModel:
    version: Tuple[int, int, int, int] = (0, 0, 1, 3)
    name: str = "Creature"
    vertices: List[Tuple[float, float, float]] = field(default_factory=list)
    patches: List[Patch] = field(default_factory=list)
    material_index: List[str] = field(default_factory=list)
    bones: List[Bone] = field(default_factory=list)
    materials: List[Material] = field(default_factory=list)
    textures: List[Texture] = field(default_factory=list)
    limbs: List[Limb] = field(default_factory=list)
    limb_connections: List[LimbConnection] = field(default_factory=list)
    animations: List[Animation] = field(default_factory=list)
    ikch_raw: Optional[bytes] = None
    anim_raw: List[bytes] = field(default_factory=list)
    skel_info: Optional[int] = None


# --------------------------------------------------------------------------- #
# Reader: file -> SgmModel  (vertices kept in local/un-deformed space).
# --------------------------------------------------------------------------- #
def _material_from_chunk(ch: MtrlChunk) -> Material:
    tags: List[Tuple[str, Any]] = []
    for tag, value in ch.fields:
        if tag in _TEX_TAGS:
            tags.append((tag, value))                      # filename string
        elif tag in _COLOUR_TAGS:
            tags.append((tag, tuple(value)))               # 4 raw bytes
        elif tag in _FLAG_TAGS:
            tags.append((tag, struct.unpack(">I", value)[0]))   # big-endian flag word
        else:                                              # any other 4-byte field
            tags.append((tag, struct.unpack(">I", value)[0]))
    return Material(ch.name, tags)


def _texture_from_chunk(ch: TxmpChunk) -> Optional[Texture]:
    if not ch._parsed:
        return None
    pe = ch.palette_count & 0xFFFF
    fmt = ch.palette_count >> 16
    mips: List[bytes] = []
    px = ch.pixels
    off = 0
    for i in range(ch.mip_count):
        w = max(1, ch.width >> i); h = max(1, ch.height >> i)
        mips.append(px[off:off + w * h]); off += w * h
    return Texture(ch.filename, pe, fmt, ch.width, ch.height, ch.mip_count, ch.palette, mips)


def read_sgm(path: str) -> SgmModel:
    sgm = Sgm.load(path)
    m = SgmModel()

    vers = sgm.find("VERS")
    if vers is not None and len(vers.raw) >= 4:
        m.version = tuple(vers.raw[i] for i in range(4))

    mesh = sgm.find("MESH")
    if isinstance(mesh, FormChunk):
        nm = next((c for c in mesh.children if c.tag == "NAME"), None)
        if nm is not None:
            m.name = nm.raw.split(b"\x00", 1)[0].decode("latin1", "ignore")
        vert = next((c for c in mesh.children if c.tag == "VERT"), None)
        if vert is not None:
            m.vertices = list(vert.points)
        midx = next((c for c in mesh.children if c.tag == "MIDX"), None)
        if midx is not None:
            m.material_index = list(midx.names)
        prim = next((c for c in mesh.children if c.tag == "PRIM"), None)
        if prim is not None and prim._full:
            m.patches = [Patch(mat, t, list(cps), list(uvs))
                         for mat, t, cps, uvs in prim.records]

    skel = sgm.find("SKEL")
    if isinstance(skel, FormChunk):
        info = next((c for c in skel.children if c.tag == "INFO" and len(c.raw) == 4), None)
        if info is not None:
            m.skel_info = struct.unpack("<I", info.raw)[0]
        for b in (c for c in skel.children if c.tag == "BONE" and getattr(c, "_full", False)):
            m.bones.append(Bone(
                name=b.name, parent=b.parent,
                start_vertex=b.counts[0], vertex_count=b.counts[1],
                transform=list(b.matrix), tail=list(b.tail),
                meta0=b._head[0], meta1=b._head[1], meta5=b._head[5]))

    m.materials = [_material_from_chunk(c) for c in sgm.find_all("MTRL")]
    for c in sgm.find_all("TXMP"):
        tx = _texture_from_chunk(c)
        if tx is not None:
            m.textures.append(tx)

    limb = sgm.find("LIMB")
    if isinstance(limb, LimbChunk):
        for lm in limb.limbs:
            m.limbs.append(Limb(lm.name, lm.limb_id, lm.patch_indices(), list(lm.bones)))

    lcon = sgm.find("LCON")
    if isinstance(lcon, LconChunk):
        for c in lcon.connections:
            flat = [v for edge in c.edges for v in edge]
            m.limb_connections.append(
                LimbConnection(c.name, c.flags1, c.slot, flat, c.host_bone, c.pad_words))

    bonm = sgm.find("BONM")
    if isinstance(bonm, FormChunk):
        ik = next((c for c in bonm.children if c.tag == "IKCH"), None)
        if ik is not None:
            m.ikch_raw = ik.serialize()

    for af in sgm.find_all("ANIM"):
        if not isinstance(af, FormChunk):
            continue
        parsed = _anim.read_anim(af)
        if parsed is not None:
            m.animations.append(parsed)
        else:
            m.anim_raw.append(af.serialize())     # keep verbatim
    return m


# --------------------------------------------------------------------------- #
# Writer: SgmModel -> bytes.
# --------------------------------------------------------------------------- #
def _name_chunk(name: str) -> NameChunk:
    return NameChunk("NAME", name.encode("latin1", "ignore"))   # raw, unprefixed


def _vert_chunk(vertices) -> VertChunk:
    c = VertChunk("VERT", b"")
    c.points = [(float(x), float(y), float(z)) for x, y, z in vertices]
    c.mark_dirty()
    return c


def _midx_chunk(names) -> MidxChunk:
    c = MidxChunk("MIDX", b"")
    c.names = list(names)
    c.mark_dirty()
    return c


def _mtrl_chunk(mat: Material) -> MtrlChunk:
    fields = []
    for tag, value in mat.tags:
        if tag in _TEX_TAGS:
            fields.append([tag, value])
        elif tag in _COLOUR_TAGS:
            fields.append([tag, bytes(value)])
        else:                                       # flag / other 4-byte word (big-endian)
            fields.append([tag, struct.pack(">I", value)])
    c = MtrlChunk("MTRL", b"")
    c._name = mat.name
    c.fields = fields
    c.mark_dirty()
    return c


def _txmp_chunk(tx: Texture) -> TxmpChunk:
    return TxmpChunk.build(tx.name, tx.palette_entries, tx.format_code,
                           tx.width, tx.height, tx.palette, tx.mips)


def _limb_chunk(limbs: List[Limb], total_patches: int) -> LimbChunk:
    nwords = (total_patches + 31) // 32
    core = []
    for lm in limbs:
        words = [0] * nwords
        for pi in lm.prims:
            words[pi >> 5] |= (1 << (pi & 31))
        mask = struct.pack("<%dI" % nwords, *words) if nwords else b""
        core.append(_CoreLimb(lm.name, lm.limb_id, mask, list(lm.bone_names)))
    c = LimbChunk("LIMB", b"")
    c.total_patches = total_patches
    c.limbs = core
    c.mark_dirty()
    return c


def _lcon_chunk(entries: List[LimbConnection]) -> LconChunk:
    core = []
    for e in entries:
        edges = [(e.vertex_indices[i], e.vertex_indices[i + 1])
                 for i in range(0, len(e.vertex_indices), 2)]
        core.append(_CoreConn(e.name, e.flags2, edges, e.bone_name, e.flags1, e.zero_words))
    c = LconChunk("LCON", b"")
    c.connections = core
    c.mark_dirty()
    return c


def write_sgm(m: SgmModel) -> bytes:
    """Serialise an SgmModel to SGM bytes via the chunk tree (byte-identical for an
    unedited round-trip): FORM BOBJ { VERS, TXMP*, MTRL*, FORM BONM { MESH, SKEL, LIMB,
    [IKCH], LCON }, ANIM* }."""
    mesh = FormChunk("MESH", [
        _name_chunk(m.name),
        _vert_chunk(m.vertices),
        _midx_chunk(m.material_index),
        PrimChunk.build((p.material_index, p.type, p.control_points, p.uvs) for p in m.patches),
    ], b"")
    mesh.mark_dirty()

    skel_count = m.skel_info if m.skel_info is not None else len(m.bones)
    skel_children = [InfoChunk("INFO", struct.pack("<I", skel_count))]
    for b in m.bones:
        skel_children.append(BoneChunk.build(
            b.name, b.parent, b.start_vertex, b.vertex_count,
            b.transform, b.tail, b.meta0, b.meta1, b.meta5))
    skel = FormChunk("SKEL", skel_children, b"")
    skel.mark_dirty()

    bonm_children = [mesh, skel, _limb_chunk(m.limbs, len(m.patches))]
    if m.ikch_raw:
        bonm_children.append(RawChunk("IKCH", m.ikch_raw[8:]))   # strip tag+size header
    bonm_children.append(_lcon_chunk(m.limb_connections))
    bonm = FormChunk("BONM", bonm_children, b"")
    bonm.mark_dirty()

    bobj_children = [VersChunk("VERS", bytes(m.version))]
    bobj_children += [_txmp_chunk(t) for t in m.textures]
    bobj_children += [_mtrl_chunk(mat) for mat in m.materials]
    bobj_children.append(bonm)
    bobj_children += [_anim.write_anim(a) for a in m.animations]
    for raw in m.anim_raw:
        bobj_children.append(ChunkParser().parse(raw)[0])        # re-attach verbatim
    bobj = FormChunk("BOBJ", bobj_children, b"")
    bobj.mark_dirty()
    return bobj.serialize()


def write_sgm_file(m: SgmModel, path: str) -> None:
    Path(path).write_bytes(write_sgm(m))
