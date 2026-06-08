# sgm_parser

A small, modular, **pure-Python** reader/writer for *Impossible Creatures* (Relic, 2003)
`.sgm` model files. It decodes the chunk tree into typed Python objects, lets you inspect
or edit it, and writes it back out — **untouched chunks round-trip byte-for-byte**, so your
edits stay surgical and the engine still loads the result.

It is standard-library only (no third-party dependencies) and works on Python 3.8+.

- **Repo:** <https://github.com/akipina/sgm_parser>
- **Used by:** [sgm_explorer](https://github.com/akipina/sgm_explorer) (hex/chunk GUI) and
  [sgm_blender](https://github.com/akipina/sgm_blender) (Blender import/export add-on).

> This is an unofficial, fan-made tool for an old game. *Impossible Creatures* and the
> `.sgm` format are property of their respective rights holders; this project ships no game
> assets.

## Highlights

- **Byte-faithful round-trip.** Parse → save with no edits reproduces the input exactly.
  Only chunks you actually modify are re-serialized; everything else is preserved verbatim.
- **Two layers, pick your altitude:**
  - a **low-level chunk tree** (`Sgm`) for inspection and surgical byte-level patching, and
  - a **high-level creature model** (`read_sgm` → `SgmModel`) of typed dataclasses
    (vertices, patches, bones, materials, textures, limbs, connections, animations) for
    tools that build or rewrite whole creatures.
- **Creatures *and* scenes.** Handles both `BOBJ` (creature) and `NOBS` (scene) containers.
- **Animations decoded.** Both stock animation layouts — v1 (`VERS 0,0,0,1`) and v7
  (`VERS 0,0,0,7`) — are understood by the creature model.
- **Self-documenting.** `model.dump()` prints the whole chunk tree; `model.byte_account()`
  reports where every byte went.

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

## Quick start

```python
from sgm_parser import Sgm

model = Sgm.load("Bullgator.sgm")
print(model.format)            # 'BOBJ' (creature) or 'NOBS' (scene)
print(model.dump())            # the whole chunk tree, indented

for c in model.connections:    # combine seams between limbs
    print(c.name, c.slot_label, c.host_bone)

model.materials[0].texture = "bullgator_torso.bmp"   # edit a diffuse texture name
model.save("fixed.sgm")        # untouched chunks stay byte-identical
```

## The `.sgm` format in one minute

An `.sgm` file is a tree of **FORM/chunk** records, EA-IFF-85 style:

- Each chunk has a **4-character tag** (e.g. `FORM`, `BOBJ`, `MTRL`, `TXMP`, `VERT`,
  `ANIM`/`BANM`), a size, and a payload. `FORM` chunks contain children; others carry data.
- The top-level container tag tells you the kind of file: **`BOBJ`** for a creature,
  **`NOBS`** for a scene.
- **Endianness gotcha:** chunk **sizes are big-endian**, but **numeric payload fields are
  little-endian**. `sgm_parser` handles both for you; it matters mostly if you hand-patch
  bytes elsewhere (see sgm_explorer).

`sgm_parser` reads this tree once, keeps each chunk's original bytes, and only rebuilds the
bytes of chunks you change.

## Two APIs

### 1. Low-level chunk tree — `Sgm`

Best for inspection and minimal, surgical edits.

```python
from sgm_parser import Sgm

m = Sgm.load("Bullgator.sgm")

m.format                       # 'BOBJ' or 'NOBS'
m.find("MTRL")                 # first chunk with this tag, or None
m.find_all("TXMP")             # every chunk with this tag
m.all_chunks()                 # flat list of every chunk in the tree
m.dump()                       # pretty-printed tree as a string
m.byte_account()               # dict: bytes accounted for, per tag

# Convenience views (lists of the underlying chunk objects):
m.vertices, m.materials, m.textures, m.bones, m.limbs, m.connections

# Edit a typed chunk field, then write back:
m.materials[0].texture = "bullgator_torso.bmp"
data = m.to_bytes()            # serialize to bytes ...
m.save("out.sgm")              # ... or straight to a file
```

### 2. High-level creature model — `read_sgm` / `write_sgm_file`

Best for tools that assemble or rewrite a whole creature (this is the API the Blender
add-on uses). Everything is plain dataclasses.

```python
from sgm_parser import read_sgm, write_sgm_file, SgmModel

model = read_sgm("Bullgator.sgm")        # -> SgmModel
model.name                                # creature name
model.vertices                            # list[(x, y, z)]
model.patches                             # bicubic Bezier patches
model.bones                               # skeleton
model.materials, model.textures           # typed Material / Texture
model.limbs, model.limb_connections       # limb slots + combine sockets
model.animations                          # decoded v1/v7 animations

write_sgm_file(model, "rebuilt.sgm")      # or: data = write_sgm(model)
```

`SgmModel` fields: `version`, `name`, `vertices`, `patches`, `material_index`, `bones`,
`materials`, `textures`, `limbs`, `limb_connections`, `animations` (plus a few raw
pass-through fields for chunks that are preserved verbatim).

## Architecture

Layered, low to high — each layer depends only on the ones beneath it:

| Layer | Module | Responsibility |
| --- | --- | --- |
| Bytes | `sgm_parser.binary` | `BinaryReader` / `BinaryWriter` (endian-aware primitives) |
| Chunks | `sgm_parser.chunks` | the FORM/chunk tree, tag registry, typed chunk classes |
| Parse | `sgm_parser.parser` | `ChunkParser` — bytes ⇄ chunk tree |
| Model | `sgm_parser.model` | `Sgm` — the file-level object and convenience views |
| Creature | `sgm_parser.creature`, `sgm_parser.anim` | `SgmModel` + `read_sgm`/`write_sgm` |

## Project layout

```
sgm_parser/
├── binary.py        # endian-aware read/write primitives
├── constants.py     # tags, slot types, enums
├── chunks/          # chunk classes + registry (base, common, creature, scene)
├── parser.py        # ChunkParser: bytes <-> chunk tree
├── model.py         # Sgm: file object, find/dump/byte_account, save/load
├── creature.py      # SgmModel + read_sgm / write_sgm(_file)
└── anim.py          # v1 / v7 animation channel semantics
```

## Round-trip guarantee (and how to verify it)

```python
from sgm_parser import Sgm
raw = open("Bullgator.sgm", "rb").read()
assert Sgm.from_bytes(raw).to_bytes() == raw   # byte-identical with no edits
```

Because only modified chunks are rebuilt, edits to one material or texture name won't
perturb the bytes of any other chunk — which is what keeps engine-touchy files loadable.

## License

See the repository for license details. Unofficial fan project; not affiliated with or
endorsed by the rights holders of *Impossible Creatures*.
