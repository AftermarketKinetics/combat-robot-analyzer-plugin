#!/usr/bin/env python3
"""Per-element damage history across every VTK frame of a solved run.

Walks the animation frames once and records, for each element of the selected
part(s): the peak plastic strain and von Mises stress reached *while the element
was still alive*, and the frame/time at which it eroded.  Centroids and element
volumes are taken from the first frame (undeformed), so downstream maps read as
"where material was lost from the part", not where the debris ended up.

The result is cached in a compressed .npz so a figure can be re-drawn in a
second without re-parsing gigabytes of VTK.

    extract_damage.py BUILD/vtk --part 4                 # -> BUILD/<name>_damage.npz
    extract_damage.py BUILD/vtk --part 4,5 -o dmg.npz
    extract_damage.py BUILD/vtk                          # every part

Prints a JSON summary on stdout; per-frame progress goes to stderr.
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

import numpy as np

from extract_results import (resolve_damage_field, resolve_element_status,
                             resolve_strain_field, resolve_stress_field)


# ---------------------------------------------------------------------------
# Fast VTK reader
# ---------------------------------------------------------------------------
#
# extract_results.read_vtk() is a general line-by-line parser; walking twenty
# frames of a half-million-element model with it takes minutes.  This reader
# slices the file with regexes and hands the numeric blocks straight to numpy,
# which is seconds.  The duplication is deliberate — do not "unify" them.

# Cell faces by VTK cell type, wound consistently within each cell.  Volumes
# are taken as |sum of signed tetrahedra|, so a globally flipped winding is
# harmless; only per-cell consistency matters.
VTK_TETRA, VTK_HEXAHEDRON, VTK_WEDGE, VTK_PYRAMID = 10, 12, 13, 14
VTK_TRIANGLE, VTK_QUAD = 5, 9

CELL_FACES = {
    VTK_TETRA: [(0, 2, 1), (0, 1, 3), (1, 2, 3), (0, 3, 2)],
    VTK_HEXAHEDRON: [(0, 3, 2, 1), (4, 5, 6, 7), (0, 1, 5, 4),
                     (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)],
    VTK_WEDGE: [(0, 2, 1), (3, 4, 5), (0, 1, 4, 3), (1, 2, 5, 4), (2, 0, 3, 5)],
    VTK_PYRAMID: [(0, 3, 2, 1), (0, 1, 4), (1, 2, 4), (2, 3, 4), (3, 0, 4)],
}
CELL_SURFACE = {VTK_TRIANGLE: [(0, 1, 2)], VTK_QUAD: [(0, 1, 2), (0, 2, 3)]}

# Cell scalars worth carrying.  PART_ID and ELEMENT_ID are named the same for
# every element type; the rest vary by prefix and are resolved from the file.
_ALWAYS = ("PART_ID", "ELEMENT_ID", "EROSION_STATUS")


def _numeric_block(txt, start, end):
    return np.fromstring(txt[start:end], sep=" ")


def _next_section(txt, start):
    """Offset of the next VTK section header after `start`, or len(txt)."""
    best = len(txt)
    for tag in ("\nSCALARS", "\nVECTORS", "\nTENSORS", "\nFIELD",
                "\nPOINT_DATA", "\nCELL_DATA", "\nLOOKUP_TABLE"):
        i = txt.find(tag, start)
        if 0 <= i < best:
            best = i
    return best


def scalar_names(txt):
    """Every SCALARS field present, without parsing any of the data."""
    return re.findall(r"^SCALARS (\S+) ", txt, re.M)


def resolve_fields(names):
    """Map our internal field roles onto this file's actual scalar names."""
    stub = {n: True for n in names}
    strain, _ = resolve_strain_field(stub)
    stress, _ = resolve_stress_field(stub)
    damage, _ = resolve_damage_field(stub)
    status, _ = resolve_element_status(stub)
    # OpenRadioss writes EROSION_STATUS for /FAIL models and Element_status for
    # ELEM/OFF; either one answers "is this element still alive".
    alive = "EROSION_STATUS" if "EROSION_STATUS" in stub else status
    return {"strain": strain, "stress": stress, "damage": damage,
            "alive": alive}


def read_frame(path, want=None, geometry=False):
    """Parse one ASCII VTK frame.

    `want` limits which SCALARS blocks are parsed (None = the standard set).
    `geometry` additionally returns points, cell connectivity and cell types.
    """
    with open(path) as fh:
        txt = fh.read()

    out = {}
    m = re.search(r"^TIME 1 1 double\n([-\d.eE+]+)", txt, re.M)
    out["time"] = float(m.group(1)) if m else None

    names = scalar_names(txt)
    out["fields"] = resolve_fields(names)
    if want is None:
        want = set(_ALWAYS) | {v for v in out["fields"].values() if v}

    if geometry:
        m = re.search(r"^POINTS (\d+) float$", txt, re.M)
        if not m:
            sys.exit(f"extract_damage: {path} has no POINTS block")
        npts = int(m.group(1))
        s = m.end() + 1
        out["points"] = _numeric_block(txt, s, txt.index("\nCELLS ", s)).reshape(npts, 3)

        m = re.search(r"^CELLS (\d+) (\d+)$", txt, re.M)
        ncells = int(m.group(1))
        s = m.end() + 1
        flat = _numeric_block(txt, s, txt.index("\nCELL_TYPES", s)).astype(np.int64)
        out["cells"] = _split_cells(flat, ncells)

        m = re.search(r"^CELL_TYPES (\d+)$", txt, re.M)
        s = m.end() + 1
        out["cell_types"] = _numeric_block(
            txt, s, _next_section(txt, s)).astype(np.int32)[:ncells]

    for name in want:
        if not name:
            continue
        m = re.search(r"^SCALARS %s \S+.*\nLOOKUP_TABLE \S+$" % re.escape(name),
                      txt, re.M)
        if not m:
            continue
        s = m.end() + 1
        out[name] = _numeric_block(txt, s, _next_section(txt, s))
    return out


def _split_cells(flat, ncells):
    """Connectivity, as an (ncells, k) array when every cell has the same node
    count — which is the usual case and keeps the geometry maths vectorised —
    and as a list of per-cell arrays otherwise."""
    if ncells == 0:
        return np.zeros((0, 0), dtype=np.int64)
    k = int(flat[0])
    if len(flat) == ncells * (k + 1) and np.all(flat[0::k + 1] == k):
        return flat.reshape(ncells, k + 1)[:, 1:]
    cells, i = [], 0
    for _ in range(ncells):
        n = int(flat[i])
        cells.append(flat[i + 1:i + 1 + n])
        i += 1 + n
    return cells


def subset_cells(cells, idx):
    """Select cells by index, preserving the array/list distinction."""
    if isinstance(cells, np.ndarray):
        return cells[idx]
    return [cells[i] for i in idx]


def stack_cells(cells, idx):
    """(m, k) connectivity for a set of same-node-count cells."""
    if isinstance(cells, np.ndarray):
        return cells[idx]
    return np.array([cells[i] for i in idx], dtype=np.int64)


# ---------------------------------------------------------------------------
# Geometry
# ---------------------------------------------------------------------------

def cell_measures(points, cells, cell_types):
    """Per-cell (volume, area, centroid, size).

    Volume is |sum of signed tetrahedra| over the cell's triangulated boundary,
    each face fanned from its own centroid, which is exact for tets and wedges
    and correct for non-planar hex faces too.  2D elements get an area instead
    and a volume of zero.  Vectorised per cell type — a Python loop over the
    elements of a real model is minutes, not seconds.

    `size` is the longest node-to-node distance in the element.  It is the
    honest characteristic length: a cube root of volume underestimates a tet's
    edge by a factor of two, which is enough to make a downstream raster full
    of holes.
    """
    n = len(cells) if not isinstance(cells, np.ndarray) else cells.shape[0]
    vol = np.zeros(n)
    area = np.zeros(n)
    size = np.zeros(n)
    cent = np.zeros((n, 3))
    ct = np.asarray(cell_types, dtype=np.int32)

    for t in np.unique(ct):
        idx = np.nonzero(ct == t)[0]
        P = points[stack_cells(cells, idx)]          # (m, k, 3)
        cent[idx] = P.mean(axis=1)

        k = P.shape[1]
        longest = np.zeros(len(idx))
        for a in range(k):
            for b in range(a + 1, k):
                longest = np.maximum(
                    longest, np.linalg.norm(P[:, a, :] - P[:, b, :], axis=1))
        size[idx] = longest

        faces = CELL_FACES.get(int(t))
        if faces is not None:
            v = np.zeros(len(idx))
            for f in faces:
                fp = P[:, list(f), :]
                fc = fp.mean(axis=1)                 # face centroid, (m, 3)
                for j in range(len(f)):
                    a = fp[:, j, :]
                    b = fp[:, (j + 1) % len(f), :]
                    v += np.einsum("ij,ij->i", fc, np.cross(a, b))
            vol[idx] = np.abs(v) / 6.0
            continue

        tris = CELL_SURFACE.get(int(t))
        if tris is not None:
            a = np.zeros(len(idx))
            for tri in tris:
                q = P[:, list(tri), :]
                a += np.linalg.norm(np.cross(q[:, 1] - q[:, 0],
                                             q[:, 2] - q[:, 0]), axis=1) / 2.0
            area[idx] = a

    return vol, area, size, cent


# ---------------------------------------------------------------------------
# Frame walk
# ---------------------------------------------------------------------------

def frame_files(vtk_dir, prefix=None):
    if os.path.isdir(vtk_dir):
        files = sorted(glob.glob(os.path.join(vtk_dir, "*.vtk")))
    else:
        files = [vtk_dir]
    if prefix:
        files = [f for f in files if os.path.basename(f).startswith(prefix)]
    return files


def run_name(files):
    """Strip the trailing frame counter OpenRadioss appends (…A001.vtk)."""
    base = os.path.splitext(os.path.basename(files[0]))[0]
    return re.sub(r"A\d+$", "", base) or base


def track(files, parts=None, quiet=False):
    """Walk every frame, returning the per-element damage arrays."""
    f0 = read_frame(files[0], geometry=True)
    if "PART_ID" not in f0:
        sys.exit(f"extract_damage: {files[0]} has no PART_ID field — "
                 "is this an OpenRadioss animation frame?")
    pid = f0["PART_ID"].astype(int)

    if parts:
        sel = np.isin(pid, list(parts))
        if not sel.any():
            sys.exit("extract_damage: no elements on part(s) %s; this run has %s"
                     % (",".join(str(p) for p in parts),
                        ",".join(str(p) for p in np.unique(pid))))
    else:
        sel = np.ones(len(pid), dtype=bool)

    idx = np.nonzero(sel)[0]
    cells = subset_cells(f0["cells"], idx)
    ctypes = f0["cell_types"][idx]
    vol, area, size, cent = cell_measures(f0["points"], cells, ctypes)
    n = len(idx)

    eid = f0["ELEMENT_ID"][idx].astype(np.int64) if "ELEMENT_ID" in f0 else None
    fields = f0["fields"]
    if not fields["strain"]:
        sys.exit("extract_damage: no plastic strain field in the frames — "
                 "nothing to map")

    if not fields["alive"]:
        print("extract_damage: warning — no element-status field in the frames; "
              "erosion cannot be tracked (add /ANIM/ELEM/OFF to the engine deck)",
              file=sys.stderr)

    peak_eps = np.zeros(n)
    peak_vm = np.zeros(n)
    peak_dam = np.zeros(n)
    erode_frame = np.full(n, -1, dtype=np.int32)
    erode_time = np.full(n, np.nan)
    # Elements already inactive at the first frame were never alive to lose:
    # OpenRadioss reports elements inside a rigid body (/RBODY) as status 0
    # from t = 0. Counting them as eroded made a rigid tooth read as 100 %
    # eroded and a rigidified slab of armour as 5,700 lost elements (v2,
    # 2026-10-08). They are flagged `rigid` instead.
    rigid = np.zeros(n, dtype=bool)
    times = []

    for i, path in enumerate(files):
        fr = read_frame(path)
        times.append(fr["time"])

        # Frames normally keep the same cell ordering; if the writer drops
        # deleted elements, fall back to matching on ELEMENT_ID.
        if len(fr["PART_ID"]) == len(pid):
            take = idx
            present = np.ones(n, dtype=bool)
        elif eid is not None and "ELEMENT_ID" in fr:
            here = fr["ELEMENT_ID"].astype(np.int64)
            order = np.argsort(here)
            pos = np.searchsorted(here, eid, sorter=order)
            pos = np.clip(pos, 0, len(here) - 1)
            take = order[pos]
            present = here[take] == eid
        else:
            sys.exit("extract_damage: frame %s has %d elements but frame 1 had "
                     "%d, and there is no ELEMENT_ID to match on"
                     % (os.path.basename(path), len(fr["PART_ID"]), len(pid)))

        alive = present.copy()
        if fr["fields"]["alive"] and fr["fields"]["alive"] in fr:
            alive &= fr[fr["fields"]["alive"]][take] > 0.5

        eps = fr[fr["fields"]["strain"]][take]
        peak_eps = np.where(alive, np.maximum(peak_eps, eps), peak_eps)
        if fr["fields"]["stress"] and fr["fields"]["stress"] in fr:
            vm = fr[fr["fields"]["stress"]][take]
            peak_vm = np.where(alive, np.maximum(peak_vm, vm), peak_vm)
        if fr["fields"]["damage"] and fr["fields"]["damage"] in fr:
            dm = fr[fr["fields"]["damage"]][take]
            peak_dam = np.where(alive, np.maximum(peak_dam, dm), peak_dam)

        if i == 0:
            rigid = ~alive & present
        newly = (~alive) & (erode_frame < 0) & ~rigid
        erode_frame[newly] = i
        erode_time[newly] = fr["time"] if fr["time"] is not None else np.nan
        if not quiet:
            print("  frame %3d/%d  t=%s  eroded %d/%d"
                  % (i + 1, len(files),
                     "%.4e" % fr["time"] if fr["time"] is not None else "?",
                     int((erode_frame >= 0).sum()), n), file=sys.stderr)

    return dict(centroid=cent, part=pid[idx], element_id=(eid if eid is not None
                                                          else np.arange(n)),
                peak_eps=peak_eps, peak_vm=peak_vm, peak_damage=peak_dam,
                erode_frame=erode_frame, erode_time=erode_time,
                volume=vol, area=area, size=size, cell_type=ctypes,
                erosion_tracked=np.array(bool(fields["alive"])),
                rigid=rigid,
                times=np.array([t if t is not None else np.nan for t in times],
                               dtype=float))


def summarise(data, npz_path, files):
    ero = data["erode_frame"] >= 0
    rigid = data["rigid"] if "rigid" in data else np.zeros(len(ero), dtype=bool)
    vol, area = data["volume"], data["area"]
    # Rigid elements neither deform nor erode: fractions are of deformable material.
    measure = np.where(rigid, 0.0, vol if vol.sum() > 0 else area)
    out = {
        "npz": os.path.abspath(npz_path),
        "frames": len(files),
        "elements": int(len(ero)),
        "eroded_elements": int(ero.sum()),
        "rigid_elements": int(rigid.sum()),
        "parts": [int(p) for p in np.unique(data["part"])],
        "peak_plastic_strain_alive": float(data["peak_eps"][~ero].max()) if (~ero).any() else 0.0,
        "peak_von_mises_alive": float(data["peak_vm"][~ero].max()) if (~ero).any() else 0.0,
        "total_volume": float(vol.sum()),
        "total_area": float(area.sum()),
        "element_size_median": float(np.median(data["size"])),
        "erosion_tracked": bool(data["erosion_tracked"]),
    }
    if measure.sum() > 0:
        out["eroded_fraction"] = float(measure[ero].sum() / measure.sum())
    if ero.any():
        t = data["erode_time"][ero]
        if np.isfinite(t).any():
            out["first_erosion_time"] = float(np.nanmin(t))
            out["last_erosion_time"] = float(np.nanmax(t))
        out["strain_at_deletion_median"] = float(np.median(data["peak_eps"][ero]))
    times = data["times"]
    if np.isfinite(times).any():
        out["end_time"] = float(np.nanmax(times))
    return out


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="extract_damage.py",
        description="Track per-element peak strain and erosion across VTK frames")
    p.add_argument("vtk_dir", help="directory of VTK frames (or a build directory)")
    p.add_argument("--part", "-p", help="part id, or comma-separated list "
                                        "(default: every part)")
    p.add_argument("--prefix", help="only read frames whose basename starts with this")
    p.add_argument("--out", "-o", help="output .npz "
                                       "(default: <build>/<name>_damage.npz)")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="suppress the per-frame progress on stderr")
    args = p.parse_args(argv)

    vtk_dir = args.vtk_dir
    if os.path.isdir(os.path.join(vtk_dir, "vtk")):
        vtk_dir = os.path.join(vtk_dir, "vtk")
    files = frame_files(vtk_dir, args.prefix)
    if not files:
        sys.exit(f"extract_damage: no .vtk frames under {args.vtk_dir}")

    parts = None
    if args.part and args.part != "*":
        parts = [int(x) for x in str(args.part).replace(",", " ").split()]

    out_path = args.out
    if not out_path:
        base = os.path.dirname(os.path.abspath(vtk_dir))
        out_path = os.path.join(base, run_name(files) + "_damage.npz")

    data = track(files, parts, quiet=args.quiet)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    np.savez_compressed(out_path, **data)

    print(json.dumps(summarise(data, out_path, files), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
