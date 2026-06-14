# FX Tool

A standalone GUI **fx player + field editor** for Impossible Creatures, emulating Relic's FXTool
(see `IC_FX_Tool_Documentation.doc`). Plays `.lua` particle effects with the reverse-engineered
`RenderAnim/Fx` algorithms (`sgm_parser.fxsim`) and edits their properties (`sgm_parser.fxlua`).

## Run
```
python3 fxtool.py [effect.lua]
```
Requires `tkinter`, `numpy`, `Pillow`, and `sgm_parser` importable (found automatically if run from
the repo). Open a `.lua` from `art/fx/`, scrub the keyframe slider or press Play, click a property to
tweak it, and Save writes back to the `.lua`.

## Layout (like FXTool)
- **File**: New / Open / Save / Save As
- **Edit**: Emitter position / Background Colour / Gravity / Wind
- **View**: Grid / Wireframe / Statgraph / Choose Model
- **Viewer** + keyframe slider + Play/Stop; **Properties** list + tweak box.

Renders SPRAY particles as their `Particle_Fx` sub-fx and composes COMBO/CONDITIONAL sub-effects,
with the engine blend modes (additive = `Blending 2`).
