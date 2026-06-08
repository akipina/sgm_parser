"""Build a complete cube-shaped creature and write it to cube.sgm.

This is the smallest example that includes everything a creature needs: geometry,
a skeleton, a material with an embedded texture, a limb slot, and an animation.
Run it with:  python examples/cube_creature.py
"""

from sgm_parser import (SgmModel, Patch, Bone, Material, Texture,
                        Animation, BoneTrack, V1Channel, write_sgm_file)
from sgm_parser.creature import Limb     # the model dataclass (top-level Limb is the chunk)


def build_cube() -> SgmModel:
    # Geometry: the cube's 6 faces, each a flat 4x4 grid of Bezier control points.
    # Each face is given as (origin corner, edge-u vector, edge-v vector).
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
                verts.append(tuple(origin[k] + du[k] * i / 3 + dv[k] * j / 3
                                   for k in range(3)))
        patches.append(Patch(material_index=0, type=1,
                             control_points=list(range(base, base + 16)),
                             uvs=[0, 0, 1, 0, 1, 1, 0, 1]))

    # A tiny 2x2 two-colour texture, with the full mip chain down to 1x1.
    texture = Texture(name="cube.bmp", palette_entries=2, format_code=0,
                      width=2, height=2, mip_count=2,
                      palette=bytes([200, 80, 40, 0,  40, 80, 200, 0]),   # 2 x B,G,R,A
                      mips=[bytes([0, 1, 1, 0]), bytes([0])])

    # A material that references the texture, with diffuse/ambient colours.
    material = Material(name="skin", tags=[
        ("TXTR", "cube.bmp"),
        ("DIFC", (255, 200, 200, 200)),
        ("AMBC", (255, 25, 25, 25)),
        ("SMTH", 0),
    ])

    # One bone owning the whole mesh (identity transform).
    identity = [1, 0, 0, 0,  0, 1, 0, 0,  0, 0, 1, 0,  0, 0, 0, 1]
    bone = Bone(name="torso", parent=None, start_vertex=0, vertex_count=len(verts),
                transform=identity, tail=[0.0, 1.0, 0.0])

    # One limb (the Torso slot, id 2048) that owns all 6 patches.
    limb = Limb(name="Cube_Torso", limb_id=2048, prims=list(range(6)),
                bone_names=["torso"])

    # A one-frame "rest" animation: a single key on each channel of the bone.
    anim = Animation(name="idle", frame_count=1, fps=30.0, vers=(0, 0, 0, 1),
                     tracks=[BoneTrack(bone_name="torso",
                                       v1_loc=V1Channel(keys=[(None, (0.0, 0.0, 0.0))]),
                                       v1_rot=V1Channel(keys=[(None, (0.0, 0.0, 0.0, 1.0))]),
                                       v1_scale=V1Channel(keys=[(None, (1.0, 1.0, 1.0))]))])

    return SgmModel(name="Cube", vertices=verts, patches=patches,
                    material_index=["skin"], bones=[bone], materials=[material],
                    textures=[texture], limbs=[limb], animations=[anim])


if __name__ == "__main__":
    write_sgm_file(build_cube(), "cube.sgm")
    print("wrote cube.sgm")
