"""
The chunk parser: turns raw bytes into a tree of :class:`FourCCChunk`.

Two things worth noting about the container, both learned the hard way:

* The top level is a **sequence** of chunks, not a single root that spans the file.
  A creature (``BOBJ``) file happens to be one top-level FORM, but a scene (``NOBS``)
  file is a 12-byte ``FORM NOBS`` marker followed by a sibling ``FORM SIGM``. So we
  read top-level chunks until EOF rather than trusting any one chunk's size.
* Whether a chunk is a container is decided by the literal ``FORM`` tag on disk,
  not by our class registry — the registry only chooses *which* class to build.
"""
from __future__ import annotations

import struct

from .binary import BinaryReader
from .chunks import FourCCChunk, chunk_class_for
from .chunks.base import FormChunk


class ChunkParser:
    """Parses SGM/IFF bytes into a list of top-level chunks."""

    def parse(self, data: bytes) -> list[FourCCChunk]:
        """Parse a whole file into its top-level chunk sequence."""
        return self._parse_sequence(data, 0, len(data))

    # -- internals ---------------------------------------------------------
    def _parse_sequence(self, data: bytes, start: int, end: int) -> list[FourCCChunk]:
        chunks: list[FourCCChunk] = []
        pos = start
        while pos + 8 <= end:
            tag = data[pos : pos + 4].decode("latin1")
            size = struct.unpack_from(">I", data, pos + 4)[0]
            body_start = pos + 8
            body_end = body_start + size
            if body_end > end:
                # Truncated/short file (e.g. a cut-off extract like fx/bonfire.sgm): clamp to the
                # bytes we actually have and read what's there, as the engine's IFF reader does.
                # Valid files never overrun, so this only ever affects malformed ones; the clamped
                # chunk becomes the last parsed (pos jumps to end), so nothing after is misread.
                body_end = end
            chunks.append(self._build(data, tag, body_start, body_end))
            pos = body_end
        return chunks

    def _build(self, data: bytes, tag: str, body_start: int, body_end: int) -> FourCCChunk:
        payload = data[body_start:body_end]
        if tag == "FORM":
            form_type = data[body_start : body_start + 4].decode("latin1")
            children = self._parse_sequence(data, body_start + 4, body_end)
            cls = chunk_class_for(form_type, is_form=True)
            chunk: FourCCChunk = cls(form_type, children, payload)  # type: ignore[call-arg]
        else:
            cls = chunk_class_for(tag, is_form=False)
            chunk = cls(tag, payload)
        chunk.decode()
        return chunk
