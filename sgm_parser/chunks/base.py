"""
The chunk class hierarchy and the tag -> class registry.

Design (see SGM_PARSER_ARCHITECTURE.md):

* Every chunk *is a* :class:`FourCCChunk`. The base owns what all chunks share —
  the four-char tag, the original payload bytes, and the two operations every
  chunk supports: ``decode`` (bytes -> typed fields) and ``encode`` (fields -> bytes).
* :class:`FormChunk` is the one structural special case: a container holding child
  chunks. Leaf chunk types subclass :class:`FourCCChunk` directly.
* :class:`RawChunk` is the fallback for any tag we have not written a class for:
  it keeps its bytes verbatim and round-trips them untouched.
* Each concrete subclass declares ``TAG`` and is auto-registered via
  ``__init_subclass__`` — so the parser dispatches by tag with no if/elif ladder,
  and adding support for a new chunk is simply *write one more subclass*.

Fidelity rule: ``serialize()`` returns the original bytes unless the chunk (or, for
a form, any descendant) has been marked ``dirty`` by an edit, in which case it
re-encodes. Untouched files therefore round-trip byte-for-byte by construction.
"""
from __future__ import annotations

import struct
from typing import ClassVar

# 4cc -> chunk class. For leaf chunks the key is the tag; for form chunks it is
# the FORM's form-type (e.g. "MESH"). Populated automatically on subclass creation.
REGISTRY: dict[str, type["FourCCChunk"]] = {}


class FourCCChunk:
    """Base class for every chunk in the file."""

    #: The 4cc this subclass handles. Empty on the base / generic helpers.
    TAG: ClassVar[str] = ""
    #: True for container (FORM) chunks; see :class:`FormChunk`.
    IS_FORM: ClassVar[bool] = False

    def __init__(self, tag: str, raw: bytes) -> None:
        self.tag = tag          #: literal 4cc on disk ("FORM" for containers)
        self.raw = raw          #: original payload bytes (serialization source of truth)
        self._dirty = False     #: set when typed fields have been edited

    def __init_subclass__(cls, **kwargs: object) -> None:
        super().__init_subclass__(**kwargs)
        if cls.TAG:
            REGISTRY[cls.TAG] = cls

    # -- edit tracking -----------------------------------------------------
    @property
    def dirty(self) -> bool:
        return self._dirty

    def mark_dirty(self) -> None:
        """Call after mutating a decoded field so ``serialize`` re-encodes it."""
        self._dirty = True

    # -- decode / encode (overridden by decoded subclasses) ----------------
    def decode(self) -> None:
        """Populate typed fields from ``self.raw``. Base/stub: nothing to do."""

    def encode(self) -> bytes:
        """Rebuild the payload from typed fields. Base/stub: bytes are unchanged."""
        return self.raw

    def interpreted_len(self) -> int:
        """How many payload bytes this chunk actually *understands* (turns into
        named fields). The remainder is preserved verbatim but not yet decoded.
        Base/stub returns 0 — the whole payload is opaque. Used for byte accounting."""
        return 0

    # -- serialization -----------------------------------------------------
    def serialize(self) -> bytes:
        """Framed bytes: tag + big-endian size + payload (raw if clean, else encoded)."""
        payload = self.encode() if self.dirty else self.raw
        return self.tag.encode("latin1") + struct.pack(">I", len(payload)) + payload

    # -- probing helpers ---------------------------------------------------
    def summary(self) -> str:
        """One-line description used by the tree dump."""
        return f"{self.tag} ({len(self.raw)}B)"

    def hexdump(self, limit: int = 256) -> str:
        """Annotated hex of the payload, for poking at undecoded chunks."""
        out = []
        data = self.raw[:limit]
        for off in range(0, len(data), 16):
            row = data[off : off + 16]
            hexs = " ".join(f"{b:02x}" for b in row)
            asci = "".join(chr(b) if 32 <= b < 127 else "." for b in row)
            out.append(f"{off:5d}: {hexs:<47} {asci}")
        if len(self.raw) > limit:
            out.append(f"... (+{len(self.raw) - limit} more bytes)")
        return "\n".join(out)

    def __repr__(self) -> str:
        return f"<{type(self).__name__} {self.summary()}>"


class RawChunk(FourCCChunk):
    """Undecoded leaf chunk. The fallback used for any unrecognised tag."""

    def __init__(self, tag: str, raw: bytes) -> None:
        super().__init__(tag, raw)


class FormChunk(FourCCChunk):
    """A container chunk (``FORM`` + form-type + nested children)."""

    IS_FORM = True

    def __init__(self, form_type: str, children: list["FourCCChunk"], raw: bytes) -> None:
        super().__init__("FORM", raw)
        self.form_type = form_type
        self.children = children

    # A form is dirty if it was edited directly or any descendant changed.
    @property
    def dirty(self) -> bool:
        return self._dirty or any(c.dirty for c in self.children)

    def encode(self) -> bytes:
        # Payload = form-type (4 bytes) + the serialized children, concatenated.
        body = self.form_type.encode("latin1")
        for child in self.children:
            body += child.serialize()
        return body

    # -- tree search -------------------------------------------------------
    def find(self, tag: str) -> "FourCCChunk | None":
        """First descendant matching a leaf tag or a form-type, depth-first."""
        for chunk in self.iter_all():
            if chunk.tag == tag or getattr(chunk, "form_type", None) == tag:
                return chunk
        return None

    def find_all(self, tag: str) -> list["FourCCChunk"]:
        return [
            c for c in self.iter_all()
            if c.tag == tag or getattr(c, "form_type", None) == tag
        ]

    def iter_all(self) -> "list[FourCCChunk]":
        """Flatten this form's whole subtree (children, grandchildren, ...)."""
        flat: list[FourCCChunk] = []
        for child in self.children:
            flat.append(child)
            if isinstance(child, FormChunk):
                flat.extend(child.iter_all())
        return flat

    def summary(self) -> str:
        return f"FORM {self.form_type} ({len(self.children)} children)"


def chunk_class_for(key: str, *, is_form: bool) -> type[FourCCChunk]:
    """Look up the registered class for a tag/form-type, falling back sensibly.

    The file's actual structure (``FORM`` tag or not) always wins: if a stub was
    registered with the wrong form-ness, we degrade to the generic container/leaf
    rather than mis-constructing it. This makes the stub classifications harmless
    even where they're guesses.
    """
    cls = REGISTRY.get(key)
    if cls is None:
        return FormChunk if is_form else RawChunk
    if is_form and not cls.IS_FORM:
        return FormChunk
    if not is_form and cls.IS_FORM:
        return RawChunk
    return cls
