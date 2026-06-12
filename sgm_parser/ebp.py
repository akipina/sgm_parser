"""Read and generate Impossible Creatures ``.ebp`` entity blueprints.

An ``.ebp`` is the entity blueprint the game (and the Object Editor) pairs with a creature's
``.sgm``: it carries the combiner **icon**, the **motion tree** (which behaviour state plays
which animation), animation **events** (effect/combine markers bound to bones), and build
info. It's an EA-IFF-85 FORM/chunk tree, so :class:`~sgm_parser.model.Sgm` already parses and
round-trips it byte-for-byte; this module decodes the creature-specific chunks on top.

Creature ``.ebp`` shape::

    FORM EBPF { VERS, EBPD(icon+attrs+name), FORM XSGM(empty -> sibling .sgm),
                FORM SIGM { FORM MTRE { MINF, MNOD x animations },
                            FORM EVCT { VERS, EVNT x events } },
                FORM EBPC { BLDI } }

Why this matters: the motion tree's MNODs name animations, and the EVNTs name bones. **Those
names must exist in the paired ``.sgm``** -- a blueprint that references an animation or bone
the model lacks crashes the game on combine. :func:`retarget` rewrites a template blueprint so
every animation/bone reference resolves against a target ``.sgm`` (this is the core of what
the Object Editor does when it builds an ``.ebp`` for a model).
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass, field
from typing import List, Optional

from .model import Sgm
from .chunks.base import FormChunk, RawChunk


def _lpstr(b: bytes, o: int):
    """Read a u32-length-prefixed latin1 string at offset o; return (string, next_offset).

    IC ``.ebp`` come in two flavours: tool-generated/mod ones (e.g. the Object Editor's output)
    store the length **little-endian**, while the stock ``EngineExtract`` creatures store it
    **big-endian**. For the short ASCII names in MNOD/EVNT the two readings are never both plausible
    (a real small length one way is a huge number the other way), so auto-detect by picking whichever
    gives an in-range, in-bounds length -- preferring little-endian when both somehow fit."""
    n_le = struct.unpack_from("<I", b, o)[0]
    n_be = struct.unpack_from(">I", b, o)[0]
    for n in (n_le, n_be):
        if 0 <= n <= 0xFFFF and o + 4 + n <= len(b):
            return b[o + 4:o + 4 + n].decode("latin1"), o + 4 + n
    n = max(0, min(n_le, len(b) - o - 4))            # neither plausible: clamp, don't crash
    return b[o + 4:o + 4 + n].decode("latin1", "ignore"), o + 4 + n


def _put_lpstr(s: str, big_endian: bool = False) -> bytes:
    e = s.encode("latin1")
    return struct.pack(">I" if big_endian else "<I", len(e)) + e


def _ebp_big_endian(sgm) -> bool:
    """True if this blueprint stores chunk-internal string lengths big-endian (stock creatures) vs
    little-endian (tool-generated). Detected from the first MNOD's leading length word."""
    for c in sgm.all_chunks() if hasattr(sgm, "all_chunks") else []:
        if c.tag == "MNOD" and len(c.raw) >= 4:
            return struct.unpack_from(">I", c.raw, 0)[0] <= 0xFFFF
    return False


def _alpha(s: str) -> str:
    """Normalise an animation name for fuzzy matching: lowercase, letters only."""
    return re.sub(r"[^a-z]", "", s.lower())


def _is_name(s: str) -> bool:
    """True if `s` looks like a real animation name (printable, no control bytes)."""
    return bool(s) and all(32 <= ord(c) < 127 for c in s)


def _ebpd_name_span(raw: bytes):
    """Locate the EBPD's trailing display-name field: ``<u32 charCount><UTF-16LE name><pad>``.
    Returns ``(count_offset, string_end_offset, name)``. Stock EBPDs pad with 4 trailing nulls,
    so that's tried first."""
    for pad in (4, 0, 1, 2, 3, 5, 6, 7, 8):
        str_end = len(raw) - pad
        for n in range(1, 64):
            cs = str_end - 2 * n
            co = cs - 4
            if co < 0:
                break
            if struct.unpack_from("<I", raw, co)[0] == n:
                try:
                    txt = raw[cs:str_end].decode("utf-16-le")
                except UnicodeDecodeError:
                    continue
                if txt and all(32 <= ord(c) < 127 for c in txt):
                    return co, str_end, txt
    raise ValueError("could not locate EBPD name field")


@dataclass
class MotionNode:
    """One MTRE motion node: behaviour `state` -> `anim` plus opaque parameter bytes."""
    state: str
    anim: str
    params: bytes            # everything after the anim string (blend/timing floats)
    _mid: bytes = b"\x00\x00\x00\x00"   # the u32 between state and anim

    @classmethod
    def parse(cls, raw: bytes) -> "MotionNode":
        state, o = _lpstr(raw, 0)
        mid = raw[o:o + 4]
        o += 4
        anim, o = _lpstr(raw, o)
        return cls(state=state, anim=anim, params=raw[o:], _mid=mid)

    def to_bytes(self) -> bytes:
        return _put_lpstr(self.state) + self._mid + _put_lpstr(self.anim) + self.params


@dataclass
class Event:
    """An EVCT event (kept opaque); `tokens` are its readable length-prefixed strings."""
    raw: bytes

    @property
    def tokens(self) -> List[str]:
        out, i = [], 0
        b = self.raw
        while i + 4 <= len(b):
            n = struct.unpack_from("<I", b, i)[0]
            if 0 < n <= 64 and i + 4 + n <= len(b):
                s = b[i + 4:i + 4 + n]
                if all(32 <= c < 127 for c in s):
                    out.append(s.decode("latin1"))
                    i += 4 + n
                    continue
            i += 1
        return out


class Ebp:
    """A parsed entity blueprint. :meth:`load`/:meth:`from_bytes` to read, :meth:`to_bytes`
    to write (byte-faithful unless modified), and :func:`retarget` to fix references."""

    def __init__(self, sgm: Sgm):
        self._sgm = sgm        # the underlying FORM tree (Sgm just means "FORM container")

    # ---- read ----
    @classmethod
    def load(cls, path: str) -> "Ebp":
        return cls(Sgm.load(path))

    @classmethod
    def from_bytes(cls, data: bytes) -> "Ebp":
        return cls(Sgm.from_bytes(data))

    def to_bytes(self) -> bytes:
        return self._sgm.to_bytes()

    def save(self, path: str) -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())

    def _forms(self, form_type: str) -> List[FormChunk]:
        return [c for c in self._sgm.all_chunks()
                if isinstance(c, FormChunk) and c.form_type == form_type]

    def _chunks(self, tag: str):
        return [c for c in self._sgm.all_chunks() if c.tag == tag]

    @property
    def motion_nodes(self) -> List[MotionNode]:
        return [MotionNode.parse(c.raw) for c in self._chunks("MNOD")]

    @property
    def events(self) -> List[Event]:
        return [Event(c.raw) for c in self._chunks("EVNT")]

    def animation_refs(self) -> List[str]:
        """Animation names the motion tree references (real names only; excludes blanks,
        'proc:' nodes, and nodes whose anim field isn't a clean name)."""
        return [mn.anim for mn in self.motion_nodes
                if _is_name(mn.anim) and not mn.anim.startswith("proc:")]

    # ---- display name (the EBPD trailing name field) ----
    def _ebpd(self):
        for c in self._sgm.all_chunks():
            if c.tag == "EBPD":
                return c
        return None

    @property
    def creature_name(self) -> Optional[str]:
        """The blueprint's display name. Stock creatures store a locale key like ``"$58033"``
        (resolved to "Tuna" via the mod's text DLL); a literal (non-``$``) name shows verbatim."""
        c = self._ebpd()
        if c is None:
            return None
        return _ebpd_name_span(c.raw)[2]

    @creature_name.setter
    def creature_name(self, value: str) -> None:
        c = self._ebpd()
        if c is None:
            raise ValueError("blueprint has no EBPD chunk")
        co, str_end, _ = _ebpd_name_span(c.raw)
        c.raw = (c.raw[:co] + struct.pack("<I", len(value))
                 + value.encode("utf-16-le") + c.raw[str_end:])
        c.mark_dirty()

    # ---- combiner icon (the embedded paletted thumbnail in the EBPD) ----
    def icon_span(self):
        """`(pixel_start, width, height)` of the EBPD's embedded combiner icon, or None. The icon
        is a paletted bitmap: a ``<u32 width><u32 height>`` header near the EBPD start, then
        width*height 1-byte indices (top-down, row-major), then a 256-entry B,G,R,X palette."""
        c = self._ebpd()
        if c is None:
            return None
        raw = c.raw
        for off in range(8, min(64, len(raw) - 8)):
            w, h = struct.unpack_from("<II", raw, off)
            if w == h and w in (16, 32, 48, 64, 96, 128) and off + 8 + w * h + 1024 <= len(raw):
                return off + 8, w, h
        return None

    def set_icon(self, indices: bytes, palette: bytes) -> bool:
        """Replace the EBPD's embedded combiner icon. ``indices`` = width*height 1-byte palette
        indices (top-down, row-major); ``palette`` = 256*4 bytes in B,G,R,X order. The icon keeps
        its existing dimensions (so ``indices`` must be exactly width*height). Returns True if the
        icon was found and replaced; False otherwise (the original is left untouched)."""
        span = self.icon_span()
        if span is None:
            return False
        ps, w, h = span
        if len(indices) != w * h or len(palette) != 256 * 4:
            return False
        c = self._ebpd()
        c.raw = c.raw[:ps] + bytes(indices) + bytes(palette) + c.raw[ps + w * h + 1024:]
        c.mark_dirty()
        return True


# ===========================================================================
# From-scratch blueprint generation (no donor needed)
#
# The Object Editor's "Generate missing motions" builds the motion tree
# algorithmically: for each known behaviour state it checks whether the model
# has the matching animation clip and, if so, emits a node with hardcoded
# parameters. This was recovered from the decompiled ObjectEditor.exe
# (FUN_00556f54 builder, FUN_0055632d MNOD deserializer) and verified byte-exact
# against the stock corpus. The code below replicates it so a creature can be
# blueprinted with no donor .ebp at all.
# ===========================================================================

# Container scaffolding constants (extracted from the stock corpus; pure format
# bytes, not creature assets).
_EBPF_VERS = b"\x00\x00\x00\x6d"          # FORM EBPF version word
_MTRE_VERS = b"\x00\x00\x00\x04"          # motion-tree format version
_EVCT_VERS = b"\x00\x00\x00\x02"          # event-table format version
_BLDI = bytes.fromhex("0000000100000001000000000000000000000000")
_EBPD_HEADER = bytes.fromhex("000003e80400000002000000010101000000")
# the byte after the icon palette: a constant 0x01 flag + a small per-creature
# float (combiner icon scale). A neutral default is fine for a generated icon.
_EBPD_POST = b"\x01" + struct.pack("<f", 0.05)


@dataclass
class _NodeBuild:
    """A motion node under construction. Fields default to the OE node
    constructor's defaults; each generated node overrides only what it needs.
    Serializes to the exact MNOD layout (see FUN_0055632d)."""
    state: str
    anim: str = ""
    children: List[str] = field(default_factory=list)   # child state names
    loop: bool = True                  # flags bit0 (+0x38)
    invert: bool = False               # flags bit1 (+0x4c) — melee AttackTime
    bit2: bool = False                 # flags bit2 (+0x8c)
    x34: float = 1.0                   # playback rate (1.0 = normal; SwimIdle uses 0.25)
    blend: tuple = (0.0, 0.0, 0.0, 0.0)   # +0x54/0x58/0x5c/0x60
    weight: tuple = (1.0, 0.0)            # +0x78/0x7c
    fade: tuple = (0.5, 0.0)              # +0x80 fade-in / +0x84 fade-out
    speed_ref: float = 0.0               # +0x48
    speed_var: str = ""                  # speed-scale variable name
    cond: tuple = (-1.0, -1.0)           # +0x64 min / +0x68 max
    cond_var: str = ""                   # conditional variable name
    x88: int = 0
    x50: int = 0

    def to_mnod(self, index_of, big_endian: bool = True) -> bytes:
        flags = (1 if self.loop else 0) | (2 if self.invert else 0) | (4 if self.bit2 else 0)
        b = _put_lpstr(self.state, big_endian)
        b += struct.pack("<I", len(self.children))
        for ch in self.children:
            b += struct.pack("<I", index_of[ch])
        b += _put_lpstr(self.anim, big_endian)
        b += struct.pack("<I", flags)
        b += struct.pack("<I", self.x88)
        b += struct.pack("<f", self.x34)
        b += struct.pack("<ffff", *self.blend)
        b += struct.pack("<ff", *self.weight)
        b += struct.pack("<ff", *self.fade)
        b += struct.pack("<f", self.speed_ref)
        b += _put_lpstr(self.speed_var, big_endian)
        b += struct.pack("<ff", *self.cond)
        b += _put_lpstr(self.cond_var, big_endian)
        b += struct.pack("<I", self.x50)
        return b


def build_motion_nodes(anim_names, durations=None) -> List[_NodeBuild]:
    """Build the motion-node list for a creature from its animation clip names,
    replicating the Object Editor's generator. ``durations`` maps a clip name to
    its length in seconds (used for melee ``AttackTime`` timing); missing entries
    fall back to a neutral default. Returns nodes in creation order with children
    referenced by state name."""
    have = {a.lower() for a in anim_names}
    actual = {a.lower(): a for a in anim_names}
    dur = {k.lower(): v for k, v in (durations or {}).items()}

    def has(name):
        return name.lower() in have

    def clip(*names):
        """Actual-case clip name for the first variant the model has, else None."""
        for n in names:
            if n.lower() in actual:
                return actual[n.lower()]
        return None

    nodes: List[_NodeBuild] = []
    present = set()

    def add(node):
        nodes.append(node)
        present.add(node.state)

    V = "Velocity"
    # --- idle overlay + pose (and the Idle group is added last) ---
    if has("OAttack1"):
        add(_NodeBuild("Idle_Overlay", anim="OAttack1",
                       blend=(0.0, 20.0, 15.0, 30.0), weight=(0.85, 1.0),
                       fade=(0.2, 0.2), cond=(-1.0, 0.0), cond_var=V))
    if has("Idle1"):
        add(_NodeBuild("Idle_Pose", anim="Idle1", fade=(0.3, 0.0)))
    # --- procedural overlays (engine-provided; standard on the IC skeleton) ---
    add(_NodeBuild("SpineBend", anim="proc:SpineBend", weight=(1.0, 1.0), fade=(0.3, 0.0)))
    add(_NodeBuild("TargetLook", anim="proc:TargetLook", weight=(1.0, 1.0),
                   fade=(0.3, 0.0), cond=(-1.0, 2.0), cond_var=V))
    # --- ground locomotion ---
    if has("Walk"):
        add(_NodeBuild("Walk", anim="Walk", speed_ref=1.5, speed_var=V,
                       fade=(0.3, 0.0), cond=(0.0, 3.0 if has("Run") else -1.0), cond_var=V))
    if has("Run"):
        add(_NodeBuild("Run", anim="Run", speed_ref=10.0, speed_var=V,
                       fade=(0.2, 0.0), cond=(3.0, -1.0), cond_var=V))
    # --- swimming ---
    if has("Swim"):
        add(_NodeBuild("SwimIdle", anim="Swim", x34=0.25, fade=(0.2, 0.0),
                       cond=(-1.0, 0.0), cond_var=V))
        add(_NodeBuild("SwimMove", anim="Swim", speed_ref=5.0, speed_var=V,
                       fade=(0.2, 0.0), cond=(0.0, -1.0), cond_var=V))
    # --- flight ---
    if has("Soar"):
        add(_NodeBuild("Soar", anim="Soar", fade=(0.2, 0.0)))
    if has("Fly"):
        add(_NodeBuild("Fly_Slow", anim="Fly", blend=(3.0, 5.0, 5.0, 8.0),
                       fade=(0.2, 0.2), cond=(-1.0, 5.0), cond_var=V))
        add(_NodeBuild("Fly_Fast", anim="Fly", speed_ref=10.0, speed_var=V,
                       fade=(0.2, 0.0), cond=(5.0, -1.0), cond_var=V))
    # --- attacks (AttackTime = clip_length * 0.5, playback inverted; child Idle_Pose) ---
    pose = ["Idle_Pose"] if "Idle_Pose" in present else []
    for melee in ("Melee1", "Melee2", "MeleeLow1"):
        if has(melee):
            add(_NodeBuild(melee, anim=melee, loop=False, invert=True,
                           fade=(0.2, 0.2), speed_var="AttackTime",
                           speed_ref=dur.get(melee.lower(), 1.0) * 0.5, children=list(pose)))
    # --- ability attacks/moves (templated from stock; gated on the clip existing) ---
    leap = clip("leapattack", "LeapAttack", "Leapattack")
    if leap:                                              # Leap ability — a simple leaf
        add(_NodeBuild("Leap", anim=leap, fade=(0.2, 0.0)))
    rng = clip("Range1")
    if rng:                                               # ranged attack — melee-family + Idle_Pose
        add(_NodeBuild("Range1", anim=rng, loop=False, invert=True, fade=(0.2, 0.2),
                       speed_var="AttackTime", speed_ref=dur.get("range1", 1.0) * 0.5,
                       children=list(pose)))
    # --- stumble / death ---
    if has("StumbleGetup"):
        add(_NodeBuild("Stumble", anim="StumbleGetup", loop=False, fade=(0.1, 0.2)))
    if has("StumbleDie"):
        add(_NodeBuild("Die", anim="StumbleDie", loop=False, fade=(0.4, 0.0)))

    # --- group nodes (collect children that exist) ---
    if has("Idle1"):
        kids = [s for s in ("Idle_Overlay", "Idle_Pose") if s in present]
        add(_NodeBuild("Idle", children=kids, cond=(-1.0, 0.0), cond_var=V))
    if has("Swim"):
        add(_NodeBuild("Swim", children=[s for s in ("SwimIdle", "SwimMove") if s in present]))
    if has("Fly"):
        add(_NodeBuild("Fly", children=[s for s in ("Soar", "Fly_Slow", "Fly_Fast") if s in present]))
    if has("Walk") or has("Run"):
        kids = [s for s in ("TargetLook", "SpineBend", "Run", "Walk", "Idle") if s in present]
        add(_NodeBuild("Move", children=kids))
    charge = clip("charge", "Charge")
    if charge:                                            # Charge ability — a Move-like locomotion group
        kids = [s for s in ("SpineBend", "Run", "Walk", "Idle") if s in present]
        add(_NodeBuild("Charge", anim=charge, weight=(1.0, 1.0), fade=(0.2, 0.0), children=kids))

    return nodes


def build_motion_tree(anim_names, durations=None, big_endian: bool = True) -> FormChunk:
    """Build a ``FORM MTRE`` (VERS, MINF default-index, MNOD*) from a creature's
    animation clip names — the Object Editor's motion tree, from scratch."""
    nodes = build_motion_nodes(anim_names, durations)
    index_of = {n.state: i for i, n in enumerate(nodes)}
    # default motion: Move if the creature walks/runs, else Idle, else first node
    default = index_of.get("Move", index_of.get("Idle", 0))
    children = [RawChunk("VERS", _MTRE_VERS),
                RawChunk("MINF", struct.pack("<I", default))]
    children += [RawChunk("MNOD", n.to_mnod(index_of, big_endian)) for n in nodes]
    return _form("MTRE", children)


def _form(form_type: str, children) -> FormChunk:
    f = FormChunk(form_type, list(children), b"")
    f.mark_dirty()                       # constructed -> encode from children
    return f


def _blank_icon_ebpd(name: str, icon_indices=None, icon_palette=None) -> RawChunk:
    """Build an EBPD chunk: attrs header + 64x64 paletted combiner icon + name.
    A blank icon is written if none is supplied (the add-on splices the real
    thumbnail in afterward via :meth:`Ebp.set_icon`)."""
    idx = bytes(icon_indices) if icon_indices is not None else bytes(64 * 64)
    pal = bytes(icon_palette) if icon_palette is not None else bytes(256 * 4)
    if len(idx) != 64 * 64 or len(pal) != 256 * 4:
        raise ValueError("icon must be 64x64 indices + 256*4 palette")
    name_field = struct.pack("<I", len(name)) + name.encode("utf-16-le") + b"\x00\x00\x00\x00"
    raw = (_EBPD_HEADER + struct.pack("<II", 64, 64) + idx + pal + _EBPD_POST + name_field)
    return RawChunk("EBPD", raw)


def build_ebp(sgm_data, name: str, *, durations=None,
              icon_indices=None, icon_palette=None) -> Ebp:
    """Generate a complete entity blueprint **from scratch** for a creature.

    ``sgm_data`` is the creature's ``.sgm`` (path or bytes); it is embedded as the
    ``BOBJ`` object exactly as stock blueprints do. The motion tree is generated
    from the model's animation names (no donor ``.ebp`` required), events are left
    empty, and ``name`` becomes the display name. Pass ``icon_indices``/
    ``icon_palette`` (64x64 paletted) to embed the combiner thumbnail now, or splice
    it later with :meth:`Ebp.set_icon`.
    """
    if isinstance(sgm_data, (str, bytes, bytearray)) and not isinstance(sgm_data, (bytes, bytearray)):
        sgm = Sgm.load(sgm_data)
    else:
        sgm = Sgm.from_bytes(bytes(sgm_data))

    anim_names, dur = _sgm_animation_names(sgm)
    durations = durations or dur

    bobj = _form("BOBJ", sgm.root_chunks)
    mtre = build_motion_tree(anim_names, durations, big_endian=True)
    sigm = _form("SIGM", [mtre, _form("EVCT", [RawChunk("VERS", _EVCT_VERS)])])
    ebpc = _form("EBPC", [RawChunk("BLDI", _BLDI)])
    ebpd = _blank_icon_ebpd(name, icon_indices, icon_palette)
    ebpf = _form("EBPF", [RawChunk("VERS", _EBPF_VERS), ebpd, bobj, sigm, ebpc])
    return Ebp(Sgm([ebpf]))


def _sgm_animation_names(sgm: Sgm):
    """Return ``(names, durations)`` for the animations embedded in an .sgm FORM
    tree. Each ``FORM ANIM`` carries an INFO chunk naming the clip and a frame
    count; duration is frames / 30fps (the IC playback rate)."""
    names: List[str] = []
    durations = {}
    for form in sgm.all_chunks():
        if not (isinstance(form, FormChunk) and form.form_type == "ANIM"):
            continue
        info = next((c for c in form.children if c.tag == "INFO"), None)
        if info is None:
            continue
        nm, o = _lpstr(info.raw, 0)
        if not _is_name(nm):
            continue
        names.append(nm)
        # INFO after the name: u32, u32, u32 frame_count, f32 fps, ...
        if o + 16 <= len(info.raw):
            frames = struct.unpack_from("<I", info.raw, o + 8)[0]
            fps = struct.unpack_from("<f", info.raw, o + 12)[0]
            if 0 < frames < 100000 and 1.0 <= fps <= 240.0:
                durations[nm] = frames / fps
    return names, durations


def retarget(template: Ebp, target_anims, target_bones, ref_bones=None) -> Ebp:
    """Rewrite `template` in place so every reference resolves against a target model.

    * MNOD animation refs are matched to `target_anims` (exact, case-insensitive, then
      letters-only); an unmatched ref is blanked so the node becomes a no-op rather than a
      dangling reference.
    * EVNT events that name a bone present in `ref_bones` but absent from `target_bones`
      are dropped (those are the references that would crash combine).

    `target_anims` / `target_bones` / `ref_bones` are iterables of names (use the target
    .sgm's animation and bone names, and the template .sgm's bone names for `ref_bones`).
    """
    anims = list(target_anims)
    exact = {a.lower(): a for a in anims}
    alpha = {}
    for a in anims:
        alpha.setdefault(_alpha(a), a)
    tbones = {b.lower() for b in target_bones}
    rbones = {b.lower() for b in (ref_bones or [])}
    missing_bones = rbones - tbones        # bones the template uses that the target lacks

    sgm = template._sgm

    # 1) remap MNOD animation references. Only touch nodes we parse cleanly and losslessly,
    # and whose anim is a real name -- some node variants don't fit the simple layout, and
    # those must be left byte-identical rather than risk corrupting them.
    for c in [c for c in sgm.all_chunks() if c.tag == "MNOD"]:
        mn = MotionNode.parse(c.raw)
        if mn.to_bytes() != c.raw:                 # our parse isn't lossless here -> skip
            continue
        if not _is_name(mn.anim) or mn.anim.startswith("proc:"):
            continue
        new = exact.get(mn.anim.lower()) or alpha.get(_alpha(mn.anim))
        if new == mn.anim:
            continue
        mn.anim = new or ""                        # blank if no equivalent animation exists
        c.raw = mn.to_bytes()
        c.mark_dirty()

    # 2) drop events referencing bones the target doesn't have
    for evct in template._forms("EVCT"):
        kept = []
        for ch in evct.children:
            if ch.tag == "EVNT":
                toks = {t.lower() for t in Event(ch.raw).tokens}
                if toks & missing_bones:
                    continue               # this event names a bone the target lacks -> drop
            kept.append(ch)
        evct.children = kept
        evct.mark_dirty()

    return template


def build_creature_ebp(target_sgm_path: str, template_ebp_path: str,
                       template_sgm_path: str) -> Ebp:
    """Generate an entity blueprint for ``target_sgm_path`` from a reference creature.

    Takes a working creature's blueprint (`template_ebp_path`) + its model
    (`template_sgm_path`) and retargets every animation/bone reference to the target model,
    so the result pairs correctly with ``target_sgm_path`` (no references the model lacks).
    The icon and behaviour come from the template. This is the practical equivalent of what
    the Object Editor does when it writes an ``.ebp`` for a model.
    """
    from .creature import read_sgm
    target = read_sgm(target_sgm_path)
    ref = read_sgm(template_sgm_path)
    ebp = Ebp.load(template_ebp_path)
    retarget(ebp,
             target_anims={a.name for a in target.animations},
             target_bones={b.name for b in target.bones},
             ref_bones={b.name for b in ref.bones})
    return ebp
