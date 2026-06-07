"""
Chunk package.

Importing this package imports every chunk module, which triggers the
``__init_subclass__`` auto-registration so the parser can dispatch by tag. If you
add a new chunk module, import it here.
"""
from __future__ import annotations

from . import common, creature, scene  # noqa: F401  (import for registration side-effects)
from .base import (  # noqa: F401
    REGISTRY,
    FormChunk,
    FourCCChunk,
    RawChunk,
    chunk_class_for,
)

__all__ = ["REGISTRY", "FourCCChunk", "FormChunk", "RawChunk", "chunk_class_for",
           "common", "creature", "scene"]
