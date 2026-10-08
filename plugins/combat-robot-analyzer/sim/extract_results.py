#!/usr/bin/env python3
"""Extract per-element and per-node results from OpenRadioss VTK output.

Reads the ASCII VTK files produced by anim_to_vtk and prints frame-by-frame
summaries, spatial distributions, and hotspot locations.  Usable as both a
CLI tool and an importable module.

    # frame-by-frame max plastic strain on part 1
    extract_results.py build/vtk --part 1

    # hotspots and spatial distribution at the last frame
    extract_results.py build/vtk --hotspots 6 --spatial --radius-band 5

    # JSON output for programmatic consumption
    extract_results.py build/vtk --json
"""

from __future__ import annotations

import argparse
import collections
import glob
import json
import math
import os
import sys


# ---------------------------------------------------------------------------
# VTK reader
# ---------------------------------------------------------------------------

def read_vtk(path):
    """Parse an ASCII VTK unstructured grid file.

    Returns (points, cells, cell_types, point_data, cell_data, field_data)
    where:
      points      — list of (x, y, z)
      cells       — list of [node_idx, …]
      cell_types  — list of int (VTK cell types)
      point_data  — {name: list-of-values-or-tuples}
      cell_data   — {name: list-of-values-or-tuples}
      field_data  — {name: value}  (TIME, CYCLE, …)
    """
    with open(path) as fh:
        lines = fh.readlines()

    idx = 0
    n = len(lines)

    def advance():
        nonlocal idx
        while idx < n and not lines[idx].strip():
            idx += 1

    def peek():
        advance()
        return lines[idx].strip() if idx < n else ""

    # Skip header (lines 0–3)
    idx = 4
    advance()

    field_data = {}
    points = []
    cells = []
    cell_types = []
    point_data = {}
    cell_data = {}

    while idx < n:
        advance()
        if idx >= n:
            break
        line = lines[idx].strip()
        toks = line.split()
        if not toks:
            idx += 1
            continue

        keyword = toks[0]

        if keyword == "FIELD" and "FieldData" in line:
            nf = int(toks[2])
            idx += 1
            for _ in range(nf):
                advance()
                hdr = lines[idx].split()
                fname, _nc, nt, _dt = hdr[0], int(hdr[1]), int(hdr[2]), hdr[3]
                idx += 1
                vals = []
                while len(vals) < nt:
                    advance()
                    vals.extend(lines[idx].split())
                    idx += 1
                field_data[fname] = float(vals[0]) if nt == 1 else [float(v) for v in vals[:nt]]
            continue

        if keyword == "POINTS":
            np_ = int(toks[1])
            idx += 1
            raw = []
            while len(raw) < np_ * 3:
                raw.extend(lines[idx].split())
                idx += 1
            for i in range(np_):
                points.append((float(raw[i * 3]), float(raw[i * 3 + 1]),
                                float(raw[i * 3 + 2])))
            continue

        if keyword == "CELLS":
            nc = int(toks[1])
            idx += 1
            for _ in range(nc):
                row = lines[idx].split()
                idx += 1
                npe = int(row[0])
                cells.append([int(v) for v in row[1:1 + npe]])
            continue

        if keyword == "CELL_TYPES":
            nc = int(toks[1])
            idx += 1
            while len(cell_types) < nc:
                cell_types.extend(int(v) for v in lines[idx].split())
                idx += 1
            continue

        if keyword in ("POINT_DATA", "CELL_DATA"):
            target = point_data if keyword == "POINT_DATA" else cell_data
            count = int(toks[1])
            idx += 1

            while idx < n:
                advance()
                if idx >= n:
                    break
                hdr = lines[idx].strip().split()
                if not hdr:
                    idx += 1
                    continue
                if hdr[0] in ("POINT_DATA", "CELL_DATA", "FIELD", "POINTS",
                              "CELLS", "CELL_TYPES"):
                    break  # start of next section

                if hdr[0] == "SCALARS":
                    sname = hdr[1]
                    dtype = hdr[2] if len(hdr) > 2 else "float"
                    idx += 1
                    # skip LOOKUP_TABLE line
                    advance()
                    if lines[idx].strip().startswith("LOOKUP_TABLE"):
                        idx += 1
                    vals = []
                    while len(vals) < count:
                        vals.extend(lines[idx].split())
                        idx += 1
                    conv = int if dtype == "int" else float
                    target[sname] = [conv(v) for v in vals[:count]]

                elif hdr[0] == "VECTORS":
                    vname = hdr[1]
                    idx += 1
                    vals = []
                    while len(vals) < count * 3:
                        vals.extend(lines[idx].split())
                        idx += 1
                    target[vname] = [(float(vals[i * 3]), float(vals[i * 3 + 1]),
                                      float(vals[i * 3 + 2]))
                                     for i in range(count)]
                else:
                    idx += 1
            continue

        idx += 1

    return points, cells, cell_types, point_data, cell_data, field_data


# ---------------------------------------------------------------------------
# analysis helpers
# ---------------------------------------------------------------------------

def cell_centroid(pts, cell):
    """Mean of node positions — works for tets, quads, tris."""
    cx = sum(pts[j][0] for j in cell) / len(cell)
    cy = sum(pts[j][1] for j in cell) / len(cell)
    cz = sum(pts[j][2] for j in cell) / len(cell)
    return cx, cy, cz


def resolve_strain_field(cell_data):
    """Find the plastic strain field regardless of element type prefix."""
    for name in ("3DELEM_Plastic_Strain", "2DELEM_Plastic_Strain",
                  "Plastic_Strain"):
        if name in cell_data:
            return name, cell_data[name]
    return None, None


def resolve_stress_field(cell_data):
    """Find the von Mises stress field regardless of element type prefix."""
    for name in ("3DELEM_Von_Mises", "2DELEM_Von_Mises", "Von_Mises"):
        if name in cell_data:
            return name, cell_data[name]
    return None, None


def resolve_damage_field(cell_data):
    """Find the damage scalar field (from /FAIL models via ELEM/DAM1)."""
    for name in ("3DELEM_Damage_1", "2DELEM_Damage_1", "Damage_1"):
        if name in cell_data:
            return name, cell_data[name]
    return None, None


def resolve_element_status(cell_data):
    """Find the element status field (from ELEM/OFF — 0=deleted, 1=active)."""
    for name in ("3DELEM_Element_status", "2DELEM_Element_status",
                  "Element_status"):
        if name in cell_data:
            return name, cell_data[name]
    return None, None


def filter_elements(cell_data, part_filter):
    """Return indices of elements matching the part filter.

    part_filter can be:
      - None or "*"  → all elements
      - an int        → match PART_ID == int
      - a list of int → match PART_ID in list
    """
    if part_filter is None or part_filter == "*":
        pid = cell_data.get("PART_ID")
        return list(range(len(pid))) if pid else []

    pids = cell_data.get("PART_ID")
    if not pids:
        return []

    if isinstance(part_filter, (int, float)):
        part_filter = [int(part_filter)]
    else:
        part_filter = [int(p) for p in part_filter]

    return [i for i, p in enumerate(pids) if int(p) in part_filter]


def frame_summary(path, part_filter=None, thresholds=None):
    """Compute a summary dict for one VTK frame."""
    pts, cells, ct, pd, cd, fd = read_vtk(path)

    _, strain = resolve_strain_field(cd)
    _, stress = resolve_stress_field(cd)

    indices = filter_elements(cd, part_filter)
    if not indices or not strain:
        return None

    eps = [strain[i] for i in indices]
    vm = [stress[i] for i in indices] if stress else []

    summary = {
        "file": os.path.basename(path),
        "time": fd.get("TIME"),
        "cycle": fd.get("CYCLE"),
        "elements": len(indices),
        "max_plastic_strain": max(eps),
        "mean_plastic_strain": sum(eps) / len(eps),
    }
    if vm:
        summary["max_von_mises"] = max(vm)
        summary["mean_von_mises"] = sum(vm) / len(vm)

    if thresholds:
        summary["above_threshold"] = {}
        for t in thresholds:
            summary["above_threshold"][str(t)] = sum(1 for e in eps if e > t)

    # Velocity magnitude on nodes belonging to selected elements
    vel = pd.get("Velocity")
    disp = pd.get("Displacement")
    if vel:
        node_set = set()
        for i in indices:
            node_set.update(cells[i])
        vmag = [math.sqrt(vel[j][0]**2 + vel[j][1]**2 + vel[j][2]**2)
                for j in node_set]
        if vmag:
            summary["max_velocity"] = max(vmag)

    if disp:
        node_set = set()
        for i in indices:
            node_set.update(cells[i])
        dmag = [math.sqrt(disp[j][0]**2 + disp[j][1]**2 + disp[j][2]**2)
                for j in node_set]
        if dmag:
            summary["max_displacement"] = max(dmag)

    # Damage and erosion fields (present when /FAIL models are active)
    _, damage = resolve_damage_field(cd)
    _, status = resolve_element_status(cd)
    erosion = cd.get("EROSION_STATUS")

    if damage:
        dam_vals = [damage[i] for i in indices]
        max_dam = max(dam_vals)
        if max_dam > 0:
            summary["max_damage"] = max_dam
            summary["mean_damage"] = sum(dam_vals) / len(dam_vals)
            summary["damaged_elements"] = sum(1 for d in dam_vals if d > 0)

    if status:
        eroded = sum(1 for i in indices if float(status[i]) < 0.5)
        if eroded > 0:
            summary["eroded_elements"] = eroded

    if erosion:
        eroded = sum(1 for i in indices if int(erosion[i]) != 0)
        if eroded > 0:
            summary["eroded_elements"] = eroded  # overrides status count

    return summary


def hotspot_analysis(path, part_filter=None, top_n=6):
    """Find the N elements with the highest plastic strain."""
    pts, cells, ct, pd, cd, fd = read_vtk(path)
    _, strain = resolve_strain_field(cd)
    _, stress = resolve_stress_field(cd)
    _, damage = resolve_damage_field(cd)
    _, status = resolve_element_status(cd)
    if not strain:
        return []

    indices = filter_elements(cd, part_filter)
    ranked = sorted(indices, key=lambda i: -strain[i])[:top_n]

    hotspots = []
    for i in ranked:
        cx, cy, cz = cell_centroid(pts, cells[i])
        entry = {
            "element_index": i,
            "plastic_strain": strain[i],
            "centroid": [round(cx, 2), round(cy, 2), round(cz, 2)],
            "radius": round(math.sqrt(cx**2 + cy**2), 1),
        }
        if stress:
            entry["von_mises"] = stress[i]
        if damage:
            entry["damage"] = damage[i]
        if status:
            entry["active"] = float(status[i]) >= 0.5
        pid = cd.get("PART_ID")
        if pid:
            entry["part_id"] = int(pid[i])
        hotspots.append(entry)
    return hotspots


def spatial_distribution(path, part_filter=None, strain_threshold=0.02,
                          band_size=10, axis="r"):
    """Bin yielded elements (above threshold) by spatial coordinate.

    axis: "r" (radial distance in xy), "x", "y", "z"
    """
    pts, cells, ct, pd, cd, fd = read_vtk(path)
    _, strain = resolve_strain_field(cd)
    if not strain:
        return {}

    indices = filter_elements(cd, part_filter)
    bands = collections.Counter()
    total_above = 0

    for i in indices:
        if strain[i] <= strain_threshold:
            continue
        total_above += 1
        cx, cy, cz = cell_centroid(pts, cells[i])
        if axis == "r":
            val = math.sqrt(cx**2 + cy**2)
        elif axis == "x":
            val = cx
        elif axis == "y":
            val = cy
        elif axis == "z":
            val = cz
        else:
            val = math.sqrt(cx**2 + cy**2)
        band_key = int(val // band_size * band_size)
        bands[band_key] += 1

    return {
        "axis": axis,
        "threshold": strain_threshold,
        "band_size": band_size,
        "total_above": total_above,
        "bands": dict(sorted(bands.items())),
    }


def part_summary(path):
    """List the parts present in a VTK frame with element counts."""
    _, cells, _, _, cd, _ = read_vtk(path)
    pid = cd.get("PART_ID")
    if not pid:
        return {}
    counts = collections.Counter(int(p) for p in pid)
    return dict(sorted(counts.items()))


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def print_table(summaries, thresholds):
    """Print a compact frame-by-frame table to stdout."""
    # Detect whether any frame has erosion or damage data
    has_erosion = any(s and s.get("eroded_elements") for s in summaries)
    has_damage = any(s and s.get("max_damage") for s in summaries)

    th_heads = ["n>" + str(t) for t in thresholds] if thresholds else []
    header = "frame    time      max_eps_p  mean_eps_p"
    if th_heads:
        header += "  " + "  ".join(f"{h:>7}" for h in th_heads)
    header += "    max_vM    max_disp  max_vel"
    if has_damage:
        header += "   max_dam"
    if has_erosion:
        header += "  eroded"
    print(header)
    print("-" * len(header))

    for s in summaries:
        if s is None:
            continue
        line = f"{s['file'][-8:-4]}  {s.get('time', 0):9.2e}  {s['max_plastic_strain']:9.5f}  {s['mean_plastic_strain']:9.5f}"
        if thresholds:
            for t in thresholds:
                count = s.get("above_threshold", {}).get(str(t), 0)
                line += f"  {count:7d}"
        vm = s.get("max_von_mises", 0)
        disp = s.get("max_displacement", 0)
        vel = s.get("max_velocity", 0)
        line += f"  {vm:8.1f}   {disp:8.3f}  {vel:7.0f}"
        if has_damage:
            line += f"  {s.get('max_damage', 0):8.4f}"
        if has_erosion:
            line += f"  {s.get('eroded_elements', 0):6d}"
        print(line)


def main(argv=None):
    p = argparse.ArgumentParser(
        prog="extract_results.py",
        description="Extract per-element results from OpenRadioss VTK output",
    )
    p.add_argument("vtk_dir", help="directory containing VTK frames, or a single .vtk file")
    p.add_argument("--prefix", help="only read VTK files whose basename starts with this "
                   "(e.g. 'bracket_impact' when the vtk/ dir has multiple runs)")
    p.add_argument("--part", help="filter by part ID (comma-separated list, default: all)")
    p.add_argument("--threshold", "-t", action="append", type=float,
                   help="count elements above this strain level (repeatable, "
                        "e.g. -t 0.02 -t 0.06 -t 0.12)")
    p.add_argument("--hotspots", "-n", type=int, default=0,
                   help="print the N most-strained elements at the last frame")
    p.add_argument("--spatial", action="store_true",
                   help="show spatial distribution of yielded elements at the last frame")
    p.add_argument("--spatial-threshold", type=float, default=0.02,
                   help="strain threshold for spatial distribution (default: 0.02)")
    p.add_argument("--band-size", type=float, default=10.0,
                   help="bin width for spatial distribution (default: 10 mm)")
    p.add_argument("--axis", choices=["r", "x", "y", "z"], default="r",
                   help="spatial axis: r=radial in xy plane (default), or x/y/z")
    p.add_argument("--parts-list", action="store_true",
                   help="list the parts in the first frame and exit")
    p.add_argument("--last-only", action="store_true",
                   help="analyse only the last frame")
    p.add_argument("--json", action="store_true",
                   help="output as JSON instead of tables")
    args = p.parse_args(argv)

    # Resolve file list
    if os.path.isfile(args.vtk_dir):
        files = [args.vtk_dir]
    else:
        files = sorted(glob.glob(os.path.join(args.vtk_dir, "*.vtk")))
    if args.prefix:
        files = [f for f in files if os.path.basename(f).startswith(args.prefix)]
    if not files:
        sys.exit(f"extract_results: no .vtk files found in {args.vtk_dir}"
                 + (f" matching prefix '{args.prefix}'" if args.prefix else ""))

    # Parse part filter
    part_filter = None
    if args.part:
        part_filter = [int(x) for x in args.part.split(",")]
        if len(part_filter) == 1:
            part_filter = part_filter[0]

    # --parts-list: enumerate parts and exit
    if args.parts_list:
        parts = part_summary(files[0])
        if args.json:
            print(json.dumps({"parts": parts}, indent=2))
        else:
            print("PART_ID  elements")
            for pid, count in parts.items():
                print(f"  {pid:5d}  {count:8d}")
        return 0

    thresholds = args.threshold or []

    # Default thresholds if none given
    if not thresholds and not args.json:
        thresholds = [0.02, 0.06, 0.12]

    # Frame summaries
    if args.last_only:
        frame_files = [files[-1]]
    else:
        frame_files = files

    summaries = []
    for fp in frame_files:
        s = frame_summary(fp, part_filter, thresholds)
        summaries.append(s)

    if args.json:
        result = {"frames": [s for s in summaries if s]}
    else:
        print_table(summaries, thresholds)

    # Last-frame analysis
    last_file = files[-1]

    if args.hotspots > 0:
        spots = hotspot_analysis(last_file, part_filter, args.hotspots)
        if args.json:
            result["hotspots"] = spots
        else:
            print(f"\nhottest {args.hotspots} elements (last frame: "
                  f"{os.path.basename(last_file)}):")
            for s in spots:
                c = s["centroid"]
                line = (f"  eps={s['plastic_strain']:.4f}"
                        f"  vM={s.get('von_mises', 0):7.1f}"
                        f"  ({c[0]:7.1f},{c[1]:7.1f},{c[2]:7.2f})"
                        f"  r={s['radius']:.0f}")
                if "part_id" in s:
                    line += f"  part={s['part_id']}"
                print(line)

    if args.spatial:
        dist = spatial_distribution(last_file, part_filter,
                                    args.spatial_threshold,
                                    args.band_size, args.axis)
        if args.json:
            result["spatial"] = dist
        else:
            axis_label = {"r": "radius", "x": "x", "y": "y", "z": "z"}[args.axis]
            print(f"\nyielded elements (eps_p>{args.spatial_threshold}) by "
                  f"{axis_label} band (last frame):")
            print(f"total above threshold: {dist['total_above']}")
            bs = int(args.band_size)
            for b, count in sorted(dist["bands"].items()):
                print(f"  {axis_label} = {b:3d}-{b + bs:3d} mm : {count}")

    # Extra info at the end
    if not args.json:
        pts, cells, ct, pd, cd, fd = read_vtk(last_file)
        pid = cd.get("PART_ID")
        if pid:
            indices = filter_elements(cd, part_filter)
            parts = collections.Counter(int(pid[i]) for i in indices)
            total = len(cd.get("PART_ID", []))
            sel = len(indices)
            print(f"\nselected elements: {sel} / {total} total")
            for p, c in sorted(parts.items()):
                print(f"  part {p}: {c} elements")

    if args.json:
        print(json.dumps(result, indent=2))

    return 0


if __name__ == "__main__":
    sys.exit(main())
