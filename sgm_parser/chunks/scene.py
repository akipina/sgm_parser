"""
Chunks of the native scene-graph (``NOBS``) format used by structures, characters,
projectiles and henchmen.

None are decoded yet — for the current (creature-combine) goal we only need to
*open, enumerate and round-trip* these. Each is registered as a named stub so the
tree reads meaningfully (``<SigmChunk ...>`` rather than ``<RawChunk ...>``) and so
a decoder can be dropped in later without touching the parser or anything else.

The form-vs-leaf split below is a best guess from observed trees; ``chunk_class_for``
falls back safely if a guess is wrong, so mis-classification cannot break parsing.
"""
from __future__ import annotations

from .base import FormChunk, RawChunk

# Container (FORM) chunk types observed in the scene format.
_SCENE_FORMS = [
    "NOBS",  # 12-byte root marker form
    "SIGM",  # the scene container that follows the marker
    "SHDR",  # shader (NAME/VERS/DATA/CHAN…)
    "GGRP",  # geometry group (a transform-node hierarchy)
    "XFRM",  # transform node (NAME/MTRX/BVOL…)
    "PMSH",  # packed mesh
    "GEOM",  # geometry container
    "BVOL",  # bounding volume
    "MRKR",  # named marker / attach point
    "MRPH",  # morph target group
    "CMRA",  # camera
    "LGHT",  # light
    "NODE",  # generic scene node
]

# Leaf chunk types observed in the scene format (opaque payloads for now).
_SCENE_LEAVES = [
    "MTRX",  # 4x3 transform matrix
    "CHAN",  # animation / shader channel
    "DATA",  # opaque packed payload (mesh, bounds, anim, …)
    "BIND",  # skin binding
    "TEAM",  # team-colour data
    "TXRF",  # texture reference
    "TXTR",  # texture (may carry embedded image)
    "STXT",  # secondary/skin texture
    "IMAG",  # embedded image
    "ATTR",  # attributes
    "VTMP",  # vertex morph / template
    "XREF",  # external model reference
]


# Generate the stub classes programmatically (one logical line each) so the file
# stays a readable manifest rather than dozens of near-identical class blocks.
def _make(tag: str, base: type) -> type:
    return type(f"{tag.title()}Chunk", (base,), {"TAG": tag, "__doc__": f"Scene stub for '{tag}'."})


_GENERATED = {
    **{tag: _make(tag, FormChunk) for tag in _SCENE_FORMS},
    **{tag: _make(tag, RawChunk) for tag in _SCENE_LEAVES},
}
# Bind them as module-level names too (e.g. ``scene.SigmChunk``) for direct use.
globals().update({f"{tag.title()}Chunk": cls for tag, cls in _GENERATED.items()})
