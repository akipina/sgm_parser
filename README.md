# sgm_parser

A small, modular, pure-Python reader/writer for Impossible Creatures `.sgm`
files. Untouched chunks round-trip byte-for-byte, so edits stay surgical.

## Install

```bash
pip install git+https://github.com/akipina/sgm_parser.git
```

Or, for local development:

```bash
git clone https://github.com/akipina/sgm_parser.git
cd sgm_parser
pip install -e .
```

## Quick start

```python
from sgm_parser import Sgm

model = Sgm.load("FuckyFish.sgm")
print(model.format)            # 'BOBJ' (creature) or 'NOBS' (scene)
print(model.dump())            # the whole chunk tree
for c in model.connections:    # combine seams
    print(c.name, c.slot_label, c.host_bone)

model.materials[0].texture = "arapaima_torso.bmp"   # edit
model.save("fixed.sgm")        # untouched chunks stay byte-identical
```

## Layers

Low to high: `sgm_parser.binary` → `sgm_parser.chunks` → `sgm_parser.parser`
→ `sgm_parser.model`.

## Used by

- [sgm_explorer](https://github.com/akipina/sgm_explorer) — a hex
  inspector / chunk-tree browser built on this library.
