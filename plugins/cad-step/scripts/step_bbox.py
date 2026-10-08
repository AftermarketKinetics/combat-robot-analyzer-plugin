#!/usr/bin/env python3
"""Bounding boxes per part and for the whole model.

The default is the fast text tier, which measures vertices only: silhouettes
of curved faces can extend a little past the reported box.  ``--exact`` uses
OpenCASCADE for true B-rep extents.
"""

from __future__ import annotations

import sys

import _common as C
import stepcore


def collect_fast(path, parts=None):
    sf = stepcore.load(path)
    rows = []
    for root in sf.assembly():
        for n in root.walk():
            for s in n.solids:
                if not C.match(n.product, parts):
                    continue
                b = sf.solid_bounds(s)
                if b is None:
                    continue
                corners = [stepcore.Vec(x, y, z)
                           for x in (b[0][0], b[1][0])
                           for y in (b[0][1], b[1][1])
                           for z in (b[0][2], b[1][2])]
                w = [n.world.apply(c) for c in corners]
                lo = stepcore.Vec(min(p[0] for p in w), min(p[1] for p in w),
                                  min(p[2] for p in w))
                hi = stepcore.Vec(max(p[0] for p in w), max(p[1] for p in w),
                                  max(p[2] for p in w))
                rows.append({"part": n.product, "solid": s.id, "name": s.name,
                             "min": list(lo), "max": list(hi),
                             "size": list(hi - lo)})
    total = None
    if rows:
        lo = [min(r["min"][i] for r in rows) for i in range(3)]
        hi = [max(r["max"][i] for r in rows) for i in range(3)]
        total = {"min": lo, "max": hi, "size": [hi[i] - lo[i] for i in range(3)]}
    return {"path": path, "method": "vertex (approximate)", "parts": rows,
            "total": total}


def collect_exact(path, parts=None):
    import occenv
    occenv.ensure_occ()
    import occshapes as O
    rows = []
    for p in O.load_parts(path):
        if not C.match(p.name, parts) and not C.match(p.path, parts):
            continue
        extent = O.bbox_or_none(p.shape)
        if extent is None:
            continue          # bodiless occurrence: nothing to bound
        lo, hi = extent
        rows.append({"part": p.name, "path": p.path, "min": list(lo),
                     "max": list(hi),
                     "size": [hi[i] - lo[i] for i in range(3)]})
    total = None
    if rows:
        lo = [min(r["min"][i] for r in rows) for i in range(3)]
        hi = [max(r["max"][i] for r in rows) for i in range(3)]
        total = {"min": lo, "max": hi, "size": [hi[i] - lo[i] for i in range(3)]}
    return {"path": path, "method": "exact (OpenCASCADE)", "parts": rows,
            "total": total}


def show(d):
    print("Method: %s" % d["method"])
    C.table([[r["part"], C.fmt_xyz(r["min"], 3), C.fmt_xyz(r["max"], 3),
              C.fmt_xyz(r["size"], 3)] for r in d["parts"]],
            ["part", "min", "max", "size"])
    if d["total"]:
        t = d["total"]
        C.heading("Overall")
        print("  min  %s" % C.fmt_xyz(t["min"], 3))
        print("  max  %s" % C.fmt_xyz(t["max"], 3))
        print("  size %s" % C.fmt_xyz(t["size"], 3))


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[])
    ap.add_argument("--exact", action="store_true",
                    help="use OpenCASCADE for true extents (slower)")
    a = ap.parse_args(argv)
    data = collect_exact(a.file, a.part) if a.exact else collect_fast(a.file, a.part)
    C.emit(data, a.json, show)


if __name__ == "__main__":
    sys.exit(main())
