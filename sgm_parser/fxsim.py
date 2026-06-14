"""
fxsim.py -- pure-Python simulator for Impossible Creatures particle FX, for a Blender PREVIEW.

Implements the per-style render algorithms reverse-engineered from RenderAnim/Fx/* (see the
fx-render-pipeline notes). NO bpy here -- given a parsed effect (style + typed/keyframed properties +
frames.timeStart) and a normalized time t in [0,1], `simulate()` returns neutral PRIMITIVES the
Blender binder turns into geometry. Deterministic: re-simulating from 0 to the same time yields the
same result (so scrubbing/looping is stable), with per-particle seeded randomness.

Primitive bundle (a dict):
  billboards : list of (center(x,y,z), size, rgba(0..1), roll_radians)   -- camera-facing quads
  ribbons    : list of strips; each strip = list of (vA(x,y,z), vB(x,y,z), rgba, u)  -- world ribbon
  meshinsts  : list of (matrix4x4_rowmajor, rgba)                        -- UseMesh ring instances
  blend      : "alpha" | "add" | "mul"
  texture    : the effect's texture ref (a DATA:... path) or ""

An `effect` is any object exposing: .style (str), .frame_times (list[float] or None),
.properties (iterable of (slot, name, vartype, value)). sgm_parser.fxlua.FxEffect matches this.
"""
from __future__ import annotations

import math
import random
from typing import List, Optional

GRAVITY = (0.0, -9.81, 0.0)


# --------------------------------------------------------------------------- keyframe sampling
def _as_keyframes(value):
    """Normalize an ARRAY_* value to an ordered list of entries. lua stores ARRAY_VECTOR3/COLOUR as
    {entry_00:[...], entry_01:[...]} (a dict) and ARRAY_FLOAT as a flat list."""
    if isinstance(value, dict):
        return [value[k] for k in sorted(value)]
    if isinstance(value, (list, tuple)):
        return list(value)
    return [value]


def _seg(times, t):
    """Return (i, f): lower keyframe index i and blend fraction f for normalized t over `times`."""
    n = len(times)
    if n < 2:
        return 0, 0.0
    if t <= times[0]:
        return 0, 0.0
    if t >= times[-1]:
        return n - 2, 1.0
    i = 0
    while i < n - 1 and times[i + 1] < t:
        i += 1
    span = times[i + 1] - times[i]
    f = (t - times[i]) / span if span > 1e-9 else 0.0
    return i, f


def _lerp_floats(values, times, t):
    seq = _as_keyframes(values)
    if not seq:
        return 0.0
    if len(seq) == 1:
        return float(seq[0])
    i, f = _seg(times, t)
    i = min(i, len(seq) - 2)
    return float(seq[i]) * (1 - f) + float(seq[i + 1]) * f


def _lerp_vecs(values, times, t, dim):
    seq = _as_keyframes(values)
    if not seq:
        return [0.0] * dim
    if len(seq) == 1:
        return [float(x) for x in seq[0]]
    i, f = _seg(times, t)
    i = min(i, len(seq) - 2)
    a, b = seq[i], seq[i + 1]
    return [float(a[k]) * (1 - f) + float(b[k]) * f for k in range(dim)]


class Params:
    """Type-aware accessor over an effect's properties, sampling keyframed (ARRAY_*) ones by time."""

    def __init__(self, effect):
        self.times = list(effect.frame_times) if effect.frame_times else [0.0, 1.0]
        self.byname = {name: (vtype or "", value) for _slot, name, vtype, value in effect.properties}

    def has(self, name):
        return name in self.byname

    def at(self, name, t, default=None):
        ent = self.byname.get(name)
        if ent is None:
            return default
        vtype, val = ent
        if "ARRAY_FLOAT" in vtype:
            return _lerp_floats(val, self.times, t)
        if "ARRAY_COLOUR" in vtype:
            c = _lerp_vecs(val, self.times, t, 4)
            return [c[0] / 255.0, c[1] / 255.0, c[2] / 255.0, c[3] / 255.0]
        if "ARRAY_VECTOR3" in vtype:
            return _lerp_vecs(val, self.times, t, 3)
        if "COLOUR" in vtype:
            return [val[0] / 255.0, val[1] / 255.0, val[2] / 255.0, val[3] / 255.0]
        if "VECTOR3" in vtype:
            return [float(x) for x in val]
        if "BOOL" in vtype or "INT" in vtype:
            try:
                return int(float(val))
            except Exception:
                return 0
        if "FLOAT" in vtype:
            try:
                return float(val)
            except Exception:
                return 0.0
        return val            # STRING

    def num(self, name, default=0.0):
        v = self.at(name, 0.0, default)
        try:
            return float(v)
        except Exception:
            return default


def _blend(P):
    b = P.at("Blending", 0.0, 1)
    try:
        b = int(b)
    except Exception:
        b = 1
    return {0: "alpha", 1: "alpha", 2: "add", 3: "mul"}.get(b, "alpha")


def _texture(P):
    ent = P.byname.get("Texture") or P.byname.get("Mesh")
    return ent[1] if ent else ""


def _empty(P):
    return {"billboards": [], "ribbons": [], "meshinsts": [], "blend": _blend(P), "texture": _texture(P)}


def duration_of(effect, default=2.0):
    P = Params(effect)
    for key in ("Duration", "Emitter_Duration"):
        if P.has(key):
            d = P.num(key, default)
            # building/fx durations are sometimes stored huge (100) meaning "until killed"; clamp for preview
            return max(0.1, min(d, 10.0)) if d > 0 else default
    return default


# --------------------------------------------------------------------------- per-style
def _ring(P, t):
    """STYLE_RING (preview): a camera-facing textured quad sized 2*Radius at Offset, colour Colour,
    rolled by accumulated Spin. (Engine sweeps many such quads; for offset=0 they stack into one ring,
    which this single animated quad represents.)"""
    radius = P.at("Radius", t, 1.0)
    radius = float(radius) if radius else 1.0
    colour = P.at("Colour", t, [1, 1, 1, 1]) or [1, 1, 1, 1]
    offset = P.at("Offset", t, [0, 0, 0]) or [0, 0, 0]
    spin = P.at("Spin", t, 0.0) or 0.0
    out = _empty(P)
    out["billboards"].append((tuple(offset[:3]), max(2 * radius, 0.01), tuple(colour[:4]),
                              math.radians(float(spin)) * t))
    return out


def _beam(P, t, endpoint=(0.0, 0.0, 3.0)):
    """STYLE_BEAM (preview): a straight ribbon from origin to `endpoint`, half-width Width/2, colour
    keyframed, U along length. Segments fixed at a few; no jitter (none confirmed in the decomp)."""
    width = P.num("Width", 0.2)
    colour = P.at("Colour", t, [1, 1, 1, 1]) or P.at("ColourHead", t, [1, 1, 1, 1]) or [1, 1, 1, 1]
    out = _empty(P)
    segs = 8
    A = (0.0, 0.0, 0.0)
    B = endpoint
    # side vector: perpendicular to the beam in the XY plane (binder re-faces to camera for billboard)
    dx, dy, dz = B[0] - A[0], B[1] - A[1], B[2] - A[2]
    ln = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
    side = (-(dy) / ln, dx / ln, 0.0)
    hw = width * 0.5
    strip = []
    for s in range(segs + 1):
        u = s / segs
        cx = A[0] + dx * u
        cy = A[1] + dy * u
        cz = A[2] + dz * u
        a = (cx + side[0] * hw, cy + side[1] * hw, cz + side[2] * hw)
        b = (cx - side[0] * hw, cy - side[1] * hw, cz - side[2] * hw)
        strip.append((a, b, tuple(colour[:4]), u))
    out["ribbons"].append(strip)
    return out


def _trail(P, t, path):
    """STYLE_TRAIL (preview): ribbon along the emitter `path` (a list of (frame_t, pos)), keeping the
    samples within the last `Length` of normalized time; colour lerps ColourHead->ColourTail head->tail;
    width = Width (half). `path` is provided by the binder (emitter motion)."""
    length = max(P.num("Length", 0.7), 0.05)
    width = P.num("Width", 0.2)
    head = P.at("ColourHead", t, [1, 1, 1, 1]) or [1, 1, 1, 1]
    tail = P.at("ColourTail", t, [1, 1, 1, 0]) or [1, 1, 1, 0]
    out = _empty(P)
    # window in normalized time proportional to Length (rough: Length seconds / a 2s nominal life)
    win = min(1.0, length / 2.0)
    pts = [(pt, pos) for (pt, pos) in path if t - pt <= win and pt <= t]
    if len(pts) < 2:
        return out
    hw = width * 0.5
    strip = []
    span = max(pts[-1][0] - pts[0][0], 1e-6)
    for k, (pt, pos) in enumerate(pts):
        frac = (t - pt) / win if win > 1e-6 else 0.0           # 0 head -> 1 tail
        col = [head[i] * (1 - frac) + tail[i] * frac for i in range(4)]
        # side: perpendicular to motion in XZ, fallback world X
        if 0 < k < len(pts):
            pprev = pts[k - 1][1]
            d = (pos[0] - pprev[0], pos[1] - pprev[1], pos[2] - pprev[2])
        else:
            d = (1.0, 0.0, 0.0)
        side = (-d[2], 0.0, d[0])
        sl = math.sqrt(side[0] ** 2 + side[1] ** 2 + side[2] ** 2) or 1.0
        side = (side[0] / sl * hw, 0.0, side[2] / sl * hw)
        a = (pos[0] + side[0], pos[1], pos[2] + side[2])
        b = (pos[0] - side[0], pos[1], pos[2] - side[2])
        u = (pt - pts[0][0]) / span
        strip.append((a, b, tuple(col), u))
    out["ribbons"].append(strip)
    return out


def _spray(effect, P, t, fps, duration):
    """STYLE_SPRAY (preview): replay emission 0->t. Each particle is a small billboard moving
    ballistically (semi-implicit Euler with optional gravity). Renders the emission pattern; the real
    engine renders each particle as its Particle_Fx sub-effect (TODO: recurse)."""
    out = _empty(P)
    dt = 1.0 / max(fps, 1)
    nframes = max(1, int(round(t * duration * fps)))
    direction_axis = (0.0, 1.0, 0.0)
    dyn = int(P.num("Particle_Dynamics", 0))
    grav_scale = 0.0 if dyn == 0 else 1.0
    carry = 0.0
    gidx = 0
    alive = []           # (pos[list3], vel[list3])
    for k in range(nframes):
        tk = (k / (duration * fps)) if duration * fps > 0 else 0.0
        rate = P.at("Emitter_Rate", tk, 0.0) or 0.0
        dev = math.radians(P.at("Emitter_Deviation", tk, 0.0) or 0.0)
        speed = P.at("Emitter_Speed", tk, None)
        if speed is None:
            speed = P.at("Particle_Speed", tk, 3.0)
        speed = float(speed or 3.0)
        offset = P.at("Emitter_Offset", tk, [0, 0, 0]) or [0, 0, 0]
        vol = P.at("Emitter_Volume", tk, [0, 0, 0]) or [0, 0, 0]
        carry += rate * dt
        n = int(carry + 0.5)
        carry -= n
        for _ in range(n):
            rng = random.Random(gidx)
            gidx += 1
            theta = rng.random() * (dev * 0.5)
            phi = rng.random() * 2 * math.pi
            s = math.sin(theta)
            d = (s * math.cos(phi), math.cos(theta), s * math.sin(phi))     # cone about +Y
            px = offset[0] + (rng.random() - 0.5) * (vol[0] if len(vol) > 0 else 0)
            py = offset[1] + (rng.random() - 0.5) * (vol[1] if len(vol) > 1 else 0)
            pz = offset[2] + (rng.random() - 0.5) * (vol[2] if len(vol) > 2 else 0)
            alive.append(([px, py, pz], [d[0] * speed, d[1] * speed, d[2] * speed]))
        for pos, vel in alive:
            vel[0] += GRAVITY[0] * dt * grav_scale
            vel[1] += GRAVITY[1] * dt * grav_scale
            vel[2] += GRAVITY[2] * dt * grav_scale
            pos[0] += vel[0] * dt
            pos[1] += vel[1] * dt
            pos[2] += vel[2] * dt
    for pos, _vel in alive:
        out["billboards"].append((tuple(pos), 0.12, (1.0, 1.0, 1.0, 0.9), 0.0))
    return out


def simulate(effect, t, fps=30.0, duration=None, emitter_path=None, beam_endpoint=(0.0, 0.0, 3.0)):
    """Simulate `effect` at normalized time t in [0,1]; return the primitive bundle.
    `emitter_path` (for trails) is a list of (norm_time, pos(x,y,z)); `duration` overrides the effect's."""
    P = Params(effect)
    style = (effect.style or "").upper()
    if duration is None:
        duration = duration_of(effect)
    if "RING" in style:
        return _ring(P, t)
    if "BEAM" in style:
        return _beam(P, t, beam_endpoint)
    if "TRAIL" in style:
        return _trail(P, t, emitter_path or [(0.0, (0, 0, 0))])
    if "SPRAY" in style:
        return _spray(effect, P, t, fps, duration)
    return _empty(P)        # COMBO/CONDITIONAL handled by the binder (compose sub-fx)
