#!/usr/bin/env python3
"""
FX Tool -- a standalone GUI fx player + field editor for Impossible Creatures, emulating the original
FXTool (IC_FX_Tool_Documentation.doc). Three sections, like the original:

  * Main Menubar : File (New/Open/Save/Save As) | Edit (Emitter position/Background/Gravity/Wind)
                   | View (Grid/Wireframe/Statgraph/Choose Model) | Help (About)
  * Viewer       : the effect playing, with a keyframe slider + Play/Stop below it
  * Properties   : the effect's property list + a tweak box for the selected property

Renders the real algorithms (sgm_parser.fxsim) with engine blend modes, resolving SPRAY Particle_Fx
and COMBO/CONDITIONAL sub-fx. Edits write back to the .lua via sgm_parser.fxlua.

Run:  python3 fxtool.py [effect.lua]
"""
import json
import os
import sys

import tkinter as tk
from tkinter import ttk, filedialog, colorchooser, messagebox, simpledialog

from PIL import Image, ImageTk

_HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, _HERE)                       # for the sibling fxrender module
import importlib.util
if importlib.util.find_spec("sgm_parser") is None:   # find the sgm_parser package near us
    for _c in (os.path.join(_HERE, ".."), os.path.join(_HERE, "..", "sgm_parser"),
               os.path.join(_HERE, "..", "..", "sgm_parser")):
        if os.path.isdir(os.path.join(_c, "sgm_parser")):
            sys.path.insert(0, os.path.abspath(_c))
            break

import fxrender
from sgm_parser import fxlua, fxsim

EFFECT_TYPES = ["STYLE_SPRAY", "STYLE_RING", "STYLE_TRAIL", "STYLE_BEAM",
                "STYLE_COMBO", "STYLE_CONDITIONAL", "STYLE_SOUND"]


def _parse_value(text, vtype):
    t = (vtype or "").upper()
    s = (text or "").strip()
    try:
        if "ARRAY" in t or "COLOUR" in t or "VECTOR" in t:
            return json.loads(s)
        if "BOOL" in t or "INT" in t:
            return int(float(s))
        if "FLOAT" in t:
            return float(s)
    except Exception:
        pass
    return text


class FXToolApp(tk.Tk):
    VW, VH = 640, 480

    def __init__(self):
        super().__init__()
        self.title("FX Tool")
        self.geometry("1024x620")
        self.fx = None
        self.path = None
        self.base_dir = os.getcwd()
        self.total = 60
        self.frame = 0
        self.playing = False
        self._after = None
        self.motion = "line"
        self.renderer = fxrender.Renderer(self.VW, self.VH)
        self._photo = None

        self._build_menu()
        self._build_body()
        self._render()

    # ----------------------------------------------------------------- UI build
    def _build_menu(self):
        m = tk.Menu(self)
        f = tk.Menu(m, tearoff=0)
        f.add_command(label="New…\tCtrl+N", command=self.new_fx)
        f.add_command(label="Open…\tCtrl+O", command=self.open_fx)
        f.add_command(label="Save\tCtrl+S", command=self.save_fx)
        f.add_command(label="Save As…", command=self.save_fx_as)
        f.add_separator()
        f.add_command(label="Exit", command=self.destroy)
        m.add_cascade(label="File", menu=f)

        e = tk.Menu(m, tearoff=0)
        e.add_command(label="Emitter position…", command=self.edit_emitter)
        e.add_command(label="Background Colour…", command=self.edit_background)
        e.add_command(label="Gravity…", command=self.edit_gravity)
        e.add_command(label="Wind…", command=self.edit_wind)
        m.add_cascade(label="Edit", menu=e)

        v = tk.Menu(m, tearoff=0)
        self.var_grid = tk.BooleanVar(value=True)
        self.var_wire = tk.BooleanVar(value=False)
        v.add_checkbutton(label="Grid", variable=self.var_grid, command=self._toggle_grid)
        v.add_checkbutton(label="Wireframe", variable=self.var_wire, command=self._toggle_wire)
        v.add_command(label="Statgraph", command=self.show_stats)
        v.add_command(label="Choose Model…", command=self.choose_model)
        m.add_cascade(label="View", menu=v)

        h = tk.Menu(m, tearoff=0)
        h.add_command(label="About FX Tool…", command=self.about)
        m.add_cascade(label="Help", menu=h)
        self.config(menu=m)

        self.bind("<Control-o>", lambda e: self.open_fx())
        self.bind("<Control-s>", lambda e: self.save_fx())
        self.bind("<Control-n>", lambda e: self.new_fx())
        self.bind("<space>", lambda e: self.toggle_play())

    def _build_body(self):
        pane = ttk.PanedWindow(self, orient="horizontal")
        pane.pack(fill="both", expand=True)

        # ---- left: viewer + transport ----
        left = ttk.Frame(pane)
        self.canvas = tk.Canvas(left, width=self.VW, height=self.VH, bg="#1a1a1c",
                                highlightthickness=0)
        self.canvas.pack(padx=4, pady=4)
        tr = ttk.Frame(left)
        tr.pack(fill="x", padx=6)
        self.play_btn = ttk.Button(tr, text="▶ Play", width=8, command=self.toggle_play)
        self.play_btn.pack(side="left")
        self.slider = tk.Scale(tr, from_=0, to=self.total, orient="horizontal",
                               command=self._on_slider, showvalue=True)
        self.slider.pack(side="left", fill="x", expand=True, padx=6)
        self.frame_lbl = ttk.Label(tr, text="0/60", width=8)
        self.frame_lbl.pack(side="left")
        opt = ttk.Frame(left)
        opt.pack(fill="x", padx=6, pady=2)
        ttk.Label(opt, text="Frames:").pack(side="left")
        self.frames_var = tk.IntVar(value=self.total)
        ttk.Spinbox(opt, from_=10, to=600, width=6, textvariable=self.frames_var,
                    command=self._set_total).pack(side="left", padx=4)
        ttk.Label(opt, text="Emitter:").pack(side="left", padx=(10, 0))
        self.motion_var = tk.StringVar(value="line")
        ttk.Combobox(opt, textvariable=self.motion_var, width=8, state="readonly",
                     values=["static", "line"]).pack(side="left", padx=4)
        self.motion_var.trace_add("write", lambda *a: self._render())
        pane.add(left, weight=3)

        # ---- right: properties ----
        right = ttk.Frame(pane)
        self.head = ttk.Label(right, text="(no effect loaded)", font=("", 10, "bold"))
        self.head.pack(anchor="w", padx=6, pady=(6, 2))
        cols = ("type", "value")
        self.tree = ttk.Treeview(right, columns=cols, show="tree headings", height=18)
        self.tree.heading("#0", text="Property")
        self.tree.heading("type", text="Type")
        self.tree.heading("value", text="Value")
        self.tree.column("#0", width=150)
        self.tree.column("type", width=90)
        self.tree.column("value", width=140)
        self.tree.pack(fill="both", expand=True, padx=6)
        self.tree.bind("<<TreeviewSelect>>", self._on_select)
        ed = ttk.LabelFrame(right, text="Tweak selected property")
        ed.pack(fill="x", padx=6, pady=6)
        self.sel_lbl = ttk.Label(ed, text="—")
        self.sel_lbl.pack(anchor="w", padx=4)
        self.val_var = tk.StringVar()
        self.val_entry = ttk.Entry(ed, textvariable=self.val_var)
        self.val_entry.pack(fill="x", padx=4, pady=2)
        self.val_entry.bind("<Return>", lambda e: self.apply_value())
        ttk.Button(ed, text="Set", command=self.apply_value).pack(anchor="e", padx=4, pady=2)
        pane.add(right, weight=2)

    # ----------------------------------------------------------------- render
    def _emitter_path(self):
        if self.motion_var.get() == "line":
            return [(k / self.total, (k * (3.0 / self.total), 0.0, 0.0)) for k in range(self.total + 1)]
        return [(k / self.total, (0.0, 0.0, 0.0)) for k in range(self.total + 1)]

    def _render(self):
        self.renderer.grid = self.var_grid.get()
        if self.fx is None:
            img = Image.new("RGB", (self.VW, self.VH), tuple(int(c * 255) for c in self.renderer.bg))
        else:
            t = self.frame / self.total if self.total else 0.0
            dur = self.total / 30.0
            try:
                img = self.renderer.render(self.fx, t, self.base_dir,
                                           emitter_path=self._emitter_path(), duration=dur)
            except Exception as exc:
                img = Image.new("RGB", (self.VW, self.VH), (40, 0, 0))
                print("[fxtool] render error:", exc)
        self._photo = ImageTk.PhotoImage(img)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor="nw", image=self._photo)
        self.frame_lbl.config(text=f"{self.frame}/{self.total}")

    # ----------------------------------------------------------------- transport
    def toggle_play(self):
        self.playing = not self.playing
        self.play_btn.config(text="❚❚ Stop" if self.playing else "▶ Play")
        if self.playing:
            self._tick()
        elif self._after:
            self.after_cancel(self._after)
            self._after = None

    def _tick(self):
        if not self.playing:
            return
        self.frame = (self.frame + 1) % (self.total + 1)
        self.slider.set(self.frame)
        self._render()
        self._after = self.after(33, self._tick)

    def _on_slider(self, val):
        self.frame = int(float(val))
        if not self.playing:
            self._render()

    def _set_total(self):
        self.total = max(10, int(self.frames_var.get()))
        self.slider.config(to=self.total)
        self._render()

    # ----------------------------------------------------------------- properties
    def _refresh_props(self):
        self.tree.delete(*self.tree.get_children())
        if self.fx is None:
            self.head.config(text="(no effect loaded)")
            return
        name = os.path.basename(self.path) if self.path else "untitled"
        self.head.config(text=f"{name}   [{self.fx.style.replace('STYLE_', '')}]"
                              + (f"   keys @ {self.fx.frame_times}" if self.fx.frame_times else ""))
        for slot, pname, ptype, value in self.fx.properties:
            val = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
            self.tree.insert("", "end", iid=pname, text=pname,
                             values=(ptype.replace("VARTYPE_", ""), val))

    def _on_select(self, _e):
        sel = self.tree.selection()
        if not sel:
            return
        name = sel[0]
        ent = next((p for p in self.fx.properties if p[1] == name), None) if self.fx else None
        if ent is None:
            return
        _slot, pname, ptype, value = ent
        self.sel_lbl.config(text=f"{pname}  ({ptype.replace('VARTYPE_', '')})")
        self.val_var.set(json.dumps(value) if isinstance(value, (list, dict)) else str(value))

    def apply_value(self):
        sel = self.tree.selection()
        if not sel or self.fx is None:
            return
        name = sel[0]
        ent = next((p for p in self.fx.properties if p[1] == name), None)
        if ent is None:
            return
        self.fx.set(name, _parse_value(self.val_var.get(), ent[2]))
        fxrender._FX_CACHE.clear()
        self._refresh_props()
        self.tree.selection_set(name)
        self._render()

    # ----------------------------------------------------------------- File
    def new_fx(self):
        style = simpledialog.askstring("New Effect", "Effect type:\n" + ", ".join(
            s.replace("STYLE_", "") for s in EFFECT_TYPES), initialvalue="SPRAY", parent=self)
        if not style:
            return
        style = "STYLE_" + style.strip().upper().replace("STYLE_", "")
        if style not in EFFECT_TYPES:
            messagebox.showerror("FX Tool", f"Unknown type {style}")
            return
        tbl = {"properties": {}, "style": style, "frames": {"timeStart": [0, 1]}}
        self.fx = fxlua.FxEffect(style=style, table=tbl, path="")
        self.path = None
        fxrender._FX_CACHE.clear()
        self._refresh_props()
        self._render()

    def open_fx(self):
        p = filedialog.askopenfilename(title="Open FX (.lua)", filetypes=[("FX Lua", "*.lua"), ("All", "*")])
        if not p:
            return
        try:
            self.fx = fxlua.read_fx(p)
        except Exception as exc:
            messagebox.showerror("FX Tool", f"Could not open:\n{exc}")
            return
        self.path = p
        self.base_dir = os.path.dirname(p)
        fxrender._FX_CACHE.clear()
        self.frame = 0
        self.slider.set(0)
        self._refresh_props()
        self._render()

    def save_fx(self):
        if self.fx is None:
            return
        if not self.path:
            return self.save_fx_as()
        fxlua.write_fx(self.fx, self.path)
        messagebox.showinfo("FX Tool", f"Saved {os.path.basename(self.path)}")

    def save_fx_as(self):
        if self.fx is None:
            return
        p = filedialog.asksaveasfilename(defaultextension=".lua", filetypes=[("FX Lua", "*.lua")])
        if not p:
            return
        self.path = p
        self.base_dir = os.path.dirname(p)
        fxlua.write_fx(self.fx, p)
        self._refresh_props()

    # ----------------------------------------------------------------- Edit
    def edit_gravity(self):
        v = simpledialog.askstring("Gravity", "XYZ gravity (default 0 -9.8 0):",
                                   initialvalue=f"{fxsim.GRAVITY[0]} {fxsim.GRAVITY[1]} {fxsim.GRAVITY[2]}",
                                   parent=self)
        if v:
            try:
                fxsim.GRAVITY = tuple(float(x) for x in v.split()[:3])
                self._render()
            except Exception:
                pass

    def edit_wind(self):
        messagebox.showinfo("Wind", "Wind force preview affects Wind-dynamics particles only (not yet "
                                    "simulated in this preview).")

    def edit_background(self):
        c = colorchooser.askcolor(title="Background Colour")
        if c and c[0]:
            self.renderer.bg = tuple(x / 255.0 for x in c[0])
            self._render()

    def edit_emitter(self):
        messagebox.showinfo("Emitter position", "Use the Emitter dropdown (static/line) below the "
                                                "viewer to preview emitter motion.")

    # ----------------------------------------------------------------- View
    def _toggle_grid(self):
        self._render()

    def _toggle_wire(self):
        self.renderer.wire = self.var_wire.get()
        self._render()

    def show_stats(self):
        if self.fx is None:
            return
        n = sum(len(b.get("billboards", [])) + sum(len(s) for s in b.get("ribbons", []))
                for b in fxrender.leaves(self.fx, self.frame / self.total if self.total else 0,
                                         self.base_dir, duration=self.total / 30.0,
                                         emitter_path=self._emitter_path()))
        messagebox.showinfo("Statgraph", f"FX leaves: {len(fxrender.leaves(self.fx, 0.5, self.base_dir))}\n"
                                         f"Primitives this frame: {n}")

    def choose_model(self):
        messagebox.showinfo("Choose Model", "Emitter-model (.sgm) display is not rendered in this "
                                            "preview; the effect plays at the world origin.")

    def about(self):
        messagebox.showinfo("About FX Tool", "FX Tool (community) — a standalone player + field editor\n"
                            "emulating Relic's FXTool, driven by sgm_parser's reverse-engineered\n"
                            "RenderAnim/Fx algorithms.")


def main():
    app = FXToolApp()
    if len(sys.argv) > 1 and os.path.exists(sys.argv[1]):
        try:
            app.fx = fxlua.read_fx(sys.argv[1])
            app.path = sys.argv[1]
            app.base_dir = os.path.dirname(os.path.abspath(sys.argv[1]))
            app._refresh_props()
            app._render()
        except Exception as exc:
            print("could not load", sys.argv[1], exc)
    app.mainloop()


if __name__ == "__main__":
    main()
