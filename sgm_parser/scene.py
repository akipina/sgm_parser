"""Read Impossible Creatures *scene* ``.sgm`` files — the non-creature variant used by
characters, gatherers, buildings and props.

Creatures use the patch format (``FORM BOBJ`` -> ``MESH``/``PRIM``/``SKEL``); everything else uses
the RDN **scene graph**: ``FORM NOBS`` marker + ``FORM SIGM`` holding a transform hierarchy, polygon
meshes (``PMSH`` — a "ProgressiveMesh"), Biped skeleton (``SKEL``), animations (``ANIM``, same as
creatures), shaders (``SHDR``) and external texture refs (``TXRF``).

This module decodes those into a plain :class:`Scene` (no bpy) so an importer can build a rigged mesh:
positions/normals/UVs/triangles grouped by material, per-vertex skin weights (4 bones each), the bone
hierarchy with bind-pose transforms, and the animation names. Geometry/skin layout was recovered from
the Object Editor's ``ProgressiveMesh.cpp`` reader and validated across villager/henchmen/rex/coal-bin.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .model import Sgm
from .chunks.base import FormChunk
from .anim import Animation, BoneTrack       # data containers only; scene has its own decode below

# PMSH vertex elements, by format-mask bit -> byte size. Streams are structure-of-arrays:
# for each set bit, the file holds vertexCount * size bytes, in bit order.
_ELEM_SIZE = {0: 12, 1: 16, 2: 12, 3: 4, 4: 4, 5: 8, 6: 8, 7: 8, 8: 8}
_POS, _SKIN, _NORMAL, _UV = 0, 1, 2, 5     # the elements we decode


@dataclass
class Submesh:
    material: str
    triangles: List[Tuple[int, int, int]]


@dataclass
class SceneMesh:
    name: str
    positions: List[Tuple[float, float, float]] = field(default_factory=list)
    normals: List[Tuple[float, float, float]] = field(default_factory=list)
    uvs: List[Tuple[float, float]] = field(default_factory=list)
    submeshes: List[Submesh] = field(default_factory=list)
    # per-vertex skin: list of (bone_index, weight) pairs (empty list => rigid/unskinned)
    skin: List[List[Tuple[int, float]]] = field(default_factory=list)
    transform: Optional[List[float]] = None      # owning NODE's MTRX (3x4, row-major) if any

    @property
    def skinned(self) -> bool:
        return any(self.skin)


@dataclass
class SceneBone:
    name: str
    parent: int                       # index into the bone list, -1 for root
    transform: List[float]            # 4x3 world bind matrix (3 rows of 4: basis + translation)

    @property
    def head(self) -> Tuple[float, float, float]:
        t = self.transform
        return (t[9], t[10], t[11]) if len(t) >= 12 else (0.0, 0.0, 0.0)


@dataclass
class Scene:
    name: str = ""
    meshes: List[SceneMesh] = field(default_factory=list)
    bones: List[SceneBone] = field(default_factory=list)
    textures: List[str] = field(default_factory=list)     # "Data:Art/Textures/NAME.txr"
    materials: List[str] = field(default_factory=list)     # SHDR names
    animations: List = field(default_factory=list)         # anim.Animation (v7, decoded)

    @property
    def animation_names(self) -> List[str]:
        return [a.name for a in self.animations]


# --------------------------------------------------------------------------- helpers
def _walk(sgm: Sgm, tag: str) -> List:
    out = []
    def rec(c):
        if c.tag == tag or (isinstance(c, FormChunk) and c.form_type == tag):
            out.append(c)
        if isinstance(c, FormChunk):
            for ch in c.children:
                rec(ch)
    for c in sgm.root_chunks:
        rec(c)
    return out


def _lp_le(b: bytes, o: int) -> Tuple[str, int]:
    n = struct.unpack_from("<I", b, o)[0]
    return b[o + 4:o + 4 + n].decode("latin1", "ignore"), o + 4 + n


def is_scene(sgm: Sgm) -> bool:
    """True if this ``.sgm`` is a scene (NOBS/SIGM) rather than a creature (BOBJ)."""
    tops = [c.form_type for c in sgm.root_chunks if isinstance(c, FormChunk)]
    return "NOBS" in tops or "SIGM" in tops


# --------------------------------------------------------------------------- PMSH
def decode_pmsh(data: bytes, name: str = "") -> SceneMesh:
    """Decode one ``PMSH`` ``DATA`` blob (ProgressiveMesh) into a :class:`SceneMesh`."""
    nverts, mask = struct.unpack_from("<II", data, 0)
    o = 8
    streams = {}
    for b in range(9):
        if mask >> b & 1:
            streams[b] = o
            o += nverts * _ELEM_SIZE[b]

    mesh = SceneMesh(name=name)
    po = streams[_POS]
    mesh.positions = [struct.unpack_from("<3f", data, po + i * 12) for i in range(nverts)]
    if _NORMAL in streams:
        no = streams[_NORMAL]
        mesh.normals = [struct.unpack_from("<3f", data, no + i * 12) for i in range(nverts)]
    if _UV in streams:
        uo = streams[_UV]
        mesh.uvs = [struct.unpack_from("<2f", data, uo + i * 8) for i in range(nverts)]
    if _SKIN in streams:                      # 3 float weights + 4 u8 bone indices (255 = none)
        so = streams[_SKIN]
        skin = []
        for i in range(nverts):
            w0, w1, w2 = struct.unpack_from("<3f", data, so + i * 16)
            idx = struct.unpack_from("<4B", data, so + i * 16 + 12)
            w = [w0, w1, w2, max(0.0, 1.0 - (w0 + w1 + w2))]
            skin.append([(bi, wt) for bi, wt in zip(idx, w) if bi != 0xFF and wt > 0.0])
        mesh.skin = skin

    nsub = struct.unpack_from("<I", data, o)[0]
    o += 4
    for _ in range(nsub):
        shader, o = _lp_le(data, o)
        index_count = struct.unpack_from("<I", data, o)[0]
        o += 4 + 2                            # u32 index_count, then a u16
        k = struct.unpack_from("<I", data, o)[0]
        o += 4
        if k:
            o += k * 8                        # skin/remap block, skipped
        if index_count == 0:
            continue
        idx = struct.unpack_from("<%dH" % index_count, data, o)
        o += index_count * 2
        tris = [(idx[t], idx[t + 1], idx[t + 2]) for t in range(0, index_count - 2, 3)]
        mesh.submeshes.append(Submesh(material=shader, triangles=tris))
    return mesh


# --------------------------------------------------------------------------- bones
def _decode_bone(raw: bytes) -> Optional[SceneBone]:
    """Scene ``BONE``: LE-lpstr name, u32 flags, u32 colour, i32 parent, then transform data.
    A 4-char 'eolo' marker precedes the 4x3 world matrix (12 floats)."""
    try:
        name, o = _lp_le(raw, 0)
        o += 4                                 # flags
        o += 4                                 # colour/id
        parent = struct.unpack_from("<i", raw, o)[0]
        o += 4
        mk = raw.find(b"eolo", o)
        transform: List[float] = []
        if mk != -1 and mk + 4 + 48 <= len(raw):
            transform = list(struct.unpack_from("<12f", raw, mk + 4))
        return SceneBone(name=name, parent=parent, transform=transform)
    except struct.error:
        return None


def _read_scene_animation(anim: FormChunk):
    """Decode one scene ``FORM ANIM`` into an :class:`Animation`. Scene animations are the v7
    layout (root world translation + per-bone rotation, normalized time) with **little-endian**
    name length-prefixes — distinct from the creature decoder in ``anim.py``, which stays untouched."""
    info = next((c for c in anim.children if c.tag == "INFO"), None)
    if info is None:
        return None
    d = info.raw
    nl = struct.unpack_from("<I", d, 0)[0]; o = 4 + nl
    if o + 16 > len(d):
        return None
    flags, reserved, frames = struct.unpack_from("<III", d, o)
    fps = struct.unpack_from("<f", d, o + 12)[0]
    name = d[4:4 + nl].decode("latin1", "ignore").split("\x00", 1)[0]
    out = Animation(name=name, frame_count=frames, fps=fps, flags=flags, reserved=reserved,
                    vers=(7, 0, 0, 0))
    for c in anim.children:
        if c.tag != "BANM":
            continue
        bd = c.raw
        bnl = struct.unpack_from("<I", bd, 0)[0]; bo = 4 + bnl
        bname = bd[4:4 + bnl].decode("latin1", "ignore").split("\x00", 1)[0]
        if bo + 12 > len(bd):
            return None
        ntrans, mid, unk1 = struct.unpack_from("<III", bd, bo); bo += 12
        body = len(bd) - bo
        trans_bytes = ntrans * 16
        if trans_bytes > body or (body - trans_bytes) % 20 != 0:
            return None
        trans = [struct.unpack_from("<4f", bd, bo + i * 16) for i in range(ntrans)]
        bo += trans_bytes
        rot = [struct.unpack_from("<5f", bd, bo + i * 20) for i in range((body - trans_bytes) // 20)]
        out.tracks.append(BoneTrack(bone_name=bname, rot_keys=rot, trans_keys=trans,
                                    header_mid=mid, header_unk1=unk1))
    return out


def read_scene(path: str) -> Scene:
    """Parse a scene ``.sgm`` (characters/gatherers/buildings) into a :class:`Scene`."""
    sgm = Sgm.load(path)
    if not is_scene(sgm):
        raise ValueError("not a scene .sgm (looks like a creature BOBJ); use read_sgm()")
    scene = Scene()

    for pm in _walk(sgm, "PMSH"):
        nm = next((c for c in pm.children if c.tag == "NAME"), None)
        data = next((c for c in pm.children if c.tag == "DATA"), None)
        if data is None:
            continue
        name = nm.raw.split(b"\x00", 1)[0].decode("latin1", "ignore") if nm else ""
        scene.meshes.append(decode_pmsh(data.raw, name))

    scene.bones = [b for b in (_decode_bone(c.raw) for c in _walk(sgm, "BONE")) if b]

    # bind-pose transforms also live in the named XFRM nodes; prefer those (clean world matrices)
    xfrm = {}
    for xf in _walk(sgm, "XFRM"):
        nm = next((c for c in xf.children if c.tag == "NAME"), None)
        mt = next((c for c in xf.children if c.tag == "MTRX"), None)
        if nm is not None and mt is not None and len(mt.raw) >= 48:
            key = nm.raw.split(b"\x00", 1)[0].decode("latin1", "ignore").lower()
            xfrm[key] = list(struct.unpack_from("<12f", mt.raw, 0))
    for b in scene.bones:
        m = xfrm.get(b.name.lower())
        if m:
            b.transform = m

    for t in _walk(sgm, "TXRF"):
        s, _ = _lp_le(t.raw, 0)
        scene.textures.append(s)
    for sh in _walk(sgm, "SHDR"):
        nm = next((c for c in sh.children if c.tag == "NAME"), None)
        if nm is not None:
            scene.materials.append(nm.raw.split(b"\x00", 1)[0].decode("latin1", "ignore"))
    for a in _walk(sgm, "ANIM"):
        an = _read_scene_animation(a)
        if an is not None:
            scene.animations.append(an)
    return scene
