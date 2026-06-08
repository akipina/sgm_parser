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

### Build a creature from scratch

You can also assemble an `SgmModel` field by field and write it out. This minimal example
makes a one-patch creature with a single bone and a textured material:

```python
from sgm_parser import SgmModel, Patch, Bone, Material, write_sgm_file

model = SgmModel(name="Bullgator")

# 16 mesh points form the control grid for one bicubic patch
model.vertices = [(float(i % 4), float(i // 4), 0.0) for i in range(16)]

# one material that references a diffuse texture
model.materials = [Material(name="body", tags=[("TXTR", "bullgator_torso.bmp")])]
model.material_index = ["body"]

# one bicubic patch using all 16 control points, bound to material 0
model.patches = [Patch(material_index=0, type=0,
                       control_points=list(range(16)), uvs=[0.0] * 8)]

# a single root bone that owns the whole mesh (identity transform)
identity = [1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1]
model.bones = [Bone(name="root", parent=None, start_vertex=0, vertex_count=16,
                    transform=identity, tail=[0.0, 1.0, 0.0])]

write_sgm_file(model, "scratch.sgm")
```

This shows the shape of a model, not a game-ready creature — a real one needs proper
geometry, skinning, limb slots, and combine sockets. For building those from an actual mesh,
see [sgm_blender](https://github.com/akipina/sgm_blender), which generates all of it from a
Blender cage.

## How saving works

`write_sgm_file` rebuilds the file from the model, but only the parts you can edit are
reconstructed — chunks the model doesn't expose are carried through unchanged. In practice
that means a load-edit-save cycle leaves everything except your edits intact, so the result
stays loadable in the game's Object Editor.

Both stock animation formats are supported on read: v1 (`VERS 0,0,0,1`) and v7
(`VERS 0,0,0,7`).

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
