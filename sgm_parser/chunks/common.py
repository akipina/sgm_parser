"""
Chunks that appear in both the creature (BOBJ) and scene (NOBS) formats.

These are small and unambiguous, so they are fully decoded and apply globally
(the registry is shared, so they decode wherever they appear).
"""
from __future__ import annotations

from .base import FourCCChunk


class VersChunk(FourCCChunk):
    """A version stamp: four bytes, exposed as a ``(a, b, c, d)`` tuple."""

    TAG = "VERS"

    def decode(self) -> None:
        self.version: tuple[int, ...] = tuple(self.raw[:4])

    def encode(self) -> bytes:
        # Round-trip uses raw when unedited; this only runs if fields are changed.
        return bytes(self.version) if len(self.version) == len(self.raw) else self.raw

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        return "VERS = " + ".".join(str(x) for x in getattr(self, "version", ()))


class NameChunk(FourCCChunk):
    """A name string.

    Unlike names *embedded* inside other chunks (which are length-prefixed so they
    can be delimited from following fields), a standalone NAME chunk's payload is
    the string itself -- the chunk header already gives its length. It is usually
    null-terminated/padded, so we read up to the first NUL. Treated read-only for
    now (it re-emits its original bytes on save).
    """

    TAG = "NAME"

    def decode(self) -> None:
        self.name = self.raw.split(b"\x00", 1)[0].decode("latin1", "replace")

    def interpreted_len(self) -> int:
        return len(self.raw)

    def summary(self) -> str:
        return f'NAME = "{getattr(self, "name", "")}"'
