# The per-patch `type` field in an `.sgm` PRIM record = 3ds Max smoothing groups

Each patch record in the `PRIM` chunk is 104 bytes / 26 dwords:

```
material_index : u32      # which MTRL slot
type           : u32      # <-- THIS FIELD: 3ds Max Patch::smGroup (smoothing-group bitmask)
control_points : u32[16]  # the bicubic Bezier 4x4 control grid (indices into VERT)
uvs            : f32[8]    # 4 corner UV pairs
```

## What the `type` field is

It is the modeled patch's **3ds Max / gmax `Patch::smGroup`** — a **32-bit smoothing-group
bitmask** — copied verbatim by the Relic gmax export plugin. IC creatures were modeled in
**gmax 1.2 / 3ds Max 4** as **Editable Patch** (bicubic Bézier) surfaces, which is exactly the
control-cage primitive an `.sgm` stores; every `Patch` in a `PatchMesh` carries an `smGroup`.

3ds Max SDK `patch.h`:
> `DWORD smGroup;   // Defaults to 1 -- All patches smoothed in a PatchMesh`

### Semantics (32-bit smoothing groups)
- Each of the 32 bits = membership in one of 32 smoothing groups. **LSB (bit 0) = group #1**,
  MSB (bit 31) = group #32. A patch can belong to **several groups at once** (multiple bits).
- **Shading rule:** two patches sharing an edge are **smooth-shaded across it iff they share at
  least one group bit**. If they share **no** bit, the edge renders **hard / creased**.
- **Default = 1** (group #1). Geometry that nobody retouched inherits group #1.

So the field controls **shading (normal continuity), not geometry** — it cannot create or close
holes; it decides where the surface looks smooth vs. where it has a hard crease.

## Empirical evidence (all 51 stock creatures, 14,855 patches, via `sgm_parser`)

| Observation | Value | Interpretation |
|---|---|---|
| Edge-adjacent patches sharing ≥1 bit | **96.2% smooth / 3.8% hard** | textbook smoothing-group surface: mostly smooth, sparse creases |
| Bit 0 (group #1) set | **95.6%** of patches (69% are *exactly* `1`) | the **default** group — not an invariant |
| Bits actually used | **0–16** (groups #1–#17); popcount mostly 1–2 | within the 32-group range; hand-assigned |
| Hard edges | **76% within one material**, 24% cross-material | creases are at **geometric features**, not material seams |
| bit0-unset patches | 655; 78% have a bit0-set neighbor | **isolated crease patches** (the standard "different group → hard edge" trick) |
| Upper 16 bits | zero on **14,853/14,855** patches | **not** a `Face::flags` word (which would pack material-ID there) → rules out material/edge-vis |

Sample values are all valid smoothing masks, nonsensical as a small enum:
`1` (grp1), `3` (1+2), `7` (1+2+3), `65` (1+7), `255` (1–8, an "all-on" region), `257` (1+9),
`98311 = 0b11000000000000111` (groups 1,2,3,16,17).

## How our exporter sets it today, and the consequence

`sgm_blender/io_scene_sgm/ops_export.py::_assemble_patches` writes **`type=1` on every patch**.
That puts the entire creature in smoothing group #1 → **every edge is smooth, zero hard creases**.
Result: features that stock creatures crease (beak ridge, toe separations, eye sockets, foot
edges) render as soft, rounded transitions — the body looks uniformly inflated / under-defined.
This is a **shading** gap, not a geometry gap; `type=1` is byte-for-byte valid and matches ~69%
of stock patches — it just never produces a hard edge.

## How to set it to match stock (recommended exporter behaviour)

1. **Default every patch to `1`** (group #1) — the safe baseline, identical to most stock patches.
2. **To crease an edge between two patches, give them no shared bit.** Practically: flood-fill
   the cage into regions bounded by **hard/sharp edges** (Blender "mark sharp", or dihedral angle
   over a threshold), and assign each region a distinct single bit (`1`, `2`, `4`, …, reusing bits
   for non-adjacent regions, ≤32 groups). Adjacent patches in the same smooth region share their
   bit (smooth); across a marked-sharp boundary they share none (hard edge).
3. Treat it as a true **32-bit bitmask**, never an enum — multiple bits are normal; do not
   "validate" it as a small value.
4. **Never pack anything else into it** (material has its own u32). Leave bits ≥17 clear unless
   deliberately using high groups.

## Other creature flags (for orientation — these are separate, MTRL-level, not the patch field)

Per-material tags in `MTRL` (renderer state, not gmax smoothing):
- `2SID` (u32) — two-sided. **Stock is universally 0** (one-sided); `1` makes the engine draw back
  faces → see-through "hollow" look. (Fixed to default 0 in the add-on; see `ic-mod-packaging`.)
- `SMTH` (u32) — material smooth flag (0/1), derived from roughness on export.
- `TILE` (u32) — texture address mode (0 = clamp, 1 = tile/wrap).
- `AMBC` / `DIFC` (ARGB) — ambient / diffuse colour. `TXTR` (string) — texture filename.

## Primary sources

- 3ds Max SDK `Patch` class — `smGroup` "Defaults to 1 — All patches smoothed in a PatchMesh":
  https://github.com/phoenixzz/SGPEngine/blob/master/Tools/SGP_MAX9Plugins/MAX9SDK/include/patch.h
- Autodesk SDK *Class Face* (32-bit smGroup; LSB=group#1…MSB=group#32; `Face::flags` packs
  edge-visibility + material-ID in the upper 16 bits — the alternative we ruled out):
  http://docs.autodesk.com/3DSMAX/16/ENU/3ds-Max-SDK-Programmer-Guide/cpp_ref/class_face.html
- Autodesk *Smoothing Groups* (1–32; shared-bit → smooth, no-shared-bit → hard edge):
  https://help.autodesk.com/view/3DSMAX/2024/ENU/?guid=GUID-1244162D-A063-486C-BD9B-168466F6488B
- Autodesk *Editable Patch* (patch continuity lives at handle/vertex level — coplanar vs corner —
  so the per-patch 32-bit field is the smoothing group, not per-edge handle data):
  https://help.autodesk.com/view/3DSMAX/2024/ENU/?guid=GUID-FA026666-5696-4B8C-8827-200FEC8288EF
- IC pipeline (gmax 1.2 / 3ds Max 4 + Relic-only "export model (relic)" plugin, patch limbs) — no
  community source documents the PRIM record fields, so this identification rests on the 3ds Max
  patch mechanics + the empirical fit, not an IC-specific spec:
  https://steamcommunity.com/sharedfiles/filedetails/?id=735413158
