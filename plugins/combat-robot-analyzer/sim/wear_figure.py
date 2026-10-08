#!/usr/bin/env python3
"""Wear / damage map of one part of a solved run, as a self-contained SVG.

Rasterises per-element damage — peak plastic strain reached while the element
was alive, plus the elements that eroded away — onto the part's own plane,
viewed down its thinnest principal direction.  Element positions are the
undeformed first-frame centroids, so the map reads as *where material was lost
from the part*, not where the debris ended up.

The figure is laid out from the data: a whole-part panel, a zoom panel per
erosion cluster, a section through the thickness, and a legend.  The erosion
threshold, material density and part name are read from the simulation spec.

    wear_figure.py BUILD --part 4                     # -> BUILD/<name>_wear.svg
    wear_figure.py BUILD --part 4 -c spec.yaml -o wear.svg --spin-rpm 6438
    wear_figure.py BUILD/<name>_damage.npz --part 4 --json

Needs the per-element history from extract_damage.py; if the .npz is not next
to the frames it is built once and cached there.

Prints the damage statistics as JSON on stdout; the SVG goes to --out.
"""

from __future__ import annotations

import argparse
import glob
import json
import math
import os
import sys

import numpy as np

import extract_damage
from report import locate_artefacts, read_config, read_pipeline_json


# ---------------------------------------------------------------------------
# palette
# ---------------------------------------------------------------------------
#
# Colours are emitted as var(--name, #fallback): standalone the <style> block
# below supplies them, embedded in a report page the host's own tokens win.
# The strain ramp is deliberately literal — it was picked to read on both light
# and dark grounds and must not shift with the theme.

LIGHT = {
    "surface-1": "#fcfcfb", "ink": "#1b1d21", "ink-2": "#565a61",
    "ink-3": "#8a8e95", "line": "#d9d5cb", "accent": "#b54a17",
    "series-1": "#2a78d6", "series-3": "#1baf7a",
}
DARK = {
    "surface-1": "#16181b", "ink": "#eceef1", "ink-2": "#a8adb5",
    "ink-3": "#71767e", "line": "#2f343a", "accent": "#ff8a4c",
    "series-1": "#6aa9f0", "series-3": "#3fd4a0",
}

RAMP = ["#8e9aa0", "#9c9c8d", "#b0a179", "#c2a266", "#d09b52", "#d98b42",
        "#d97539", "#cd5a33", "#bc402d"]
ERODED = "#8c1f16"
ERODED_CELL = len(RAMP)          # sentinel band index in the raster grid


def c(name):
    return "var(--%s, %s)" % (name, LIGHT[name])


def theme_style():
    def decl(d):
        return " ".join("--%s: %s;" % (k, v) for k, v in d.items())
    return ("<style>\nsvg { %s }\n@media (prefers-color-scheme: dark) "
            "{ svg { %s } }\n</style>" % (decl(LIGHT), decl(DARK)))


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;"))


# ---------------------------------------------------------------------------
# SVG
# ---------------------------------------------------------------------------

class Svg:
    def __init__(self, w, h):
        self.w, self.h, self.o = w, h, []

    def add(self, s):
        self.o.append(s)

    def text(self, x, y, s, size=11, fill=None, weight=None, anchor=None,
             family="mono"):
        f = ('font-family="ui-monospace, SF Mono, Menlo, monospace"'
             if family == "mono"
             else 'font-family="system-ui, -apple-system, sans-serif"')
        self.add('<text x="%.1f" y="%.1f" font-size="%s" fill="%s"%s%s %s>%s</text>' % (
            x, y, size, fill or c("ink-2"),
            ' font-weight="%s"' % weight if weight else "",
            ' text-anchor="%s"' % anchor if anchor else "", f, esc(s)))

    def rect(self, x, y, w, h, fill="none", stroke=None, sw=1, dash=None):
        self.add('<rect x="%.1f" y="%.1f" width="%.1f" height="%.1f" fill="%s"%s%s/>' % (
            x, y, w, h, fill,
            ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else "",
            ' stroke-dasharray="%s"' % dash if dash else ""))

    def line(self, x1, y1, x2, y2, stroke=None, sw=1, dash=None):
        self.add('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" stroke="%s" stroke-width="%s"%s/>' % (
            x1, y1, x2, y2, stroke or c("line"), sw,
            ' stroke-dasharray="%s"' % dash if dash else ""))

    def circle(self, cx, cy, r, fill="none", stroke=None, sw=1, dash=None):
        self.add('<circle cx="%.1f" cy="%.1f" r="%.1f" fill="%s" stroke="%s" stroke-width="%s"%s/>' % (
            cx, cy, r, fill, stroke or c("ink-3"), sw,
            ' stroke-dasharray="%s"' % dash if dash else ""))

    def path(self, d, fill="none", stroke=None, sw=1):
        self.add('<path d="%s" fill="%s"%s/>' % (
            d, fill,
            ' stroke="%s" stroke-width="%s"' % (stroke, sw) if stroke else ""))

    def out(self, aria, style=True):
        return ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 %d %d" '
                'width="%d" height="%d" role="img" aria-label="%s">\n%s%s\n</svg>'
                % (self.w, self.h, self.w, self.h, esc(aria),
                   theme_style() + "\n" if style else "", "\n".join(self.o)))


# ---------------------------------------------------------------------------
# rasteriser
# ---------------------------------------------------------------------------

def bucket(eps, eps_full):
    """Strain -> ramp index, saturating at the erosion threshold."""
    t = min(max(eps / eps_full, 0.0), 1.0) if eps_full > 0 else 0.0
    return min(int(t * len(RAMP)), len(RAMP) - 1)


def raster(pts, vals, eroded, region, cell, eps_full):
    """Rasterise a damage field over region (x0, y0, x1, y1).

    Returns the grid and the region it actually covers, which is the requested
    one rounded up to whole cells — panels are fitted to that, so the drawn
    field never spills past its own box.
    """
    x0, y0, x1, y1 = region
    sel = ((pts[:, 0] >= x0) & (pts[:, 0] <= x1) &
           (pts[:, 1] >= y0) & (pts[:, 1] <= y1))
    p, v, e = pts[sel], vals[sel], eroded[sel]
    nx = max(int(math.ceil((x1 - x0) / cell)), 1)
    ny = max(int(math.ceil((y1 - y0) / cell)), 1)
    covered = (x0, y0, x0 + nx * cell, y0 + ny * cell)
    grid = np.full((ny, nx), -1, dtype=np.int16)   # -1 empty, 0..8 ramp, 9 eroded
    if not len(p):
        return grid, covered
    ix = np.clip(((p[:, 0] - x0) / cell).astype(int), 0, nx - 1)
    iy = np.clip(((p[:, 1] - y0) / cell).astype(int), 0, ny - 1)
    for k in np.argsort(v):                        # higher strain wins the cell
        if grid[iy[k], ix[k]] < ERODED_CELL:
            grid[iy[k], ix[k]] = bucket(v[k], eps_full)
    for k in np.nonzero(e)[0]:                     # erosion always wins
        grid[iy[k], ix[k]] = ERODED_CELL
    return fill_holes(grid), covered


def fill_holes(grid, rounds=2):
    """Close single-cell gaps left by centroid sampling without growing the
    outline — a cell is filled only if at least three neighbours are solid."""
    g = grid.copy()
    for _ in range(rounds):
        pad = np.full((g.shape[0] + 2, g.shape[1] + 2), -1, dtype=np.int16)
        pad[1:-1, 1:-1] = g
        nb = np.stack([pad[:-2, 1:-1], pad[2:, 1:-1], pad[1:-1, :-2], pad[1:-1, 2:]])
        filled = (nb >= 0).sum(axis=0)
        take = (g < 0) & (filled >= 3)
        if not take.any():
            break
        g = np.where(take, nb.max(axis=0), g)
    return g


def emit_grid(svg, grid, ox, oy, cw, ch):
    """Run-length merge each colour band into a single path."""
    ny, nx = grid.shape
    bands = {}
    for j in range(ny):
        row = grid[j]
        yy = oy + (ny - 1 - j) * ch          # +y is up in model space
        i = 0
        while i < nx:
            v = row[i]
            if v < 0:
                i += 1
                continue
            k = i + 1
            while k < nx and row[k] == v:
                k += 1
            bands.setdefault(int(v), []).append(
                "M%.2f %.2fh%.2fv%.2fh-%.2fz"
                % (ox + i * cw, yy, (k - i) * cw, ch, (k - i) * cw))
            i = k
    for v in sorted(bands):
        svg.path("".join(bands[v]),
                 fill=ERODED if v == ERODED_CELL else RAMP[v])


def covered_region(region, cell):
    """The region a raster of this cell size actually spans (whole cells)."""
    x0, y0, x1, y1 = region
    nx = max(int(math.ceil((x1 - x0) / cell)), 1)
    ny = max(int(math.ceil((y1 - y0) / cell)), 1)
    return (x0, y0, x0 + nx * cell, y0 + ny * cell)


def hug(box, region, max_w=None, max_h=None):
    """Shrink a panel to its content's aspect, so no panel is mostly empty."""
    x0, y0, x1, y1 = region
    aspect = (x1 - x0) / max(y1 - y0, 1e-9)
    w = min(box["w"] if max_w is None else max_w, box["w"])
    h = min(box["h"] if max_h is None else max_h, box["h"])
    if w / max(h, 1e-9) > aspect:
        w = h * aspect
    else:
        h = w / aspect
    return dict(box, w=w, h=h)


def clamp_rect(x, y, w, h, box):
    """Intersect a rectangle with a panel; returns None if it falls outside."""
    x1, y1 = min(x + w, box["x"] + box["w"]), min(y + h, box["y"] + box["h"])
    x, y = max(x, box["x"]), max(y, box["y"])
    if x1 <= x or y1 <= y:
        return None
    return x, y, x1 - x, y1 - y


def fit(box, region):
    """Uniform scale + offset placing a model-space region inside a pixel box."""
    x0, y0, x1, y1 = region
    dw, dh = max(x1 - x0, 1e-9), max(y1 - y0, 1e-9)
    sc = min(box["w"] / dw, box["h"] / dh)
    ox = box["x"] + (box["w"] - dw * sc) / 2.0
    oy = box["y"] + (box["h"] - dh * sc) / 2.0
    return sc, ox, oy


def to_px(sc, ox, oy, region, x, y):
    """Model (x, y) -> pixel, with y flipped."""
    x0, _, _, y1 = region
    return ox + (x - x0) * sc, oy + (y1 - y) * sc


def draw_field(svg, box, pts, vals, ero, region, cell, eps_full):
    grid, covered = raster(pts, vals, ero, region, cell, eps_full)
    sc, ox, oy = fit(box, covered)
    emit_grid(svg, grid, ox, oy, cell * sc, cell * sc)
    return sc, ox, oy, covered


def nice_length(span):
    """A round number about a fifth of the span, for the scale bar."""
    target = span / 5.0
    if target <= 0:
        return 1.0
    p = 10 ** math.floor(math.log10(target))
    for m in (1, 2, 5, 10):
        if m * p >= target:
            return m * p
    return 10 * p


# ---------------------------------------------------------------------------
# geometry
# ---------------------------------------------------------------------------

def view_frame(cent, axis="auto"):
    """In-plane axes (u, w) and through-thickness t for the part.

    `auto` views down the thinnest principal direction, which is the right
    normal for any plate-like part; x/y/z force a global axis instead.
    """
    origin = cent.mean(axis=0)
    rel = cent - origin
    if axis == "auto":
        _, vecs = np.linalg.eigh(np.cov(rel.T))    # ascending eigenvalues
        normal, w_ax, u_ax = vecs[:, 0], vecs[:, 1], vecs[:, 2]
    else:
        k = "xyz".index(axis)
        normal = np.eye(3)[k]
        rest = [i for i in range(3) if i != k]
        _, v2 = np.linalg.eigh(np.cov(rel[:, rest].T))
        u_ax, w_ax = np.zeros(3), np.zeros(3)
        u_ax[rest], w_ax[rest] = v2[:, 1], v2[:, 0]
    return rel @ u_ax, rel @ w_ax, rel @ normal, origin, (u_ax, w_ax, normal)


def grid_clusters(pts, mask, cell):
    """Connected components of the masked points, 8-connected on a grid."""
    idx = np.nonzero(mask)[0]
    if not len(idx):
        return []
    p = pts[idx]
    gx = np.floor(p[:, 0] / cell).astype(np.int64)
    gy = np.floor(p[:, 1] / cell).astype(np.int64)
    occupied = {}
    for k in range(len(idx)):
        occupied.setdefault((int(gx[k]), int(gy[k])), []).append(k)

    seen, comps = set(), []
    for start in occupied:
        if start in seen:
            continue
        seen.add(start)
        stack, members = [start], []
        while stack:
            cur = stack.pop()
            members.extend(occupied[cur])
            for dx in (-1, 0, 1):
                for dy in (-1, 0, 1):
                    nb = (cur[0] + dx, cur[1] + dy)
                    if nb in occupied and nb not in seen:
                        seen.add(nb)
                        stack.append(nb)
        comps.append(idx[np.array(members)])
    return comps


def cluster_boxes(pts, mask, mass, cell, limit, pad):
    """Top `limit` clusters by mass, as {members, box, mass}."""
    out = []
    for members in grid_clusters(pts, mask, cell):
        p = pts[members]
        out.append({
            "members": members,
            "mass": float(mass[members].sum()) if mass is not None else float(len(members)),
            "box": (p[:, 0].min() - pad, p[:, 1].min() - pad,
                    p[:, 0].max() + pad, p[:, 1].max() + pad),
        })
    out.sort(key=lambda d: -d["mass"])
    return out[:limit]


# ---------------------------------------------------------------------------
# spec resolution
# ---------------------------------------------------------------------------

MASS_TO_GRAM = {"Mg": 1e6, "kg": 1e3, "g": 1.0}

# Failure cards name the deletion strain differently per model; first hit wins.
FAIL_STRAIN_KEYS = ("eps_t2", "eps_f", "eps_max", "eps_s2")


def resolve_spec(build_dir, config_path, part_id, prefix=None):
    """Material density, erosion strain and labels for a part, from the spec.

    Returns a dict of whatever could be resolved — every field is optional and
    every caller has a data-driven fallback, so a run whose spec has been moved
    away still produces a figure.
    """
    info = {}
    art = (locate_artefacts(build_dir, prefix=prefix)
           if build_dir and os.path.isdir(build_dir) else {})
    pipeline = read_pipeline_json(art.get("pipeline_json"))
    cfg = read_config(config_path or art.get("config"))

    if pipeline:
        deck = pipeline.get("deck") or {}
        units = deck.get("units") or {}
        info["units"] = units
        info["mass_to_gram"] = MASS_TO_GRAM.get(units.get("mass"))
        info["length_unit"] = units.get("length")
        info["units_source"] = "pipeline json"
        info["run_name"] = deck.get("name")
        for p in deck.get("parts") or []:
            if int(p.get("id", -1)) == int(part_id):
                info["part_name"] = p.get("name")
                info["material"] = p.get("material")
                info["dim"] = p.get("dim")

    if not cfg:
        return _default_units(info)
    info.setdefault("run_name", cfg.get("name"))
    if cfg.get("units"):
        info.setdefault("units_source", "spec")
        info.setdefault("mass_to_gram",
                        MASS_TO_GRAM.get(_units_of(cfg["units"], "mass")))
        info.setdefault("length_unit", _units_of(cfg["units"], "length"))
    mats = cfg.get("materials") or {}
    name = info.get("material")
    if name not in mats and len(mats) == 1:
        # single-material spec: no ambiguity about which one this part uses
        name = next(iter(mats))
        info["material"] = name
    mat = mats.get(name)
    if not mat:
        return info

    if mat.get("density") is not None:
        info["density"] = float(mat["density"])
    fail = mat.get("failure") or {}
    for key in FAIL_STRAIN_KEYS:
        if fail.get(key) is not None:
            info["eps_erosion"] = float(fail[key])
            info["eps_erosion_from"] = "failure.%s" % key
            break
    if "eps_erosion" not in info and mat.get("eps_max") is not None:
        info["eps_erosion"] = float(mat["eps_max"])
        info["eps_erosion_from"] = "eps_max"
    if mat.get("eps_max") is not None:
        info["eps_max"] = float(mat["eps_max"])

    # shells carry their thickness on the part entry, not the material
    for entry in cfg.get("parts") or []:
        if entry.get("thickness") is not None:
            info.setdefault("thickness", float(entry["thickness"]))
    return _default_units(info)


# Unit systems are declarative in the spec; when neither the pipeline JSON nor
# the spec is at hand, fall back to the pipeline's own default rather than
# silently dropping every mass from the figure.
DEFAULT_UNITS = {"mass": "Mg", "length": "mm"}


def _units_of(system, kind):
    if isinstance(system, dict):
        return system.get(kind)
    return (UNIT_SYSTEMS.get(str(system)) or {}).get(kind)


UNIT_SYSTEMS = {
    "mm_Mg_s": {"mass": "Mg", "length": "mm"},
    "m_kg_s": {"mass": "kg", "length": "m"},
    "mm_kg_ms": {"mass": "kg", "length": "mm"},
}


def _default_units(info):
    if info.get("mass_to_gram") is None:
        info["mass_to_gram"] = MASS_TO_GRAM[DEFAULT_UNITS["mass"]]
        info["units_source"] = "assumed %s (no pipeline JSON or spec found)" % (
            "mm_Mg_s",)
    info.setdefault("length_unit", DEFAULT_UNITS["length"])
    return info


def find_npz(build_dir, prefix=None, quiet=False):
    """Locate the damage cache for this build, extracting it if absent."""
    pattern = "%s*_damage.npz" % (prefix or "")
    cached = sorted(glob.glob(os.path.join(build_dir, pattern)))
    if cached:
        return cached[0]
    art = locate_artefacts(build_dir, prefix=prefix)
    vtk_dir = art.get("vtk_dir")
    if not vtk_dir:
        sys.exit("wear_figure: no *_damage.npz and no vtk/ frames in %s — run "
                 "the simulation first, or pass the .npz directly" % build_dir)
    files = extract_damage.frame_files(vtk_dir, prefix)
    out = os.path.join(build_dir, extract_damage.run_name(files) + "_damage.npz")
    print("wear_figure: no damage cache; extracting %d frames -> %s"
          % (len(files), os.path.basename(out)), file=sys.stderr)
    data = extract_damage.track(files, None, quiet=quiet)
    np.savez_compressed(out, **data)
    return out


# ---------------------------------------------------------------------------
# annotations
# ---------------------------------------------------------------------------

def parse_annotation(spec):
    """`r=31.5:label` -> a dashed circle; `12,-4:label` -> a point label."""
    if ":" not in spec:
        sys.exit("wear_figure: --annotate wants 'r=RADIUS:label' or 'U,W:label', "
                 "got %r" % spec)
    where, label = spec.split(":", 1)
    where = where.strip()
    if where.lower().startswith("r="):
        return {"kind": "circle", "r": float(where[2:]), "label": label.strip()}
    try:
        u, w = (float(v) for v in where.split(","))
    except ValueError:
        sys.exit("wear_figure: cannot read a position from %r" % where)
    return {"kind": "point", "u": u, "w": w, "label": label.strip()}


def spin_arrow(svg, cx, cy, r, clockwise=False):
    """Rotation indicator, drawn inside the part's bore."""
    a0, a1 = math.radians(200), math.radians(70)
    if clockwise:
        a0, a1 = a1, a0
    x0, y0 = cx + r * math.cos(a0), cy - r * math.sin(a0)
    x1, y1 = cx + r * math.cos(a1), cy - r * math.sin(a1)
    svg.path("M %.1f %.1f A %.1f %.1f 0 1 %d %.1f %.1f"
             % (x0, y0, r, r, 1 if not clockwise else 0, x1, y1),
             stroke=c("accent"), sw=1.6)
    ang = a1 + (math.pi / 2 if not clockwise else -math.pi / 2)
    svg.path("M %.1f %.1f L %.1f %.1f L %.1f %.1f z" % (
        x1, y1,
        x1 - 7 * math.cos(ang - 0.4), y1 + 7 * math.sin(ang - 0.4),
        x1 - 7 * math.cos(ang + 0.4), y1 + 7 * math.sin(ang + 0.4)),
        fill=c("accent"))


# ---------------------------------------------------------------------------
# figure
# ---------------------------------------------------------------------------

W = 880
MARGIN = 24
PANEL_W = W - 2 * MARGIN


def element_size(data, m, vol, area, fallback_extent):
    """Characteristic element length, for choosing the raster resolution.

    extract_damage stores each element's longest node-to-node distance; older
    caches only have volumes, where a plain cube root understates a tet's edge
    by about a factor of two.
    """
    if "size" in getattr(data, "files", []) or "size" in data:
        sz = data["size"][m]
        sz = sz[sz > 0]
        if len(sz):
            return float(np.median(sz))
    if vol.sum() > 0:
        return 2.0 * float(np.mean(vol[vol > 0])) ** (1 / 3.0)
    if area.sum() > 0:
        return 1.4 * math.sqrt(float(np.mean(area[area > 0])))
    return max(fallback_extent, 1.0) / 100.0


def build(data, part_id, spec, opts):
    """Compute the damage statistics and draw the figure."""
    m = data["part"] == part_id
    if not m.any():
        sys.exit("wear_figure: part %s is not in the damage cache; it holds %s"
                 % (part_id, ",".join(str(int(p)) for p in np.unique(data["part"]))))

    cent = data["centroid"][m]
    eps = data["peak_eps"][m]
    vm = data["peak_vm"][m]
    ero = data["erode_frame"][m] >= 0
    et = data["erode_time"][m]
    vol, area = data["volume"][m], data["area"][m]

    # --- masses -----------------------------------------------------------
    density = opts.density if opts.density is not None else spec.get("density")
    to_gram = spec.get("mass_to_gram")
    thickness = opts.thickness or spec.get("thickness")
    if vol.sum() > 0:
        measure, measure_kind = vol, "volume"
    elif area.sum() > 0 and thickness:
        measure, measure_kind = area * thickness, "area x thickness"
    else:
        measure, measure_kind = area, "area"
    mass = None
    if density and to_gram and measure_kind != "area":
        mass = measure * density * to_gram

    # --- view frame -------------------------------------------------------
    u, w, t, origin, axes = view_frame(cent, opts.axis)
    u_ext = float(u.max() - u.min())
    w_ext = float(w.max() - w.min())
    t_ext = float(t.max() - t.min())
    plate_ratio = t_ext / w_ext if w_ext > 0 else 1.0

    # Put the damage at +u so every figure reads the same way round.  Negating
    # both in-plane axes is a 180 degree rotation, so handedness is preserved.
    focus = ero if ero.any() else (eps > np.percentile(eps, 99))
    if focus.any() and np.median(u[focus]) < 0:
        u, w = -u, -w

    elem_size = element_size(data, m, vol, area, u_ext)
    through = t_ext / elem_size if elem_size > 0 else 0.0

    # --- erosion threshold ------------------------------------------------
    eps_full = opts.eps_erosion or spec.get("eps_erosion")
    eps_from = ("given on the command line" if opts.eps_erosion
                else spec.get("eps_erosion_from"))
    if not eps_full:
        # Nothing in the spec: colour up to the strain elements actually died
        # at, or to the peak among survivors when nothing died at all.
        eps_full = (float(np.median(eps[ero])) if ero.any()
                    else float(eps.max()) or 1.0)
        eps_from = "inferred from the results"

    r = np.hypot(u, w)
    st = {
        "part": int(part_id),
        "part_name": spec.get("part_name"),
        "material": spec.get("material"),
        "elements": int(m.sum()),
        "eroded_elements": int(ero.sum()),
        "eps_erosion": float(eps_full),
        "eps_erosion_source": eps_from,
        "peak_strain_alive": float(eps[~ero].max()) if (~ero).any() else 0.0,
        "mean_strain_alive": float(eps[~ero].mean()) if (~ero).any() else 0.0,
        "peak_von_mises_alive": float(vm[~ero].max()) if (~ero).any() else 0.0,
        "units_source": spec.get("units_source"),
        "view_axis": opts.axis,
        "view_normal": [round(float(v), 4) for v in axes[2]],
        "extent": {"in_plane_long": u_ext, "in_plane_short": w_ext,
                   "through_thickness": t_ext},
        "elements_through_thickness": round(through, 1),
        "plate_ratio": round(plate_ratio, 3),
        "radius": {"min": float(r.min()), "max": float(r.max())},
    }
    if measure.sum() > 0:
        st["eroded_fraction"] = float(measure[ero].sum() / measure.sum())
    if mass is not None:
        st["mass_g"] = float(mass.sum())
        st["mass_eroded_g"] = float(mass[ero].sum())
    if ero.any():
        st["strain_at_deletion_median"] = float(np.median(eps[ero]))
        if np.isfinite(et[ero]).any():
            st["first_erosion_time"] = float(np.nanmin(et[ero]))
    if plate_ratio > 0.35:
        st["warning"] = ("part is not plate-like (through-thickness extent is "
                         "%.0f%% of the in-plane width); the projection overlaps "
                         "unrelated material and the map understates depth"
                         % (100 * plate_ratio))
        print("wear_figure: warning — %s" % st["warning"], file=sys.stderr)

    # --- clusters ---------------------------------------------------------
    pts = np.column_stack([u, w])
    cluster_mask = ero if ero.any() else (eps >= 0.5 * eps_full)
    cluster_kind = "EROSION" if ero.any() else "STRAIN"
    clusters = cluster_boxes(pts, cluster_mask, mass if mass is not None else measure,
                             cell=3.0 * elem_size, limit=opts.max_zooms,
                             pad=2.0 * elem_size)
    st["clusters"] = [{
        "elements": int(len(cl["members"])),
        ("mass_g" if mass is not None else "measure"): round(cl["mass"], 4),
        "centre": [round(float(pts[cl["members"], 0].mean()), 3),
                   round(float(pts[cl["members"], 1].mean()), 3)],
        "radius_mean": round(float(r[cl["members"]].mean()), 3),
    } for cl in clusters]

    if opts.stats_only:
        return st, None
    return st, draw(st, opts, spec, pts, u, w, t, eps, ero, mass, measure,
                    clusters, cluster_kind, eps_full, elem_size, through, r)


def draw(st, opts, spec, pts, u, w, t, eps, ero, mass, measure, clusters,
         cluster_kind, eps_full, elem_size, through, r):
    """Lay the panels out from what the data actually contains."""
    unit = spec.get("length_unit") or "mm"
    vals = np.where(ero, 0.0, eps)
    cell = max(elem_size * 0.85, max(st["extent"]["in_plane_long"], 1.0) / 700.0)

    show_section = through >= 2.0 and clusters
    n_zoom = len(clusters)

    # Panels hug their content: the region is known before anything is drawn,
    # so a stubby part gets a stubby panel instead of a mostly-empty box.
    pad = 1.5 * elem_size
    region = covered_region((u.min() - pad, w.min() - pad,
                             u.max() + pad, w.max() + pad), cell)

    # --- vertical stack ---------------------------------------------------
    y = 58
    main = hug({"x": MARGIN, "y": y, "w": PANEL_W, "h": 320.0}, region,
               max_h=320.0)
    main["h"] = max(main["h"], 130.0)
    y += main["h"] + (34 if n_zoom else 24)

    zooms = []
    if n_zoom:
        zw = (PANEL_W - 16 * (n_zoom - 1)) / n_zoom
        for i in range(n_zoom):
            box = {"x": MARGIN + i * (zw + 16), "y": y, "w": zw, "h": 210}
            if n_zoom == 1:
                x0, y0, x1, y1 = clusters[0]["box"]
                box = hug(box, (x0, y0, x1, y1) if (x1 - x0) >= (y1 - y0)
                          else (y0, x0, y1, x1))
            zooms.append(box)
        y += 210 + (34 if show_section else 24)

    section = None
    if show_section:
        section = {"x": MARGIN, "y": y, "w": PANEL_W, "h": 110}
        y += 110 + 26

    legend_y = y
    total_h = legend_y + 64

    S = Svg(W, int(total_h))
    S.rect(0, 0, W, total_h, fill=c("surface-1"))

    # --- header -----------------------------------------------------------
    title = opts.title or ("%s — wear map of part %s%s" % (
        spec.get("run_name") or "simulation", st["part"],
        " (%s)" % spec["part_name"] if spec.get("part_name") else ""))
    S.text(MARGIN, 26, title, size=14, fill=c("ink"), weight="600", family="ui")

    if st["eroded_elements"] and "mass_g" in st:
        sub = ("%d of %d elements deleted — %.2f g of %.0f g part (%.1f%%). "
               % (st["eroded_elements"], st["elements"], st["mass_eroded_g"],
                  st["mass_g"], 100 * st["mass_eroded_g"] / st["mass_g"]))
    elif st["eroded_elements"]:
        sub = ("%d of %d elements deleted (%.1f%% by volume). "
               % (st["eroded_elements"], st["elements"],
                  100 * st.get("eroded_fraction", 0.0)))
    else:
        sub = ("no elements deleted; %d elements, peak plastic strain %.4f. "
               % (st["elements"], st["peak_strain_alive"]))
    sub += ("Colour = peak plastic strain while the element was alive"
            + ("; deep red = deleted." if st["eroded_elements"] else "."))
    S.text(MARGIN, 44, sub, size=11.5, fill=c("ink-2"))

    # --- panel 1: whole part ---------------------------------------------
    S.rect(main["x"], main["y"], main["w"], main["h"], stroke=c("line"))
    sc, ox, oy, region = draw_field(S, main, pts, vals, ero, region, cell, eps_full)
    cx, cy = to_px(sc, ox, oy, region, 0.0, 0.0)

    for a in opts.annotations:
        if a["kind"] == "circle":
            S.circle(cx, cy, a["r"] * sc, dash="4 3")
            if a["label"]:
                S.text(cx + a["r"] * sc + 6, cy - a["r"] * sc - 4, a["label"],
                       size=10, fill=c("ink-3"))
        else:
            px, py = to_px(sc, ox, oy, region, a["u"], a["w"])
            S.circle(px, py, 3, fill=c("accent"), stroke="none")
            S.text(px + 6, py - 4, a["label"], size=10, fill=c("ink-3"))

    if opts.spin_rpm:
        bore = max(float(r.min()), 0.04 * st["extent"]["in_plane_long"])
        S.circle(cx, cy, bore * sc, sw=1)
        spin_arrow(S, cx, cy, bore * 0.72 * sc, clockwise=opts.spin_rpm < 0)
        S.text(cx, cy + 4, "%g rpm" % abs(opts.spin_rpm), size=10,
               fill=c("accent"), anchor="middle")

    S.text(main["x"] + 6, main["y"] + 14, view_label(opts.axis, st), size=10,
           fill=c("ink-3"))
    far_label = ("damaged end →" if st["eroded_elements"]
                 else "most-strained end →")
    S.text(main["x"] + main["w"] - 6, main["y"] + 14, far_label,
           size=10.5, fill=c("ink-2"), anchor="end")

    bar = nice_length(st["extent"]["in_plane_long"])
    by = main["y"] + main["h"] - 16
    S.line(main["x"] + 10, by, main["x"] + 10 + bar * sc, by, stroke=c("ink-3"), sw=1.5)
    S.text(main["x"] + 14 + bar * sc, by + 4, "%g %s" % (bar, unit), size=10,
           fill=c("ink-3"))

    # zoom-region outlines, keyed to the panels below by colour
    zoom_colours = [c("series-1"), c("series-3"), c("accent")]
    for i, cl in enumerate(clusters):
        x0, y0, x1, y1 = cl["box"]
        px, py = to_px(sc, ox, oy, region, x0, y1)
        # the cluster box is padded and can reach past the part, so keep the
        # outline inside its own panel rather than over the neighbouring text
        r_ = clamp_rect(px, py, (x1 - x0) * sc, (y1 - y0) * sc, main)
        if r_:
            S.rect(*r_, stroke=zoom_colours[i % len(zoom_colours)], sw=1.2,
                   dash="5 3")

    # --- zoom panels ------------------------------------------------------
    for i, (cl, box) in enumerate(zip(clusters, zooms)):
        x0, y0, x1, y1 = cl["box"]
        col = zoom_colours[i % len(zoom_colours)]
        # rotate the field a quarter turn when the cluster is taller than wide,
        # so a long thin region still fills a landscape panel
        swap = (y1 - y0) > (x1 - x0)
        label = "%s CLUSTER %d — %s, r = %.1f %s  [%s]" % (
            cluster_kind, i + 1,
            ("%.2f g" % cl["mass"]) if mass is not None else
            ("%d elements" % len(cl["members"])),
            st["clusters"][i]["radius_mean"], unit,
            "w → / u ↑" if swap else "u → / w ↑")
        S.text(box["x"], box["y"] - 10, label, size=10.5, fill=col, weight="600")
        S.rect(box["x"], box["y"], box["w"], box["h"], stroke=c("line"))

        zpts = np.column_stack([w, u]) if swap else pts
        zregion = (y0, x0, y1, x1) if swap else (x0, y0, x1, y1)
        zcell = max(cell * 0.7, (zregion[2] - zregion[0]) / 500.0)
        draw_field(S, box, zpts, vals, ero, zregion, zcell, eps_full)

    # --- section through the thickness -----------------------------------
    if section is not None:
        cl = clusters[0]
        wlo, whi = cl["box"][1], cl["box"][3]
        band = (w >= wlo) & (w <= whi)
        S.text(MARGIN, section["y"] - 10,
               "SECTION — through the thickness (%.2f %s, ~%.0f elements) at the "
               "cluster 1 band" % (st["extent"]["through_thickness"], unit, through),
               size=10.5, fill=c("series-1"), weight="600")
        spts = np.column_stack([u[band], t[band]])
        scell = max(cell * 0.7, (cl["box"][2] - cl["box"][0]) / 600.0)
        sregion = (cl["box"][0], t.min() - elem_size * 0.3,
                   cl["box"][2], t.max() + elem_size * 0.3)
        # a section is a thin strip; shrink the box to it rather than leaving
        # most of a full-width panel empty
        aspect = (sregion[2] - sregion[0]) / max(sregion[3] - sregion[1], 1e-9)
        section["w"] = min(section["w"], section["h"] * aspect + 8)
        S.rect(section["x"], section["y"], section["w"], section["h"],
               stroke=c("line"))
        draw_field(S, section, spts, vals[band], ero[band], sregion, scell, eps_full)
        S.text(section["x"] + 6, section["y"] + 14, "through-thickness ↑",
               size=9.5, fill=c("ink-3"))
        S.text(section["x"] + section["w"] - 6, section["y"] + section["h"] - 6,
               far_label, size=9.5, fill=c("ink-3"), anchor="end")

    # --- legend -----------------------------------------------------------
    ly = legend_y
    S.text(MARGIN, ly - 6, "peak plastic strain (element still alive)", size=10,
           fill=c("ink-3"))
    sw_ = 20
    for i, col in enumerate(RAMP):
        S.rect(MARGIN + i * sw_, ly, sw_, 12, fill=col)
    ramp_w = len(RAMP) * sw_
    S.text(MARGIN, ly + 24, "0", size=9.5, fill=c("ink-3"))
    S.text(MARGIN + ramp_w, ly + 24, "≥ %.3g" % eps_full, size=9.5,
           fill=c("ink-3"), anchor="end")
    S.text(MARGIN, ly + 40, "threshold: %s" % (st["eps_erosion_source"] or "given"),
           size=9.5, fill=c("ink-3"))
    if st["eroded_elements"]:
        S.rect(MARGIN + ramp_w + 26, ly, 16, 12, fill=ERODED)
        S.text(MARGIN + ramp_w + 48, ly + 10,
               "deleted, shown at its undeformed position", size=10, fill=c("ink-2"))

    facts = []
    for i, cl in enumerate(st["clusters"]):
        facts.append("cluster %d: %d elements%s at r = %.1f %s"
                     % (i + 1, cl["elements"],
                        ", %.2f g" % cl["mass_g"] if "mass_g" in cl else "",
                        cl["radius_mean"], unit))
    if "first_erosion_time" in st:
        facts.append("first deletion at t = %.3g s; survivors peak εp = %.4f"
                     % (st["first_erosion_time"], st["peak_strain_alive"]))
    else:
        facts.append("survivors peak εp = %.4f, mean %.4f"
                     % (st["peak_strain_alive"], st["mean_strain_alive"]))
    if "warning" in st:
        facts.append("warning: projection overlaps material (not plate-like)")
    for i, f in enumerate(facts[:4]):
        S.text(W / 2 + 40, ly + 4 + i * 15, f, size=10.5, fill=c("ink-2"))

    aria = ("Damage map of part %s viewed down its %s axis. %s"
            % (st["part"], opts.axis, sub_aria(st)))
    return S.out(aria, style=not opts.embed)


def view_label(axis, st):
    if axis == "auto":
        n = st["view_normal"]
        return ("viewed down the thinnest axis (%.2f, %.2f, %.2f)"
                % (n[0], n[1], n[2]))
    return "viewed down %s" % axis


def sub_aria(st):
    if st["eroded_elements"]:
        return ("%d of %d elements were deleted, in %d cluster(s); the peak "
                "plastic strain among surviving elements is %.4f."
                % (st["eroded_elements"], st["elements"], len(st["clusters"]),
                   st["peak_strain_alive"]))
    return ("No elements were deleted; the peak plastic strain is %.4f over "
            "%d elements." % (st["peak_strain_alive"], st["elements"]))


# ---------------------------------------------------------------------------

def main(argv=None):
    p = argparse.ArgumentParser(
        prog="wear_figure.py",
        description="Draw a wear / damage map of one part as a standalone SVG")
    p.add_argument("target", help="build directory, or a *_damage.npz")
    p.add_argument("--part", "-p", type=int, required=True, help="part id to map")
    p.add_argument("--config", "-c", help="simulation spec YAML "
                                          "(auto-detected in the build dir)")
    p.add_argument("--out", "-o", help="output SVG "
                                       "(default: <build>/<name>_part<N>_wear.svg)")
    p.add_argument("--axis", choices=["auto", "x", "y", "z"], default="auto",
                   help="view down this axis; auto = the part's thinnest "
                        "principal direction (default)")
    p.add_argument("--title", help="figure title")
    p.add_argument("--annotate", action="append", default=[], metavar="SPEC",
                   help="'r=RADIUS:label' for a bolt/bore circle, or 'U,W:label' "
                        "for a point callout; repeatable")
    p.add_argument("--spin-rpm", type=float,
                   help="draw a rotation indicator at this rate "
                        "(negative = clockwise)")
    p.add_argument("--max-zooms", type=int, default=2,
                   help="how many damage clusters get a zoom panel (default: 2)")
    p.add_argument("--eps-erosion", type=float,
                   help="strain the colour ramp saturates at "
                        "(default: the spec's failure strain)")
    p.add_argument("--density", type=float,
                   help="override the material density, in the run's units")
    p.add_argument("--thickness", type=float,
                   help="shell thickness, for mass accounting on 2D parts")
    p.add_argument("--embed", action="store_true",
                   help="omit the theme <style> block so the figure inherits "
                        "the colours of the page it is embedded in")
    p.add_argument("--stats-only", action="store_true",
                   help="print the statistics without drawing")
    p.add_argument("--prefix", help="only read frames whose basename starts with this")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="suppress extraction progress on stderr")
    args = p.parse_args(argv)
    args.annotations = [parse_annotation(a) for a in args.annotate]

    target = args.target
    if os.path.isdir(target):
        build_dir = target
        npz = find_npz(build_dir, args.prefix, args.quiet)
    elif os.path.isfile(target):
        npz = target
        build_dir = os.path.dirname(os.path.abspath(target))
        # a build directory can hold several runs; the cache filename says
        # which one this is, so use it to pick the matching spec and deck
        if not args.prefix:
            base = os.path.basename(target)
            if base.endswith("_damage.npz"):
                args.prefix = base[:-len("_damage.npz")]
    else:
        sys.exit(f"wear_figure: no such build directory or .npz: {target}")

    data = np.load(npz)
    spec = resolve_spec(build_dir, args.config, args.part, args.prefix)
    st, svg = build(data, args.part, spec, args)
    st["damage_npz"] = os.path.abspath(npz)

    if svg is not None:
        out = args.out or os.path.join(
            build_dir, "%s_part%d_wear.svg"
            % (spec.get("run_name") or "run", args.part))
        os.makedirs(os.path.dirname(os.path.abspath(out)), exist_ok=True)
        with open(out, "w") as fh:
            fh.write(svg + "\n")
        st["svg"] = os.path.abspath(out)

    print(json.dumps(st, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
