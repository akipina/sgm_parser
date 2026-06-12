"""
Chunks of the creature / combinable (``BOBJ``) format.

The geometry, material, texture, limb and connection chunks are decoded into typed
fields here (these are the combine-relevant ones, verified byte-exact against stock
models). The container forms are named so the tree is self-describing, and a few
remaining creature chunks are registered as stubs to be decoded later.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass, field

from ..binary import BinaryReader, BinaryWriter
from ..constants import FLAGS1_DEFAULT, SlotType
from .base import FormChunk, FourCCChunk, RawChunk


# --------------------------------------------------------------------------- #
# Small value types carried by the multi-record chunks.
# --------------------------------------------------------------------------- #
@dataclass
class Limb:
    """One body-part slot: which patches and bones make it up."""

    name: str
    limb_id: int
    patch_mask: bytes          # 1 bit per patch in the model
    bones: list[str] = field(default_factory=list)

    def patch_indices(self) -> list[int]:
        """Decode the membership bitmask to a list of patch indices."""
        out: list[int] = []
        for byte_i, byte in enumerate(self.patch_mask):
            for bit in range(8):
                if byte & (1 << bit):
                    out.append(byte_i * 8 + bit)
        return out


@dataclass
class Connection:
    """One attachment seam: a ring of control points and the bone it hosts on."""

    name: str
    slot: int                  # raw flags2 value; see SlotType
    edges: list[tuple[int, int]]
    host_bone: str
    flags1: int = FLAGS1_DEFAULT
    pad_words: int = 2         # zero u32 words between the ring and the host-bone name

    @property
    def slot_label(self) -> str:
        return SlotType.label(self.slot)

    def ring_indices(self) -> list[int]:
        """Unique control-point indices in the ring loop."""
        seen: list[int] = []
        for a, b in self.edges:
            for i in (a, b):
                if i not in seen:
                    seen.append(i)
        return seen


# --------------------------------------------------------------------------- #
# Geometry.
# --------------------------------------------------------------------------- #
class VertChunk(FourCCChunk):
    """Bézier control points: ``count`` then ``count`` × (x, y, z) float32."""

    TAG = "VERT"

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        n = r.u32_le()
        self.points: list[tuple[float, float, float]] = [
            (r.f32_le(), r.f32_le(), r.f32_le()) for _ in range(n)
        ]

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.u32_le(len(self.points))
        for x, y, z in self.points:
            w.f32_le(x); w.f32_le(y); w.f32_le(z)
        return w.getvalue()

    def interpreted_len(self) -> int:
        return len(self.raw)  # count + xyz floats consume the whole payload

    def summary(self) -> str:
        return f"VERT ({len(getattr(self, 'points', []))} control points)"


# --------------------------------------------------------------------------- #
# Materials & textures.
# --------------------------------------------------------------------------- #
class MtrlChunk(FourCCChunk):
    """A material: a name followed by self-delimiting tagged sub-fields.

    Fully decoded. Colour/flag fields (AMBC, DIFC, SMTH, 2SID, TILE) carry a fixed
    4-byte value; texture-stage fields (TXTR diffuse, TXRF reflection, TXSP specular)
    carry a length-prefixed filename. Sub-field order is preserved so re-encoding is
    byte-exact. Editing ``name`` or any texture stage marks the chunk dirty.
    """

    TAG = "MTRL"
    STRING_FIELDS = ("TXTR", "TXRF", "TXSP")

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        self._name = r.string()
        # ordered list of [tag, value]; value is str for texture stages, else bytes(4)
        self.fields: list[list] = []
        while r.remaining() >= 4:
            tag = r.fourcc()
            value = r.string() if tag in self.STRING_FIELDS else r.read(4)
            self.fields.append([tag, value])

    # -- field access ------------------------------------------------------
    def field(self, tag: str):
        """Value of a sub-field (str for texture stages, bytes(4) otherwise), or None."""
        for t, v in self.fields:
            if t == tag:
                return v
        return None

    def _set_field(self, tag: str, value) -> None:
        for f in self.fields:
            if f[0] == tag:
                f[1] = value
                self.mark_dirty()
                return
        raise ValueError(f"material '{self._name}' has no {tag} field")

    @property
    def name(self) -> str:
        return self._name

    @name.setter
    def name(self, value: str) -> None:
        self._name = value
        self.mark_dirty()

    @property
    def texture(self):                       # diffuse (TXTR)
        return self.field("TXTR")

    @texture.setter
    def texture(self, value: str) -> None:
        self._set_field("TXTR", value)

    @property
    def reflection(self):                    # reflection (TXRF)
        return self.field("TXRF")

    @reflection.setter
    def reflection(self, value: str) -> None:
        self._set_field("TXRF", value)

    @property
    def specular(self):                      # specular (TXSP)
        return self.field("TXSP")

    @specular.setter
    def specular(self, value: str) -> None:
        self._set_field("TXSP", value)

    def texture_stages(self) -> dict:
        """All texture references on this material, keyed by stage tag."""
        return {t: v for t, v in self.fields if t in self.STRING_FIELDS}

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.string(self._name)
        for tag, value in self.fields:
            w.fourcc(tag)
            if tag in self.STRING_FIELDS:
                w.string(value)
            else:
                w.raw(value)
        return w.getvalue()

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        extra = [t for t in ("TXRF", "TXSP") if self.field(t) is not None]
        tail = f"  (+{'+'.join(extra)})" if extra else ""
        return f'MTRL "{getattr(self, "_name", "")}" -> {self.field("TXTR")!r}{tail}'


class TxmpChunk(FourCCChunk):
    """A texture map: a filename followed by a mipmapped 8-bit palettized image.

    Image layout (little-endian): ``palette_count, width, height, mip_count`` (4
    dwords), then a BGRA palette of ``palette_count`` entries (4 bytes each), then
    the full mip chain of 8-bit palette indices — level *i* is
    ``max(1, width>>i) x max(1, height>>i)``, for ``mip_count`` levels. Fully
    decoded; the filename is editable and the image round-trips byte-exact.
    """

    TAG = "TXMP"

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        self._filename = r.string()
        self._image = self.raw[r.tell() :]
        self._parsed = False
        self.palette_count = self.width = self.height = self.mip_count = None
        self.palette = self.pixels = None
        try:
            ir = BinaryReader(self._image)
            self.palette_count = ir.u32_le()
            self.width = ir.u32_le()
            self.height = ir.u32_le()
            self.mip_count = ir.u32_le()
            self.palette = ir.read(self.palette_count * 4)
            npix = sum(
                max(1, self.width >> i) * max(1, self.height >> i)
                for i in range(self.mip_count)
            )
            self.pixels = ir.read(npix)
            self._parsed = (
                ir.remaining() == 0
                and len(self.palette) == self.palette_count * 4
                and len(self.pixels) == npix
            )
        except Exception:
            self._parsed = False

    @property
    def filename(self) -> str:
        return self._filename

    @filename.setter
    def filename(self, value: str) -> None:
        self._filename = value
        self.mark_dirty()

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.string(self._filename)
        w.raw(self._image)
        return w.getvalue()

    @classmethod
    def build(cls, filename, palette_count, format_code, width, height,
              palette, mips) -> "TxmpChunk":
        """Construct a texture chunk from decoded fields. ``palette_count``/``format_code``
        are the two 16-bit halves of the leading dword; ``mips`` is a list of per-level
        8-bit-index byte strings."""
        img = BinaryWriter()
        img.raw(struct.pack("<HHIII", palette_count, format_code,
                            width, height, len(mips)))
        img.raw(palette)
        for level in mips:
            img.raw(level)
        c = cls("TXMP", b"")
        c._filename = filename
        c._image = img.getvalue()
        c._parsed = True
        c.palette_count = palette_count
        c.width, c.height, c.mip_count = width, height, len(mips)
        c.palette = palette
        c.pixels = b"".join(mips)
        c.mark_dirty()
        return c

    def interpreted_len(self) -> int:
        if self._parsed:
            return len(self.raw)
        return 4 + len(self._filename.encode("latin1"))

    def summary(self) -> str:
        if self._parsed:
            return (f'TXMP "{self._filename}" {self.width}x{self.height} '
                    f'{self.palette_count}-colour, {self.mip_count} mips')
        return f'TXMP "{getattr(self, "_filename", "")}" ({len(getattr(self, "_image", b""))}B image)'


# --------------------------------------------------------------------------- #
# Skeleton (bone record only partially understood).
# --------------------------------------------------------------------------- #
class BoneChunk(FourCCChunk):
    """A single bone.

    In the creature (BOBJ) format a bone is a length-prefixed name followed by a
    fixed 100-byte (25-dword) numeric block. Decoded read-only (re-emits its exact
    bytes), exposing the structurally identified fields:

      word[0]      flag/reserved
      word[1]      packed id
      word[2]  i32 parent bone index (-1 = root)
      word[3..5]   offset + two counts (weight/vertex table refs)
      word[6..17]  4x3 transform matrix (3x3 rotation rows + translation row)
      word[18..23] joint-limit floats (min/max per axis) [purpose inferred]
      word[24]     trailing flag/pad

    The scene (NOBS) bone layout differs; there we fall back to name-only and the
    numeric tail stays opaque (scene files aren't the creature-completeness target).
    """

    TAG = "BONE"

    def decode(self) -> None:
        self.name = "<undecoded>"
        self.parent: int | None = None
        self.matrix: list[float] | None = None
        self.limits: list[float] | None = None
        self.tail: list[float] | None = None
        self._head: list[int] | None = None
        self._full = False
        try:
            r = BinaryReader(self.raw)
            self.name = r.string()
            num = self.raw[r.tell() :]
            # creature layout: 6 dwords + 12 transform floats + trailing floats (>=72 bytes,
            # 4-byte aligned). Stock bones are 100 bytes (6 + 12 + 7 trailing).
            if len(num) >= 72 and len(num) % 4 == 0:
                self._head = list(struct.unpack_from("<6I", num, 0))
                self.matrix = list(struct.unpack_from("<12f", num, 24))
                ntail = (len(num) - 72) // 4
                self.tail = list(struct.unpack_from(f"<{ntail}f", num, 72)) if ntail else []
                self.flags = self._head[0]
                self.bone_id = self._head[1]
                self.parent = None if self._head[2] == 0xFFFFFFFF else self._head[2]
                self.counts = (self._head[3], self._head[4], self._head[5])
                self.limits = self.tail[:6]
                self._full = True
        except Exception:
            self.name = "<undecoded>"

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.string(self.name)
        w.raw(struct.pack("<6I", *self._head))
        for f in self.matrix:
            w.f32_le(f)
        for f in self.tail:
            w.f32_le(f)
        return w.getvalue()

    @classmethod
    def build(cls, name, parent, start_vertex, vertex_count, transform, tail,
              meta0=0, meta1=0, meta5=0) -> "BoneChunk":
        c = cls("BONE", b"")
        c.name = name
        c._head = [meta0 & 0xFFFFFFFF, meta1 & 0xFFFFFFFF,
                   0xFFFFFFFF if parent is None else (parent & 0xFFFFFFFF),
                   start_vertex & 0xFFFFFFFF, vertex_count & 0xFFFFFFFF, meta5 & 0xFFFFFFFF]
        c.matrix = [float(x) for x in transform]
        c.tail = [float(x) for x in tail]
        c.flags = c._head[0]
        c.bone_id = c._head[1]
        c.parent = None if c._head[2] == 0xFFFFFFFF else parent
        c.counts = (c._head[3], c._head[4], c._head[5])
        c.limits = c.tail[:6]
        c._full = True
        c.mark_dirty()
        return c

    def interpreted_len(self) -> int:
        # Whole record is structurally accounted for in the creature format.
        return len(self.raw) if self._full else (4 + len(self.name.encode("latin1"))
                                                 if self.name != "<undecoded>" else 0)

    def summary(self) -> str:
        if self._full:
            return f'BONE "{self.name}" parent={self.parent}'
        return f'BONE "{getattr(self, "name", "?")}" ({len(self.raw)}B, undecoded layout)'


# --------------------------------------------------------------------------- #
# Limb membership & connections (verified byte-exact).
# --------------------------------------------------------------------------- #
class LimbChunk(FourCCChunk):
    """The limb membership table: per slot, a patch bitmask and a bone list."""

    TAG = "LIMB"

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        count = r.u32_le()
        self.total_patches = r.u32_le()
        self.limbs: list[Limb] = []
        for _ in range(count):
            name = r.string()
            limb_id = r.u32_le()
            npatch = r.u32_le()
            mask = r.read(((npatch + 31) // 32) * 4)  # bitmask stored in 32-bit words
            nbones = r.u32_le()
            bones = [r.string() for _ in range(nbones)]
            self.limbs.append(Limb(name, limb_id, mask, bones))

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.u32_le(len(self.limbs))
        w.u32_le(self.total_patches)
        for limb in self.limbs:
            w.string(limb.name)
            w.u32_le(limb.limb_id)
            w.u32_le(self.total_patches)        # npatch == bitmask width
            w.raw(limb.patch_mask)
            w.u32_le(len(limb.bones))
            for bone in limb.bones:
                w.string(bone)
        return w.getvalue()

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        return f"LIMB ({len(getattr(self, 'limbs', []))} limbs)"


class LconChunk(FourCCChunk):
    """The connection table: per slot, a control-point ring and a host bone."""

    TAG = "LCON"

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        count = r.u32_le()
        self.connections: list[Connection] = []
        for _ in range(count):
            name = r.string()
            flags1 = r.u32_le()
            slot = r.u32_le()
            npairs = r.u32_le()
            flat = [r.u32_le() for _ in range(npairs * 2)]
            edges = [(flat[i], flat[i + 1]) for i in range(0, len(flat), 2)]
            pad = 0                              # count the (variable) zero pad words
            while r.remaining() >= 4 and self.raw[r.tell():r.tell() + 4] == b"\x00\x00\x00\x00":
                r.seek(r.tell() + 4); pad += 1
            host = r.string()
            self.connections.append(Connection(name, slot, edges, host, flags1, pad))

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.u32_le(len(self.connections))
        for c in self.connections:
            w.string(c.name)
            w.u32_le(c.flags1)
            w.u32_le(c.slot)
            w.u32_le(len(c.edges))
            for a, b in c.edges:
                w.u32_le(a); w.u32_le(b)
            for _ in range(c.pad_words):
                w.u32_le(0)
            w.string(c.host_bone)
        return w.getvalue()

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        return f"LCON ({len(getattr(self, 'connections', []))} connections)"


class MidxChunk(FourCCChunk):
    """The material-name table: a count followed by that many material names.

    These mirror the MTRL names in order (it's how patches reference materials by
    slot). Fully decoded; re-encoded byte-exact.
    """

    TAG = "MIDX"

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        n = r.u32_le()
        self.names: list[str] = [r.string() for _ in range(n)]

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.u32_le(len(self.names))
        for name in self.names:
            w.string(name)
        return w.getvalue()

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        return f"MIDX ({len(getattr(self, 'names', []))} material names)"


class InfoChunk(FourCCChunk):
    """A small header. Two shapes, distinguished by content:

    * inside SKEL: a single u32 (the bone count);
    * inside ANIM: an animation name (length-prefixed, null-padded) followed by a
      parameter block — ``flags, reserved, frame_count, fps(float)`` and, in some
      exporters, trailing motion-extent floats.

    Decoded read-only (re-emits its exact bytes); the whole payload is accounted for.
    """

    TAG = "INFO"

    def decode(self) -> None:
        self.name = None
        self.bone_count = None
        self.frame_count = None
        self.fps = None
        if len(self.raw) == 4:
            self.bone_count = struct.unpack("<I", self.raw)[0]
            self.kind = "count"
            return
        self.kind = "anim"
        try:
            r = BinaryReader(self.raw)
            self.name = r.string().split("\x00", 1)[0]
            tail = self.raw[r.tell() :]
            if len(tail) >= 16:
                f0, f1, frames = struct.unpack_from("<3I", tail, 0)
                self.frame_count = frames
                self.fps = struct.unpack_from("<f", tail, 12)[0]
        except Exception:
            self.kind = "raw"

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        if self.kind == "count":
            return f"INFO count={self.bone_count}"
        if self.name is not None:
            return f'INFO anim="{self.name}" frames={self.frame_count} fps={self.fps}'
        return f"INFO ({len(self.raw)}B)"


class PrimChunk(FourCCChunk):
    """The patch table: a count followed by one fixed 104-byte record per patch.

    Each record (26 dwords): material index, the smoothing-group bitmask, the 16
    control-point indices of the bicubic Bezier patch, then 8 UV floats (4 corner pairs).
    The second dword (parsed as ``ptype`` / surfaced as ``Patch.type``) is the modeled
    patch's **3ds Max ``Patch::smGroup``** — a 32-bit smoothing-group bitmask (each bit a
    group, bit0 = group #1, default 1). Two adjacent patches are smooth-shaded across their
    shared edge iff they share a bit, else the edge is hard/creased. It is NOT the Max
    ``Face::flags`` word (no material-ID packed in the upper bits; material has its own
    dword). See SGM_PATCH_FLAGS.md for the full reverse-engineering + sources.
    Decoded read-only (re-emits exact bytes); exposes ``patches`` as
    ``(material_index, [16 control-point indices])`` for combine analysis.
    """

    TAG = "PRIM"
    RECORD = 104

    def decode(self) -> None:
        r = BinaryReader(self.raw)
        self.count = r.u32_le()
        body = self.raw[4:]
        # full record: (material_index, type, control_points[16], uvs[8])
        self.records: list[tuple[int, int, list[int], list[float]]] = []
        # back-compat view used by combine analysis: (material_index, control_points[16])
        self.patches: list[tuple[int, list[int]]] = []
        self._full = bool(self.count) and len(body) == self.count * self.RECORD
        if self._full:
            for i in range(self.count):
                off = i * self.RECORD
                material = struct.unpack_from("<I", body, off)[0]
                ptype = struct.unpack_from("<I", body, off + 4)[0]
                cps = list(struct.unpack_from("<16I", body, off + 8))
                uvs = list(struct.unpack_from("<8f", body, off + 72))
                self.records.append((material, ptype, cps, uvs))
                self.patches.append((material, cps))

    def encode(self) -> bytes:
        w = BinaryWriter()
        w.u32_le(len(self.records))
        for material, ptype, cps, uvs in self.records:
            w.u32_le(material)
            w.u32_le(ptype)
            for c in cps:
                w.u32_le(c)
            for u in uvs:
                w.f32_le(u)
        return w.getvalue()

    @classmethod
    def build(cls, records) -> "PrimChunk":
        """records: iterable of (material_index, type, control_points[16], uvs[8])."""
        c = cls("PRIM", b"")
        c.records = [(int(m), int(t), [int(x) for x in cps], [float(u) for u in uvs])
                     for m, t, cps, uvs in records]
        c.patches = [(m, cps) for m, t, cps, uvs in c.records]
        c.count = len(c.records)
        c._full = True
        c.mark_dirty()
        return c

    def interpreted_len(self) -> int:
        return len(self.raw) if self._full else 4

    def summary(self) -> str:
        return f"PRIM ({getattr(self, 'count', 0)} patches x {self.RECORD}B)"


class BanmChunk(FourCCChunk):
    """A per-bone animation track: a bone name followed by keyframe channel data.

    The payload after the name is decoded as a 32-bit word array (it is 4-byte
    aligned), so every byte is accounted for and re-encoded exactly. The leading
    word is a channel/keyframe count; the remaining words are interleaved float
    keyframe data (rotation/translation channels). The per-channel field boundaries
    are identified structurally but not yet broken into named fields — exposed via
    ``words`` (raw u32) and ``floats``.
    """

    TAG = "BANM"

    def decode(self) -> None:
        self.bone = "<undecoded>"
        self.words: list[int] = []
        self.count = 0
        self._full = False
        try:
            r = BinaryReader(self.raw)
            self.bone = r.string()
            tail = self.raw[r.tell() :]
            if len(tail) % 4 == 0:
                self.words = list(struct.unpack(f"<{len(tail) // 4}I", tail))
                self.count = self.words[0] if self.words else 0
                self._full = True
        except Exception:
            self._full = False

    @property
    def floats(self) -> list[float]:
        return [struct.unpack("<f", struct.pack("<I", w))[0] for w in self.words]

    def interpreted_len(self) -> int:
        if self._full:
            return len(self.raw)
        return 4 + len(self.bone.encode("latin1")) if self.bone != "<undecoded>" else 0

    def summary(self) -> str:
        if self._full:
            return f'BANM "{self.bone}" ({len(self.words)} keyframe words)'
        return f"BANM ({len(self.raw)}B)"


# --------------------------------------------------------------------------- #
# Container forms (named so the tree reads cleanly).
# --------------------------------------------------------------------------- #
class BobjChunk(FormChunk):
    TAG = "BOBJ"


class BonmChunk(FormChunk):
    TAG = "BONM"


class MeshChunk(FormChunk):
    TAG = "MESH"


class SkelChunk(FormChunk):
    TAG = "SKEL"


class AnimChunk(FormChunk):
    TAG = "ANIM"


# --------------------------------------------------------------------------- #
# Known-but-undecoded creature leaf chunks (named stubs; round-trip verbatim).
# --------------------------------------------------------------------------- #
def _stub_leaf(tag: str) -> type[RawChunk]:
    """Build a named RawChunk subclass for a known-but-undecoded leaf tag."""
    return type(f"{tag.title()}Chunk", (RawChunk,), {"TAG": tag})


MidxChunk; InfoChunk; PrimChunk; BanmChunk  # noqa: B018  (decoded above; referenced for linters)
CanmChunk = _stub_leaf("CANM")   # compressed anim channel (scene format; not used by creatures)
