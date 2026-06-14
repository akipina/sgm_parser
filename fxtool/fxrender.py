"""
fxrender.py -- headless renderer + effect resolver for the standalone FX Tool.

Pure (no tkinter): resolves an FX .lua (incl. SPRAY Particle_Fx + COMBO/CONDITIONAL sub-fx) into
render "leaves", simulates them with sgm_parser.fxsim, and composites the camera-facing billboards +
ribbons into a PIL image with the engine's blend modes (additive = add tex*colour; alpha = over).
The GUI wraps this; this module is unit-testable on its own.
"""
from __future__ import annotations

import math
import os

import numpy as np
from PIL import Image, ImageDraw

from sgm_parser import fxlua, fxsim

_FX_CACHE = {}        # name -> FxEffect


def _blend(fx):
    try:
        b = int(fx.get("Blending", 1))
    except Exception:
        b = 1
    return {0: "alpha", 1: "alpha", 2: "add", 3: "mul"}.get(b, "alpha")


def find_lua(name, base_dir):
    want = name.lower() + ".lua"
    d = os.path.abspath(base_dir)
    for _ in range(6):
        for sub in ("", "fx", os.path.join("art", "fx")):
            cand = os.path.join(d, sub) if sub else d
            try:
                for f in os.listdir(cand):
                    if f.lower() == want:
                        return os.path.join(cand, f)
            except OSError:
                pass
        nd = os.path.dirname(d)
        if nd == d:
            break
        d = nd
    return None


def load_fx(name, base_dir):
    key = name.lower()
    if key not in _FX_CACHE:
        p = find_lua(name, base_dir)
        try:
            _FX_CACHE[key] = fxlua.read_fx(p) if p else None
        except Exception:
            _FX_CACHE[key] = None
    return _FX_CACHE[key]


def leaves(fx, t, base_dir, fps=30.0, duration=None, emitter_path=None, depth=0):
    """Flatten an effect to a list of (primitives, texture_ref, blend) leaf bundles at time t.
    COMBO/CONDITIONAL recurse into Fx1..Fx8 (CONDITIONAL = first/land branch); SPRAY resolves its
    Particle_Fx sub-effect."""
    style = (fx.style or "").upper()
    if depth > 6:
        return []
    if "COMBO" in style or "CONDITIONAL" in style:
        subs = [v for _s, n, _ty, v in fx.properties
                if n.upper().startswith("FX") and isinstance(v, str) and v.strip()]
        if "CONDITIONAL" in style:
            subs = subs[:1]
        out = []
        for s in subs:
            sub = load_fx(s, base_dir)
            if sub is not None:
                out += leaves(sub, t, base_dir, fps, duration, emitter_path, depth + 1)
        return out
    if "SPRAY" in style:
        pname = next((v for _s, n, _ty, v in fx.properties if n == "Particle_Fx" and v), None)
        pfx = load_fx(pname, base_dir) if pname else None
        prim = fxsim.simulate(fx, t, fps=fps, duration=duration, emitter_path=emitter_path,
                              particle_fx=pfx)
        return [(prim, (pfx.texture if pfx else fx.texture), _blend(pfx or fx))]
    prim = fxsim.simulate(fx, t, fps=fps, duration=duration, emitter_path=emitter_path)
    return [(prim, fx.texture, _blend(fx))]


class TextureCache:
    def __init__(self):
        self._img = {}        # path -> HxWx3 float [0,1]
        self._scaled = {}     # (path, px) -> HxWx3 float

    def _resolve(self, ref, base_dir):
        if not ref:
            return None
        base = ref.replace("\\", "/").split("/")[-1].lower()
        d = base_dir
        for _ in range(6):
            try:
                for root, _dirs, files in os.walk(d):
                    for f in files:
                        if f.lower() == base:
                            return os.path.join(root, f)
            except OSError:
                pass
            nd = os.path.dirname(d)
            if nd == d:
                break
            d = nd
        return None

    def get(self, ref, base_dir, px):
        path = self._resolve(ref, base_dir)
        if path is None:
            return None
        if path not in self._img:
            try:
                im = Image.open(path).convert("RGB")
                self._img[path] = np.asarray(im, dtype=np.float32) / 255.0
            except Exception:
                self._img[path] = None
        base_arr = self._img[path]
        if base_arr is None:
            return None
        px = max(2, min(px, 512))
        key = (path, px)
        if key not in self._scaled:
            im = Image.fromarray((base_arr * 255).astype(np.uint8)).resize((px, px), Image.BILINEAR)
            self._scaled[key] = np.asarray(im, dtype=np.float32) / 255.0
        return self._scaled[key]


class Renderer:
    """2D additive particle compositor. Front view: world (x,y) -> screen, y up; z ignored (billboards
    already face the camera). Additive = framebuffer += tex*colour*boost; alpha = over by luminance."""

    def __init__(self, w=640, h=480):
        self.w, self.h = w, h
        self.bg = (0.10, 0.10, 0.11)
        self.zoom = 110.0
        self.grid = True
        self.wire = False
        self.boost = 2.2
        self.textures = TextureCache()

    def _project(self, p):
        return (self.w * 0.5 + p[0] * self.zoom, self.h * 0.5 - p[1] * self.zoom)

    def _grid(self, fb):
        c = np.array(self.bg) + 0.05
        step = self.zoom
        cx, cy = self.w * 0.5, self.h * 0.5
        x = cx % step
        while x < self.w:
            fb[:, int(x)] = c
            x += step
        y = cy % step
        while y < self.h:
            fb[int(y), :] = c
            y += step
        fb[int(cy)] = np.array(self.bg) + 0.12        # axes brighter
        fb[:, int(cx)] = np.array(self.bg) + 0.12

    def _blit_billboard(self, fb, center, size, rgba, sprite, additive):
        sx, sy = self._project(center)
        px = max(2, int(size * self.zoom))
        x0 = int(sx - px / 2)
        y0 = int(sy - px / 2)
        x1, y1 = x0 + px, y0 + px
        cx0, cy0 = max(0, x0), max(0, y0)
        cx1, cy1 = min(self.w, x1), min(self.h, y1)
        if cx1 <= cx0 or cy1 <= cy0:
            return
        if sprite is not None:
            spr = self.textures.get(sprite, self._base_dir, px)
        else:
            spr = None
        if spr is None:                                # fall back to a soft radial dot
            yy, xx = np.mgrid[0:px, 0:px]
            r = np.sqrt((xx - px / 2) ** 2 + (yy - px / 2) ** 2) / (px / 2 + 1e-6)
            spr = np.clip(1.0 - r, 0, 1)[..., None] * np.ones(3)
        spr = spr[cy0 - y0:cy0 - y0 + (cy1 - cy0), cx0 - x0:cx0 - x0 + (cx1 - cx0)]
        tint = np.array(rgba[:3], dtype=np.float32)
        if additive:
            fb[cy0:cy1, cx0:cx1] += spr * tint * self.boost          # black tex adds nothing
        else:
            lum = np.clip((spr.mean(axis=2) - 0.06) / 0.94, 0, 1) * float(rgba[3] if len(rgba) > 3 else 1)
            col = spr * tint
            reg = fb[cy0:cy1, cx0:cx1]
            fb[cy0:cy1, cx0:cx1] = reg * (1 - lum[..., None]) + col * lum[..., None]

    def _ribbon(self, fb, strip, additive):
        if len(strip) < 2:
            return
        layer = Image.new("RGB", (self.w, self.h), (0, 0, 0))
        dr = ImageDraw.Draw(layer)
        for k in range(len(strip) - 1):
            a0, b0, c0, _u0 = strip[k]
            a1, b1, _c1, _u1 = strip[k + 1]
            poly = [self._project(a0), self._project(b0), self._project(b1), self._project(a1)]
            col = tuple(int(min(1, x) * 255) for x in c0[:3])
            dr.polygon(poly, fill=col)
        arr = np.asarray(layer, dtype=np.float32) / 255.0
        if additive:
            fb += arr * self.boost
        else:
            m = arr.max(axis=2, keepdims=True)
            fb[:] = fb * (1 - m) + arr * m

    def render(self, fx, t, base_dir, emitter_path=None, fps=30.0, duration=None):
        self._base_dir = base_dir
        fb = np.zeros((self.h, self.w, 3), dtype=np.float32)
        fb[:] = self.bg
        if self.grid:
            self._grid(fb)
        for prim, tex, blend in leaves(fx, t, base_dir, fps, duration, emitter_path):
            additive = (blend == "add")
            for (center, size, rgba, _roll) in prim.get("billboards", []):
                self._blit_billboard(fb, center, size, rgba, tex, additive)
            for strip in prim.get("ribbons", []):
                self._ribbon(fb, strip, additive)
        np.clip(fb, 0, 1, fb)
        return Image.fromarray((fb * 255).astype(np.uint8))
