# sgm_parser

A pure-Python library for reading, editing, and writing *Impossible Creatures*
(Relic, 2003) `.sgm` model files. Load a creature into plain Python objects, change what you
need — textures, materials, the skeleton, animations — and write it back out.

Standard library only, Python 3.8+.

- **Repo:** <https://github.com/akipina/sgm_parser>
- **Used by:** [sgm_explorer](https://github.com/akipina/sgm_explorer) (a `.sgm` browser) and
  [sgm_blender](https://github.com/akipina/sgm_blender) (a Blender import/export add-on).

This is an unofficial fan project and ships no game assets. *Impossible Creatures* and the
`.sgm` format belong to their respective rights holders.

## Install

```bash
pip install "git+https://github.com/akipina/sgm_parser.git"
```

For local development:

```bash
git clone https://github.com/akipina/sgm_parser.git
cd sgm_parser
pip install -e .
```

## Getting started

`read_sgm` turns a file into an `SgmModel`; `write_sgm_file` writes one back.

```python
from sgm_parser import read_sgm, write_sgm_file

model = read_sgm("Bullgator.sgm")

print(model.name)                 # the creature's name
print(len(model.vertices))        # mesh size
print(len(model.bones))           # skeleton
print(len(model.animations))      # animations

write_sgm_file(model, "Bullgator_copy.sgm")
```

An `SgmModel` is just a dataclass. Its main fields:

| Field | What it holds |
| --- | --- |
| `name` | the creature name |
| `vertices` | mesh points as `(x, y, z)` tuples |
| `patches` | the bicubic Bezier surface patches |
| `bones` | the skeleton (`Bone` objects: name, parent, transform, vertex range) |
| `materials` | `Material` objects (name + tag list, see below) |
| `textures` | embedded `Texture` images (TXMP: palette + mip levels) |
| `limbs`, `limb_connections` | limb slots and the sockets where limbs combine |
| `animations` | `Animation` objects (name, frame count, fps, per-bone tracks) |

## Examples

### Swap a texture and save

A material's texture references live in its `tags` list as `(tag, filename)` pairs — `TXTR`
is the diffuse map, `TXRF` reflection, `TXSP` specular. To point a material at a different
diffuse texture:

```python
from sgm_parser import read_sgm, write_sgm_file

model = read_sgm("Bullgator.sgm")

mat = model.materials[0]
mat.tags = [("TXTR", "bullgator_torso.bmp") if tag == "TXTR" else (tag, value)
            for tag, value in mat.tags]

write_sgm_file(model, "Bullgator_retex.sgm")
```

### Rename a creature

```python
from sgm_parser import read_sgm, write_sgm_file

model = read_sgm("Bullgator.sgm")
model.name = "Riverbull"
write_sgm_file(model, "Riverbull.sgm")
```

### List materials and animations

```python
from sgm_parser import read_sgm

model = read_sgm("Bullgator.sgm")

for mat in model.materials:
    textures = [name for tag, name in mat.tags if tag in ("TXTR", "TXRF", "TXSP")]
    print(mat.name, textures)

for anim in model.animations:
    print(f"{anim.name}: {anim.frame_count} frames @ {anim.fps} fps, "
          f"{len(anim.tracks)} bone tracks")
```

### Build a complete creature from scratch

You can also assemble an `SgmModel` field by field. This builds a cube-shaped creature with
everything a creature needs — geometry, a skeleton, a material with an embedded texture, a
limb slot, and an animation — and writes it to `cube.sgm`. The same script is in
[`examples/cube_creature.py`](examples/cube_creature.py).

```python
from sgm_parser import (SgmModel, Patch, Bone, Material, Texture,
                        Animation, BoneTrack, V1Channel, write_sgm_file)
from sgm_parser.creature import Limb     # the model dataclass (top-level Limb is the chunk)

# Geometry: the cube's 6 faces, each a flat 4x4 grid of Bezier control points.
# Each face is (origin corner, edge-u vector, edge-v vector).
faces = [
    ((+1, -1, -1), (0, 2, 0), (0, 0, 2)),   # +X
    ((-1, -1, -1), (0, 2, 0), (0, 0, 2)),   # -X
    ((-1, +1, -1), (2, 0, 0), (0, 0, 2)),   # +Y
    ((-1, -1, -1), (2, 0, 0), (0, 0, 2)),   # -Y
    ((-1, -1, +1), (2, 0, 0), (0, 2, 0)),   # +Z
    ((-1, -1, -1), (2, 0, 0), (0, 2, 0)),   # -Z
]
verts, patches = [], []
for origin, du, dv in faces:
    base = len(verts)
    for i in range(4):
        for j in range(4):
            verts.append(tuple(origin[k] + du[k] * i / 3 + dv[k] * j / 3 for k in range(3)))
    patches.append(Patch(material_index=0, type=1,
                         control_points=list(range(base, base + 16)),
                         uvs=[0, 0, 1, 0, 1, 1, 0, 1]))

# A tiny 2x2 two-colour texture, with the full mip chain down to 1x1.
texture = Texture(name="cube.bmp", palette_entries=2, format_code=0, width=2, height=2,
                  mip_count=2, palette=bytes([200, 80, 40, 0,  40, 80, 200, 0]),
                  mips=[bytes([0, 1, 1, 0]), bytes([0])])

# A material that references the texture.
material = Material(name="skin", tags=[("TXTR", "cube.bmp"),
                                       ("DIFC", (255, 200, 200, 200)),
                                       ("AMBC", (255, 25, 25, 25)),
                                       ("SMTH", 0)])

# One bone owning the whole mesh (identity transform).
identity = [1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1]
bone = Bone(name="torso", parent=None, start_vertex=0, vertex_count=len(verts),
            transform=identity, tail=[0.0, 1.0, 0.0])

# One limb (the Torso slot, id 2048) owning all 6 patches.
limb = Limb(name="Cube_Torso", limb_id=2048, prims=list(range(6)), bone_names=["torso"])

# A one-frame "rest" animation: a single key on each channel of the bone.
anim = Animation(name="idle", frame_count=1, fps=30.0, vers=(0, 0, 0, 1),
                 tracks=[BoneTrack(bone_name="torso",
                                   v1_loc=V1Channel(keys=[(None, (0.0, 0.0, 0.0))]),
                                   v1_rot=V1Channel(keys=[(None, (0.0, 0.0, 0.0, 1.0))]),
                                   v1_scale=V1Channel(keys=[(None, (1.0, 1.0, 1.0))]))])

model = SgmModel(name="Cube", vertices=verts, patches=patches, material_index=["skin"],
                 bones=[bone], materials=[material], textures=[texture],
                 limbs=[limb], animations=[anim])

write_sgm_file(model, "cube.sgm")
```

## How saving works

`write_sgm_file` rebuilds the file from the model, but only the parts you can edit are
reconstructed — chunks the model doesn't expose are carried through unchanged. In practice
that means a load-edit-save cycle leaves everything except your edits intact, so the result
stays loadable in the game's Object Editor.

Both stock animation formats are supported on read: v1 (`VERS 0,0,0,1`) and v7
(`VERS 0,0,0,7`).

## Entity blueprints (`.ebp`)

A creature's `.ebp` is the blueprint the game pairs with its `.sgm`: it holds the combiner
icon, the motion tree (which behaviour state plays which animation), animation events bound to
bones, and build info. Its animation/bone references **must exist in the paired `.sgm`** — a
blueprint that names an animation or bone the model lacks crashes the game on combine.

`sgm_parser.ebp` reads `.ebp` files (byte-faithful round-trip) and can **generate one matched
to a model** — the core of what the Object Editor does:

```python
from sgm_parser import Ebp, build_creature_ebp

ebp = Ebp.load("tuna.ebp")
print([(mn.state, mn.anim) for mn in ebp.motion_nodes])   # motion tree
print(ebp.animation_refs())                               # animations it needs

# Build a blueprint for your model from a working creature as a template; every animation
# and bone reference is retargeted to your model so nothing dangles:
ebp = build_creature_ebp("MyCreature.sgm", template_ebp_path="tuna.ebp",
                         template_sgm_path="tuna.sgm")
ebp.save("MyCreature.ebp")
```

## Inspecting the raw structure

If you need to see the underlying chunk tree (for debugging or to check an unfamiliar file),
`Sgm` gives you the parsed layout:

```python
from sgm_parser import Sgm

print(Sgm.load("Bullgator.sgm").dump())   # the full chunk tree, indented
```

## License

See the repository for license details. Unofficial fan project; not affiliated with or
endorsed by the rights holders of *Impossible Creatures*.
