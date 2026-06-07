"""
Low-level binary primitives for the SGM/IFF container.

This is the **only** module in the package that knows about byte order, and it
exists precisely because the format mixes endianness:

    * chunk sizes and string length-prefixes are BIG-endian
    * every numeric payload value (counts, ids, flags, indices, floats) is LITTLE-endian

Centralising that here means no decoder ever calls ``struct`` directly, which is
what keeps the recurring "I read it big-endian by mistake" class of bug out of the
higher layers.
"""
from __future__ import annotations

import struct


class BinaryReader:
    """A forward cursor over a ``bytes`` buffer with typed, endian-aware reads."""

    def __init__(self, data: bytes, pos: int = 0, end: int | None = None) -> None:
        self._data = data
        self._pos = pos
        self._end = len(data) if end is None else end

    # -- cursor management -------------------------------------------------
    def tell(self) -> int:
        return self._pos

    def seek(self, pos: int) -> None:
        self._pos = pos

    def remaining(self) -> int:
        return self._end - self._pos

    def eof(self) -> bool:
        return self._pos >= self._end

    def read(self, n: int) -> bytes:
        if self._pos + n > self._end:
            raise EOFError(f"read of {n} bytes at offset {self._pos} exceeds end {self._end}")
        chunk = self._data[self._pos : self._pos + n]
        self._pos += n
        return chunk

    # -- scalars -----------------------------------------------------------
    def u32_be(self) -> int:
        """Big-endian uint32 — used for chunk sizes and string lengths."""
        return struct.unpack(">I", self.read(4))[0]

    def u32_le(self) -> int:
        """Little-endian uint32 — used for counts, ids, flags, indices."""
        return struct.unpack("<I", self.read(4))[0]

    def i32_le(self) -> int:
        return struct.unpack("<i", self.read(4))[0]

    def f32_le(self) -> float:
        return struct.unpack("<f", self.read(4))[0]

    def fourcc(self) -> str:
        """Four ASCII bytes identifying a chunk (e.g. ``"VERT"``)."""
        return self.read(4).decode("latin1")

    def string(self) -> str:
        """An SGM string: a BIG-endian uint32 length followed by ASCII bytes."""
        length = self.u32_be()
        return self.read(length).decode("latin1")


class BinaryWriter:
    """Accumulates bytes with the same endian conventions as :class:`BinaryReader`."""

    def __init__(self) -> None:
        self._parts: list[bytes] = []

    def raw(self, data: bytes) -> None:
        self._parts.append(data)

    def u32_be(self, value: int) -> None:
        self._parts.append(struct.pack(">I", value))

    def u32_le(self, value: int) -> None:
        self._parts.append(struct.pack("<I", value))

    def i32_le(self, value: int) -> None:
        self._parts.append(struct.pack("<i", value))

    def f32_le(self, value: float) -> None:
        self._parts.append(struct.pack("<f", value))

    def fourcc(self, tag: str) -> None:
        self._parts.append(tag.encode("latin1"))

    def string(self, value: str) -> None:
        encoded = value.encode("latin1")
        self.u32_be(len(encoded))
        self._parts.append(encoded)

    def getvalue(self) -> bytes:
        return b"".join(self._parts)
