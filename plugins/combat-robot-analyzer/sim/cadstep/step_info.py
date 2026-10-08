#!/usr/bin/env python3
"""Summarise a STEP file: provenance, units, structure, geometry, appearance.

Run this first on any unfamiliar file -- it tells you whether the file is a
single part or an assembly, what units the coordinates are in, and which of
the other tools are worth running.
"""

from __future__ import annotations

import os
import sys

import _common as C
import stepcore


def collect(path, census_n=15):
    return collect_from(stepcore.load(path), census_n)


def collect_from(sf, census_n=15):
    """As :func:`collect`, but reusing a :class:`stepcore.StepFile`."""
    path = sf.path
    units = sf.units()
    tree = sf.assembly()
    nodes = [n for r in tree for n in r.walk()]
    solids = sf.solids()
    colors = sf.colors()

    palette = {}
    for _, (r, g, b, name) in colors.items():
        key = (round(r, 3), round(g, 3), round(b, 3), name)
        palette[key] = palette.get(key, 0) + 1

    surf = {}
    for s in solids:
        for t, n in s.surface_census():
            surf[t] = surf.get(t, 0) + n

    with_solids = sum(1 for n in nodes if n.solids)
    max_depth = max([n.depth for n in nodes] or [0])
    bounds = sf.world_bounds()
    hdr = sf.header
    fname = hdr.get("FILE_NAME") or []
    desc = hdr.get("FILE_DESCRIPTION") or []

    def h(lst, i):
        try:
            v = lst[i]
            if isinstance(v, list):
                v = ", ".join(str(x) for x in v if x)
            return str(v) if v else ""
        except IndexError:
            return ""

    return {
        "path": os.path.abspath(path),
        "size_bytes": os.path.getsize(path),
        "schema": sf.schema,
        "description": h(desc, 0),
        "name": h(fname, 0),
        "timestamp": h(fname, 1),
        "author": h(fname, 2),
        "organization": h(fname, 3),
        "preprocessor": h(fname, 4),
        "originating_system": h(fname, 5),
        "units": {
            "length": units["length"][0],
            "mm_per_unit": units["length"][1],
            "angle": units["angle"][0],
            "tolerance": units["tolerance"],
            "assumed": units["assumed"],
        },
        "entities": len(sf.entities),
        "structure": {
            "products": len(sf.products()),
            "instances": sf.count("NEXT_ASSEMBLY_USAGE_OCCURRENCE"),
            "solids": len(solids),
            "roots": [r.product for r in tree],
            "max_depth": max_depth,
            "solid_bearing_nodes": with_solids,
            # CAD exporters wrap even a single body in a one-instance
            # assembly, so "has a NAUO" is not the test -- having more than
            # one shape-bearing instance is
            "is_assembly": with_solids > 1 or max_depth > 1,
        },
        "geometry": {
            "faces": sf.count("ADVANCED_FACE") + sf.count("FACE_SURFACE"),
            "edges": sf.count("EDGE_CURVE"),
            "vertices": sf.count("VERTEX_POINT"),
            "surfaces": sorted(surf.items(), key=lambda kv: -kv[1]),
            "bounds": None if bounds is None else {
                "min": list(bounds[0]), "max": list(bounds[1]),
                "size": list(bounds[1] - bounds[0]),
                "method": "vertex-based (approximate); use --exact tools for true extents",
            },
        },
        "appearance": [
            {"rgb": [k[0], k[1], k[2]], "name": k[3], "items": v}
            for k, v in sorted(palette.items(), key=lambda kv: -kv[1])
        ],
        "census": sf.type_census()[:census_n],
    }


def show(d):
    print("File        %s  (%s)" % (os.path.basename(d["path"]), C.human_size(d["size_bytes"])))
    print("Schema      %s" % (d["schema"] or "?"))
    if d["timestamp"]:
        print("Written     %s" % d["timestamp"])
    if d["originating_system"] or d["preprocessor"]:
        print("Produced by %s" % " / ".join(x for x in (d["originating_system"],
                                                        d["preprocessor"]) if x))
    u = d["units"]
    note = "  (ASSUMED - file did not declare one)" if u["assumed"] else ""
    print("Units       %s = %s mm, angles in %s, tolerance %s%s"
          % (u["length"], C.fmt_num(u["mm_per_unit"], 6), u["angle"],
             C.fmt_num(u["tolerance"], 8), note))

    s = d["structure"]
    C.heading("Structure")
    print("  %-22s %s" % ("kind", "assembly" if s["is_assembly"] else "single part"))
    print("  %-22s %d" % ("products", s["products"]))
    print("  %-22s %d" % ("instances (NAUO)", s["instances"]))
    print("  %-22s %d" % ("solids", s["solids"]))
    print("  %-22s %s" % ("root", ", ".join(s["roots"]) or "-"))
    print("  %-22s %d" % ("max depth", s["max_depth"]))

    g = d["geometry"]
    C.heading("Geometry")
    print("  %-22s faces %d, edges %d, vertices %d"
          % ("topology", g["faces"], g["edges"], g["vertices"]))
    if g["surfaces"]:
        print("  %-22s %s" % ("surface types",
                              ", ".join("%s x%d" % (t.replace("_SURFACE", ""), n)
                                        for t, n in g["surfaces"][:6])))
    b = g["bounds"]
    if b:
        print("  %-22s %s .. %s" % ("bounds", C.fmt_xyz(b["min"], 2), C.fmt_xyz(b["max"], 2)))
        print("  %-22s %s   (%s)" % ("size", C.fmt_xyz(b["size"], 2), b["method"]))

    if d["appearance"]:
        C.heading("Appearance")
        C.table([["#%02X%02X%02X" % tuple(int(round(c * 255)) for c in a["rgb"]),
                  a["name"] or "-", a["items"]] for a in d["appearance"][:12]],
                ["hex", "style", "items"], aligns=["<", "<", ">"])

    C.heading("Entity census (top %d of %d entities)" % (len(d["census"]), d["entities"]))
    C.table([[t, n] for t, n in d["census"]], ["type", "count"], aligns=["<", ">"])

    C.heading("Next steps")
    if s["is_assembly"]:
        print("  step_placements.py                  where parts sit")
    print("  step_features.py                    hole tables, bolt circles, fasteners")
    print("  step_measure.py                     volume, mass, centre of mass (exact)")
    print("  step_drawing.py                     SVG orthographic drawing")


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--census", type=int, default=15, help="entity types to list")
    a = ap.parse_args(argv)
    C.emit(collect(a.file, a.census), a.json, show)


if __name__ == "__main__":
    sys.exit(main())
