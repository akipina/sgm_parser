"""
``Sgm`` — the logical object you actually work with.

It wraps the parsed top-level chunk sequence and provides:

* format detection (``BOBJ`` creature vs ``NOBS`` scene);
* whole-tree search (:meth:`find` / :meth:`find_all`) and a printable :meth:`dump`
  for probing;
* byte-faithful :meth:`to_bytes` / :meth:`save`;
* typed creature accessors (``vertices``, ``materials``, ``textures``, ``bones``,
  ``limbs``, ``connections``) for the combine work. On a scene file these simply
  come back empty — the chunk tree is still fully walkable and round-trippable.
"""
from __future__ import annotations

from pathlib import Path

from .chunks import FourCCChunk
from .chunks.base import FormChunk
from .parser import ChunkParser


class Sgm:
    def __init__(self, root_chunks: list[FourCCChunk]) -> None:
        self.root_chunks = root_chunks

    # -- construction ------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes) -> "Sgm":
        return cls(ChunkParser().parse(data))

    @classmethod
    def load(cls, path: str | Path) -> "Sgm":
        return cls.from_bytes(Path(path).read_bytes())

    # -- format ------------------------------------------------------------
    @property
    def format(self) -> str:
        forms = [c.form_type for c in self.root_chunks if isinstance(c, FormChunk)]
        if forms == ["BOBJ"]:
            return "BOBJ"
        if "NOBS" in forms:
            return "NOBS"
        return "unknown"

    # -- tree access -------------------------------------------------------
    def all_chunks(self) -> list[FourCCChunk]:
        """Every chunk in the file, depth-first (top-level + all descendants)."""
        flat: list[FourCCChunk] = []
        for c in self.root_chunks:
            flat.append(c)
            if isinstance(c, FormChunk):
                flat.extend(c.iter_all())
        return flat

    def find_all(self, tag: str) -> list[FourCCChunk]:
        """All chunks matching a leaf tag or a form-type, anywhere in the tree."""
        return [
            c for c in self.all_chunks()
            if c.tag == tag or getattr(c, "form_type", None) == tag
        ]

    def find(self, tag: str) -> FourCCChunk | None:
        matches = self.find_all(tag)
        return matches[0] if matches else None

    def dump(self) -> str:
        """A printable, indented view of the whole chunk tree (for probing)."""
        lines: list[str] = []

        def walk(chunk: FourCCChunk, depth: int) -> None:
            lines.append("  " * depth + chunk.summary())
            if isinstance(chunk, FormChunk):
                for child in chunk.children:
                    walk(child, depth + 1)

        for chunk in self.root_chunks:
            walk(chunk, 0)
        return "\n".join(lines)

    # -- serialization -----------------------------------------------------
    def to_bytes(self) -> bytes:
        return b"".join(c.serialize() for c in self.root_chunks)

    def save(self, path: str | Path) -> None:
        Path(path).write_bytes(self.to_bytes())

    # -- byte accounting ---------------------------------------------------
    def byte_account(self) -> dict:
        """Classify every byte: framing, semantically *interpreted*, or
        *preserved-but-unknown*. ``total`` must equal the file size, which is the
        proof that no byte is dropped; ``unknown`` is exactly what we don't decode yet.
        """
        framing = interpreted = unknown = 0
        per_tag: dict[str, dict[str, int]] = {}

        def visit(chunk: FourCCChunk) -> None:
            nonlocal framing, interpreted, unknown
            framing += 8  # tag + size header
            if isinstance(chunk, FormChunk):
                framing += 4  # the form-type word
                rec = per_tag.setdefault("FORM " + chunk.form_type,
                                         {"count": 0, "payload": 0, "interpreted": 0})
                rec["count"] += 1
                for child in chunk.children:
                    visit(child)
            else:
                payload = len(chunk.raw)
                il = chunk.interpreted_len()
                interpreted += il
                unknown += payload - il
                rec = per_tag.setdefault(chunk.tag,
                                         {"count": 0, "payload": 0, "interpreted": 0})
                rec["count"] += 1
                rec["payload"] += payload
                rec["interpreted"] += il

        for chunk in self.root_chunks:
            visit(chunk)
        return {
            "total": framing + interpreted + unknown,
            "framing": framing,
            "interpreted": interpreted,
            "unknown": unknown,
            "per_tag": per_tag,
        }

    # -- creature (BOBJ) accessors ----------------------------------------
    @property
    def vertices(self) -> list[tuple[float, float, float]]:
        chunk = self.find("VERT")
        return list(getattr(chunk, "points", [])) if chunk else []

    @property
    def materials(self) -> list[FourCCChunk]:
        return self.find_all("MTRL")

    @property
    def textures(self) -> list[FourCCChunk]:
        return self.find_all("TXMP")

    @property
    def bones(self) -> list[FourCCChunk]:
        return self.find_all("BONE")

    @property
    def limbs(self) -> list:
        chunk = self.find("LIMB")
        return list(getattr(chunk, "limbs", [])) if chunk else []

    @property
    def connections(self) -> list:
        chunk = self.find("LCON")
        return list(getattr(chunk, "connections", [])) if chunk else []

    def __repr__(self) -> str:
        return f"<Sgm format={self.format} top_level={len(self.root_chunks)}>"
