"""Validate a creature against every known cause of an in-game crash — so the toolkit can catch
a bad export *before* the game does.

Two levels:

* ``check_model(model)`` — structural invariants on an in-memory :class:`SgmModel` (indices in
  range, well-formed mip chains, a **watertight** patch mesh — a dangling edge makes the Object
  Editor's patch loader crash on load — and a **patch count within the editor's 16-bit display
  budget**: too many patches overflow a 65 536-vertex display buffer and crash the loader).
* ``check_creature_assets(stock_dir, name)`` — the *full on-disk asset set* a combiner creature
  needs (``.sgm`` + ``.ebp`` + ``.lua`` + ``.lsc`` + ``<Name>_torso.bmp``) and the cross-check
  that the ``.ebp`` only references animations the ``.sgm`` has. Each item maps to one crash.

Every function returns a list of problem strings (empty == ok); problems prefixed ``WARN`` are
non-fatal. ``write_torso_bmp`` exports the combiner skin a creature must ship.
"""

from __future__ import annotations

import os
import struct
from collections import Counter
from typing import List, Optional

from .creature import SgmModel, Texture
from .ebp import Ebp

_TEX_TAGS = ("TXTR", "TXRF", "TXSP")

# The four sides of a patch's 4x4 control grid, as control-point indices along each side. A
# watertight closed mesh shares every side between exactly two patches; a side used by only one
# patch is a "dangling edge" — the Object Editor's patchmesh loader rejects those (it logs
# "Patch model has dangling edge" and then crashes).
_PATCH_EDGES = ((0, 1, 2, 3), (12, 13, 14, 15), (0, 4, 8, 12), (3, 7, 11, 15))

# --- patch-count ceiling -----------------------------------------------------------------------
# On load the Object Editor tessellates every Bezier patch into a fixed block of display vertices
# kept in a single 16-bit-indexed buffer. Each patch contributes its 16 control points to that
# buffer, so once the running total crosses 65 536 the buffer overflows and the patchmesh loader
# hits a compiled-in __debugbreak() assert (EXCEPTION_BREAKPOINT at ObjectEditor 0x4aa0cb, with
# EBX pinned just past 0x10000) — a hard crash on load, independent of the file's own control-point
# count or texture. The safe ceiling is therefore 65 535 // 16 patches.
#
# Empirical anchors (all on the same chicken mesh): 257 patches loads; 5 871 patches crashes here;
# every stock creature is <=516 patches. Reducing *control points* alone does NOT help — only the
# patch count matters, because the overflow is in the tessellated display mesh, not the file verts.
PATCH_DISPLAY_VERTS = 16                                  # display verts the editor emits per patch
MAX_DISPLAY_VERTS = 65_535                                # 16-bit index buffer ceiling
MAX_PATCHES = MAX_DISPLAY_VERTS // PATCH_DISPLAY_VERTS    # 4095
STOCK_MAX_PATCHES = 516                                   # densest stock creature (velvet_worm)


def check_model(m: SgmModel) -> List[str]:
    """Structural invariants on an SgmModel. Returns a list of problems (empty == ok)."""
    problems: List[str] = []
    nverts, npatches, nmats = len(m.vertices), len(m.patches), len(m.materials)

    if nverts == 0:
        problems.append("model has no vertices")
    if npatches == 0:
        problems.append("model has no patches")

    # patch-count vs the editor's 16-bit display-vertex buffer (overflow -> hard crash on load)
    if npatches > MAX_PATCHES:
        problems.append(
            f"{npatches} patches exceed the Object Editor's display-vertex budget "
            f"(max {MAX_PATCHES}: {MAX_DISPLAY_VERTS} verts / {PATCH_DISPLAY_VERTS} per patch). "
            f"The patchmesh loader overflows its 16-bit buffer and crashes on load — decimate the "
            f"mesh (stock creatures are <= {STOCK_MAX_PATCHES} patches).")
    elif npatches > STOCK_MAX_PATCHES:
        problems.append(
            f"WARN {npatches} patches is denser than any stock creature (<= {STOCK_MAX_PATCHES}); "
            f"under the {MAX_PATCHES}-patch hard limit but not verified to load — consider decimating.")

    for i, p in enumerate(m.patches):
        if len(p.control_points) != 16:
            problems.append(f"patch {i}: expected 16 control points, got {len(p.control_points)}")
        for cp in p.control_points:
            if not 0 <= cp < nverts:
                problems.append(f"patch {i}: control point {cp} out of range (0..{nverts - 1})")
                break
        if not 0 <= p.material_index < nmats:
            problems.append(f"patch {i}: material_index {p.material_index} out of range")
        if len(p.uvs) != 8:
            problems.append(f"patch {i}: expected 8 UV floats, got {len(p.uvs)}")

    nbones = len(m.bones)
    for i, b in enumerate(m.bones):
        if b.parent is not None and not 0 <= b.parent < nbones:
            problems.append(f"bone {i} ({b.name}): parent {b.parent} out of range")
        if b.start_vertex < 0 or b.start_vertex + b.vertex_count > nverts:
            problems.append(f"bone {i} ({b.name}): vertex range exceeds {nverts}")

    # watertightness: every patch side shared by exactly two patches (else editor crashes)
    if m.patches:
        edge_use = Counter()
        for p in m.patches:
            cps = p.control_points
            if len(cps) >= 16:
                for e in _PATCH_EDGES:
                    edge_use[frozenset(cps[i] for i in e)] += 1
        dangling = sum(1 for v in edge_use.values() if v == 1)
        if dangling:
            problems.append(f"mesh is not watertight: {dangling} dangling patch edge(s) "
                            f"(the editor's patchmesh loader crashes on these)")

    bone_names = {b.name.lower() for b in m.bones}     # bones match case-insensitively

    for lm in m.limbs:
        for pi in lm.prims:
            if not 0 <= pi < npatches:
                problems.append(f"limb {lm.name}: patch {pi} out of range")
                break
        for bn in lm.bone_names:
            if bn.lower() not in bone_names:
                problems.append(f"limb {lm.name}: references unknown bone {bn!r}")

    tex_names = {t.name.lower() for t in m.textures}
    for t in m.textures:
        if len(t.palette) != t.palette_entries * 4:
            problems.append(f"texture {t.name}: palette is {len(t.palette)} bytes, "
                            f"expected {t.palette_entries * 4}")
        if t.mip_count != len(t.mips):
            problems.append(f"texture {t.name}: mip_count {t.mip_count} != {len(t.mips)} mips")
        w, h = t.width, t.height
        for level, data in enumerate(t.mips):
            if len(data) != w * h:
                problems.append(f"texture {t.name}: mip {level} is {len(data)} bytes, "
                                f"expected {w * h} ({w}x{h})")
            w, h = max(1, w // 2), max(1, h // 2)

    for mat in m.materials:
        for tag, value in mat.tags:
            if tag in _TEX_TAGS and value and value.lower() not in tex_names:
                problems.append(f"material {mat.name}: {tag} references {value!r}, "
                                f"not an embedded texture")

    for conn in m.limb_connections:
        if conn.bone_name.lower() not in bone_names:
            problems.append(f"connection {conn.name}: references unknown bone {conn.bone_name!r}")
        ring = conn.vertex_indices
        n_edges = len(ring) // 2
        if len(ring) % 2 != 0 or n_edges not in (4, 6):
            problems.append(f"connection {conn.name}: ring has {n_edges} edges, expected 4 or 6")
        for vi in ring:
            if not 0 <= vi < nverts:
                problems.append(f"connection {conn.name}: ring control point {vi} out of range")
                break

    return problems


# --------------------------------------------------------------------------- #
# Torso skin export + the in-game asset checklist.
# --------------------------------------------------------------------------- #
def torso_texture(model: SgmModel) -> Optional[Texture]:
    """The creature's torso/body skin — the texture the combiner wants as ``<Name>_torso.bmp``.
    Picks the ``*torso*``/``*body*`` material's texture (else the first textured material)."""
    by_name = {t.name.lower(): t for t in model.textures}
    ordered = sorted(model.materials,
                     key=lambda mt: 0 if ("torso" in mt.name.lower() or "body" in mt.name.lower())
                     else 1)
    for mat in ordered:
        for tag, value in mat.tags:
            if tag in _TEX_TAGS and value and value.lower() in by_name:
                return by_name[value.lower()]
    return None


def write_torso_bmp(model: SgmModel, out_path: str) -> str:
    """Write the combiner's required ``<Name>_torso.bmp`` from the model's own torso skin."""
    tex = torso_texture(model)
    if tex is None:
        raise ValueError("no torso/body texture found to export")
    with open(out_path, "wb") as fh:
        fh.write(tex.to_bmp())
    return out_path


def check_ebp_model_refs(ebp: Ebp, model: SgmModel) -> List[str]:
    """Every animation the ``.ebp`` motion tree names must exist in the ``.sgm`` — combine looks
    each one up and a missing one crashes."""
    problems: List[str] = []
    anims = {a.name.lower() for a in model.animations}
    for ref in ebp.animation_refs():
        if ref.lower() not in anims:
            problems.append(f".ebp references animation {ref!r} the .sgm lacks "
                            f"(combine crashes on the lookup)")
    return problems


def check_torso_bmp(stock_dir: str, name: str) -> List[str]:
    """The combiner loads ``DATA:ART/EBPS/STOCK/<NAME>_torso.bmp`` the instant its screen builds;
    a missing file is a NULL deref (hard crash). Must be an 8-bit BMP (stock skins are 256x256).
    Case-insensitive (the engine uppercases the request)."""
    want = (name + "_torso.bmp").lower()
    found = next((f for f in os.listdir(stock_dir) if f.lower() == want), None)
    if not found:
        return [f"missing {name}_torso.bmp (combiner torso skin -> NULL-deref crash on open)"]
    head = open(os.path.join(stock_dir, found), "rb").read(54)
    if head[:2] != b"BM":
        return [f"{found}: not a BMP"]
    w, h = struct.unpack_from("<ii", head, 18)
    bpp = struct.unpack_from("<H", head, 28)[0]
    problems = []
    if bpp != 8:
        problems.append(f"{found}: {bpp}-bit, the combiner needs 8-bit paletted")
    if (w, h) != (256, 256):
        problems.append(f"WARN {found}: {w}x{h}; stock torso skins are 256x256")
    return problems


def check_creature_assets(stock_dir: str, name: str) -> List[str]:
    """Validate a creature's full on-disk asset set in ``stock_dir`` (an ``art/ebps/stock`` folder).
    Maps each known in-game crash cause to a check; ``WARN``-prefixed items are non-fatal. Returns
    [] when the creature is safe to combine."""
    from .creature import read_sgm
    problems: List[str] = []
    base = os.path.join(stock_dir, name)

    sgm_path = base + ".sgm"
    if not os.path.exists(sgm_path):
        return [f"missing {name}.sgm"]
    model = read_sgm(sgm_path)
    problems += [f".sgm: {p}" for p in check_model(model)]

    ebp_path = base + ".ebp"
    if not os.path.exists(ebp_path):
        problems.append(f"missing {name}.ebp (combiner blueprint)")
    else:
        ebp = Ebp.load(ebp_path)
        problems += check_ebp_model_refs(ebp, model)
        ebpd = [c for c in ebp._sgm.all_chunks() if c.tag == "EBPD"]
        if not ebpd or len(ebpd[0].raw) < 64:
            problems.append(f"WARN {name}.ebp has no real icon (combiner shows the default)")

    if not os.path.exists(base + ".lua"):
        problems.append(f"missing {name}.lua (controller/attributes -> combiner crash)")
    if not os.path.exists(base + ".lsc"):
        problems.append(f"WARN missing {name}.lsc (limb-scale; editor warns, non-fatal)")

    problems += check_torso_bmp(stock_dir, name)
    return problems
