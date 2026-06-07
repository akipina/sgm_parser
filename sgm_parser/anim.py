"""Animation semantics for the creature (BOBJ) format.

``sgm_parser``'s chunk layer keeps a ``FORM ANIM`` and its ``BANM`` tracks byte-faithful
but opaque (a word array). This module decodes/encodes the *meaning* of those tracks —
the per-bone Translation/Rotation/Scale channels the Blender add-on converts to and from
Actions — so the whole creature, animations included, has a single editable model.

Two on-disk layouts exist, selected by the ``FORM ANIM``'s ``VERS``:

* **v1** (``VERS 0,0,0,1``): ``INFO`` is name + 4 words (no AABB); each ``BANM`` holds
  three channels (Translation 3f, Rotation 4f, Scale 3f) of absolute-local transforms.
* **v7** (``VERS 0,0,0,7`` / ``0,0,1,3``): ``INFO`` may carry two AABBs; each ``BANM`` is
  a ``(trans_count, mid, unk1)`` header + trans keys (4f) + rotation keys (5f).

``read_anim`` turns a parsed ``AnimChunk`` into an :class:`Animation` (or ``None`` if the
layout doesn't line up, so the caller can keep the form verbatim); ``write_anim`` builds a
parsed ``AnimChunk`` back. The byte layout here mirrors the stock files exactly.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Optional, List, Tuple

from .chunks.base import FormChunk, FourCCChunk
from .chunks.common import VersChunk
from .chunks.creature import InfoChunk, BanmChunk


# --------------------------------------------------------------------------- #
# Channel / track / animation value types.
# --------------------------------------------------------------------------- #
@dataclass
class V1Channel:
    """A single TRS channel inside a v1 BANM. ``keys`` is a list of (time, value);
    the FIRST key carries time=None (implicit 0). ``value`` is a tuple of floats:
    3 for translation/scale, 4 for rotation (qx,qy,qz,qw)."""

    flag: int = 0
    keys: List[Tuple[object, Tuple[float, ...]]] = field(default_factory=list)


@dataclass
class BoneTrack:
    """One bone's animation channel inside a BANM chunk (v1 or v7 layout)."""

    bone_name: str
    # v7 layout
    rot_keys: List[Tuple[float, float, float, float, float]] = field(default_factory=list)
    trans_keys: List[Tuple[float, float, float, float]] = field(default_factory=list)
    header_mid: int = 0
    header_unk1: int = 0
    # v1 layout
    v1_loc: Optional[V1Channel] = None
    v1_rot: Optional[V1Channel] = None
    v1_scale: Optional[V1Channel] = None


@dataclass
class Animation:
    name: str
    frame_count: int = 1
    fps: float = 30.0
    flags: int = 1
    reserved: int = 0
    bbox1: Tuple[float, ...] = (0, 0, 0, 0, 0, 0)
    bbox2: Tuple[float, ...] = (0, 0, 0, 0, 0, 0)
    bbox_mid: int = 2
    has_bbox: bool = True
    tracks: List[BoneTrack] = field(default_factory=list)
    vers: Tuple[int, int, int, int] = (0, 0, 1, 3)
    name_pad: int = 0
    trailing: List[FourCCChunk] = field(default_factory=list)   # CANM etc., re-emitted as-is


# --------------------------------------------------------------------------- #
# Reading: parsed AnimChunk -> Animation (or None to keep verbatim).
# --------------------------------------------------------------------------- #
def _be(b: bytes, o: int) -> int:
    return struct.unpack_from(">I", b, o)[0]


def _le(b: bytes, o: int) -> int:
    return struct.unpack_from("<I", b, o)[0]


def _f(b: bytes, o: int) -> float:
    return struct.unpack_from("<f", b, o)[0]


def _read_v1_channel(d: bytes, o: int, vsz: int):
    if o + 8 > len(d):
        return None, o
    cnt = _le(d, o); flag = _le(d, o + 4); o += 8
    if cnt < 1 or o + vsz * 4 > len(d):
        return None, o
    keys = [(None, struct.unpack_from("<%df" % vsz, d, o))]
    o += vsz * 4
    for _ in range(cnt - 1):
        if o + 4 + vsz * 4 > len(d):
            return None, o
        t = _f(d, o); o += 4
        keys.append((t, struct.unpack_from("<%df" % vsz, d, o)))
        o += vsz * 4
    return V1Channel(flag=flag, keys=keys), o


def _read_v1_banm(d: bytes) -> Optional[BoneTrack]:
    nl = _be(d, 0); o = 4 + nl
    name = d[4:4 + nl].decode("latin1", "ignore").rstrip("\x00")
    loc, o = _read_v1_channel(d, o, 3)
    if loc is None:
        return None
    rot, o = _read_v1_channel(d, o, 4)
    if rot is None:
        return None
    scl, o = _read_v1_channel(d, o, 3)
    if scl is None or o != len(d):
        return None
    return BoneTrack(bone_name=name, v1_loc=loc, v1_rot=rot, v1_scale=scl)


def _children(anim: FormChunk):
    vers = next((c for c in anim.children if c.tag == "VERS"), None)
    info = next((c for c in anim.children if c.tag == "INFO"), None)
    return vers, info


def _read_anim_v1(anim: FormChunk) -> Optional[Animation]:
    vers, info = _children(anim)
    if info is None:
        return None
    d = info.raw
    nl = _be(d, 0); o = 4 + nl
    if o + 16 != len(d):
        return None
    flags = _le(d, o); reserved = _le(d, o + 4); frame_count = _le(d, o + 8); fps = _f(d, o + 12)
    name = d[4:4 + nl].decode("latin1", "ignore").rstrip("\x00")
    name_pad = nl - len(name.encode("latin1"))
    out = Animation(name=name, frame_count=frame_count, fps=fps, flags=flags,
                    reserved=reserved, has_bbox=False, name_pad=name_pad,
                    vers=tuple(vers.raw[:4]) if vers and len(vers.raw) >= 4 else (0, 0, 0, 1))
    for c in anim.children:
        if c.tag in ("VERS", "INFO"):
            continue
        if c.tag != "BANM":
            out.trailing.append(c)
            continue
        tr = _read_v1_banm(c.raw)
        if tr is None:
            return None
        out.tracks.append(tr)
    return out


def read_anim(anim: FormChunk) -> Optional[Animation]:
    """Parse one ``FORM ANIM`` (an :class:`AnimChunk`) into an :class:`Animation`, or
    return ``None`` if the layout doesn't line up so the caller keeps it verbatim."""
    vers, info = _children(anim)
    if vers is not None and len(vers.raw) >= 4 and tuple(vers.raw[:4]) == (0, 0, 0, 1):
        return _read_anim_v1(anim)
    if info is None:
        return None
    d = info.raw
    nl = _be(d, 0); o = 4 + nl
    if o + 16 > len(d):
        return None
    flags = _le(d, o); reserved = _le(d, o + 4); frame_count = _le(d, o + 8); fps = _f(d, o + 12); o += 16
    name = d[4:4 + nl].decode("latin1", "ignore").rstrip("\x00")
    has_bbox = False; bbox1 = (0,) * 6; bbox2 = (0,) * 6; bbox_mid = 2
    if o == len(d):
        has_bbox = False
    elif o + 52 == len(d):
        has_bbox = True
        bbox1 = struct.unpack_from("<6f", d, o); o += 24
        bbox_mid = _le(d, o); o += 4
        bbox2 = struct.unpack_from("<6f", d, o); o += 24
    else:
        return None
    out = Animation(name=name, frame_count=frame_count, fps=fps, flags=flags,
                    reserved=reserved, bbox1=bbox1, bbox2=bbox2, bbox_mid=bbox_mid,
                    has_bbox=has_bbox,
                    vers=tuple(vers.raw[:4]) if vers and len(vers.raw) >= 4 else (0, 0, 1, 3))
    for c in anim.children:
        if c.tag in ("VERS", "INFO"):
            continue
        if c.tag != "BANM":
            out.trailing.append(c)
            continue
        bd = c.raw; bnl = _be(bd, 0); bo = 4 + bnl
        bname = bd[4:4 + bnl].decode("latin1", "ignore").rstrip("\x00")
        if bo + 12 > len(bd):
            return None
        w0 = _le(bd, bo); mid = _le(bd, bo + 4); unk1 = _le(bd, bo + 8); bo += 12
        body = len(bd) - bo
        trans_bytes = w0 * 16
        if trans_bytes > body or (body - trans_bytes) % 20 != 0:
            return None
        trans = [struct.unpack_from("<4f", bd, bo + i * 16) for i in range(w0)]
        bo += trans_bytes
        rk = (body - trans_bytes) // 20
        rot = [struct.unpack_from("<5f", bd, bo + i * 20) for i in range(rk)]
        out.tracks.append(BoneTrack(bone_name=bname, rot_keys=rot, trans_keys=trans,
                                    header_mid=mid, header_unk1=unk1))
    return out


# --------------------------------------------------------------------------- #
# Writing: Animation -> parsed AnimChunk.
# --------------------------------------------------------------------------- #
def _w_v1_channel(ch: V1Channel, vsz: int) -> bytes:
    b = bytearray(struct.pack("<II", len(ch.keys), ch.flag))
    b += struct.pack("<%df" % vsz, *ch.keys[0][1])
    for t, v in ch.keys[1:]:
        b += struct.pack("<f", float(t))
        b += struct.pack("<%df" % vsz, *v)
    return bytes(b)


def _info_payload_v1(a: Animation) -> bytes:
    nm = a.name.encode("latin1", "ignore")
    info = bytearray(struct.pack(">I", len(nm) + a.name_pad) + nm + b"\x00" * a.name_pad)
    info += struct.pack("<III", a.flags, a.reserved, a.frame_count)
    info += struct.pack("<f", a.fps)
    return bytes(info)


def _info_payload_v7(a: Animation) -> bytes:
    nm = a.name.encode("latin1", "ignore")
    info = bytearray(struct.pack(">I", len(nm)) + nm)
    info += struct.pack("<III", a.flags, a.reserved, a.frame_count)
    info += struct.pack("<f", a.fps)
    if a.has_bbox:
        info += struct.pack("<6f", *a.bbox1)
        info += struct.pack("<I", a.bbox_mid)
        info += struct.pack("<6f", *a.bbox2)
    return bytes(info)


def write_anim(a: Animation) -> FormChunk:
    """Build a parsed ``FORM ANIM`` (:class:`AnimChunk`-style) from an :class:`Animation`.
    Children are constructed with exact payload bytes so serialization is byte-faithful."""
    v1 = tuple(a.vers[:4]) == (0, 0, 0, 1)
    children: List[FourCCChunk] = [
        VersChunk("VERS", bytes(a.vers)),
        InfoChunk("INFO", _info_payload_v1(a) if v1 else _info_payload_v7(a)),
    ]
    for t in a.tracks:
        bd = bytearray(struct.pack(">I", len(t.bone_name.encode("latin1", "ignore")))
                       + t.bone_name.encode("latin1", "ignore"))
        if v1:
            bd += _w_v1_channel(t.v1_loc, 3)
            bd += _w_v1_channel(t.v1_rot, 4)
            bd += _w_v1_channel(t.v1_scale, 3)
        else:
            bd += struct.pack("<III", len(t.trans_keys), t.header_mid, t.header_unk1)
            for k in t.trans_keys:
                bd += struct.pack("<4f", *k)
            for k in t.rot_keys:
                bd += struct.pack("<5f", *k)
        children.append(BanmChunk("BANM", bytes(bd)))
    children.extend(a.trailing)
    form = FormChunk("ANIM", children, b"")
    form.mark_dirty()
    return form
