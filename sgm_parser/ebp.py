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
from .chunks.base import FormChunk


def _lpstr(b: bytes, o: int):
    """Read a u32-length-prefixed latin1 string at offset o; return (string, next_offset)."""
    n = struct.unpack_from("<I", b, o)[0]
    o += 4
    return b[o:o + n].decode("latin1"), o + n


def _put_lpstr(s: str) -> bytes:
    e = s.encode("latin1")
    return struct.pack("<I", len(e)) + e


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
