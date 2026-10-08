#!/usr/bin/env python3
"""Per-part triangles and B-rep edges, for a viewer that has to show the model.

Every other section here answers a question about the geometry. This one hands
the geometry over, because some questions cannot be answered without surfaces
and this analysis has none -- it reasons about bounding boxes, and a bounding
box is not a shape. Whether a point lies *on* a part is one such question, and
it is the one that decides where an impact is aimed.

Two representations per part, because a viewer draws the selection solid and
everything else as wireframe:

* triangles, from ``BRepMesh`` -- shaded, only ever drawn for the selection
* edges, discretised from the real B-rep curves -- what the wireframe is

Real edges rather than triangle outlines: a tessellated box has 12 triangle
edges per face pair and looks like a mess of diagonals, where the B-rep has 12
clean lines for the whole solid. It is both prettier and far smaller.

Positions are quantised to uint16 over the *model's* bounding box, so every
part shares one frame. At 65k steps across a 500 mm bot that is 8 um, far
finer than a 1.5 mm simulation mesh cares about, and it halves the file
against float32.

**The geometry is written to disk, not returned inline.** The corpus median is
6 MB a model, which is three orders of magnitude past the drawing SVG this
would otherwise imitate; the section returns a manifest and the caller streams
the file wherever it is going.

**It carries opaque ids and no names.** ``parts[].name`` and ``parts[].path``
are user-authored text, and this document is the largest thing the pipeline
hands out -- it is the one object that goes to a browser. Callers that already
hold an id-to-name map (the analyser keeps one in ``private/``) join against
it; callers that do not, like the offline corpus tool, ask for the sidecar
with ``names_out`` and get it written separately, where losing track of it is
a deliberate act rather than an oversight.
"""

from __future__ import annotations

import base64
import json
import math
import os
import struct
import time
from itertools import pairwise

#: Chord tolerance for tessellation and edge discretisation, as a fraction of
#: the model's bounding-box diagonal. 1/600 keeps a 300 mm bar visibly curved
#: at its fillets while staying inside a few MB per model. Coarsening it is
#: not the lever it looks like: measured on a 178k-triangle model, going to
#: 1/150 dropped only 50k triangles, because mechanical CAD is mostly planar
#: and a flat face needs two triangles at any tolerance. Cost tracks face
#: count, which is why admission is on faces.
DEFLECTION_RATIO = 1.0 / 600.0

#: Angular tolerance (radians). Coarse on purpose: this is a recognition aid
#: and an aiming surface, not a render.
ANGULAR = 0.6

#: Dedup key for tessellation vertices, in reciprocal mm. Below a micron two
#: vertices are the same corner.
VERTEX_QUANTUM = 1000.0



def _ntris(record):
    """Triangle count of one record: 12 bytes (3 x uint32) per triangle,
    counted on the decoded bytes -- v2 fix; the base64 text is 4/3 longer."""
    return len(base64.b64decode(record["tris"])) // 12

def _b64(raw):
    return base64.b64encode(raw).decode("ascii")


def _quantise(vals, lo, span):
    """Map floats in ``[lo, lo+span]`` onto uint16."""
    if span <= 0:
        span = 1.0
    scale = 65535.0 / span
    return b"".join(
        struct.pack("<H", max(0, min(65535, round((v - lo) * scale)))) for v in vals
    )


def _edges(shape, deflection):
    """Discretised B-rep edges as a flat list of line-segment endpoints."""
    from OCC.Core.BRep import BRep_Tool
    from OCC.Core.BRepAdaptor import BRepAdaptor_Curve
    from OCC.Core.GCPnts import GCPnts_QuasiUniformDeflection
    from OCC.Core.TopAbs import TopAbs_EDGE
    from OCC.Core.TopExp import TopExp_Explorer

    out = []
    exp = TopExp_Explorer(shape, TopAbs_EDGE)
    while exp.More():
        edge = exp.Current()
        exp.Next()
        # A degenerate edge (a pole seam on a sphere, say) has no 3D curve.
        if BRep_Tool.Degenerated(edge):
            continue
        try:
            curve = BRepAdaptor_Curve(edge)
            disc = GCPnts_QuasiUniformDeflection(curve, deflection)
            if not disc.IsDone() or disc.NbPoints() < 2:
                continue
            pts = [disc.Value(i) for i in range(1, disc.NbPoints() + 1)]
        except Exception:
            continue
        for a, b in pairwise(pts):
            out.append((a.X(), a.Y(), a.Z()))
            out.append((b.X(), b.Y(), b.Z()))
    return out


def _triangles(occ, shape, deflection):
    """Indexed triangles: ``(vertices, indices)`` with vertices deduplicated."""
    verts = {}
    flat = []
    idx = []
    for tri in occ.mesh(shape, deflection=deflection, angular=ANGULAR):
        for p in tri:
            key = (int(p[0] * VERTEX_QUANTUM),
                   int(p[1] * VERTEX_QUANTUM),
                   int(p[2] * VERTEX_QUANTUM))
            got = verts.get(key)
            if got is None:
                got = len(flat)
                verts[key] = got
                flat.append(p)
            idx.append(got)
    return flat, idx


def _frame(occ, loaded):
    """The shared quantisation frame: model bounds, span, and per-part boxes.

    One frame for the whole model rather than one per part, because a viewer
    draws them together and a per-part frame would make every part's precision
    depend on its own size.
    """
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    boxes = {}
    for p in loaded:
        bb = occ.bbox_or_none(p.shape)
        if bb is None:
            continue
        blo, bhi = bb
        boxes[id(p)] = list(blo) + list(bhi)
        for k in range(3):
            lo[k] = min(lo[k], blo[k])
            hi[k] = max(hi[k], bhi[k])
    return lo, hi, boxes


def collect_from(loaded, out_dir, name="model", source="", id_of=None,
                 max_parts=0, deadline=None, names_out=None):
    """Tessellate every part and write one geometry document to ``out_dir``.

    *id_of* maps an :class:`occshapes.Part` to the stable part id the rest of
    the report uses. Without it the document is unjoinable to anything, which
    makes it useless -- a viewer that cannot say which part it is showing
    cannot tag it either -- so it is required in practice.

    *max_parts* keeps only the largest N by volume. Zero, the default, keeps
    everything: a part absent from the document cannot be tagged or aimed at,
    and the caller has already decided admission on face count.

    *names_out* writes the id-to-name sidecar to that path. Without it the
    names are dropped, which is the right default: a caller that needs them
    almost always has them already.

    Returns the manifest. The geometry itself is on disk.
    """
    t0 = time.time()
    import occshapes as occ

    lo, hi, boxes = _frame(occ, loaded)
    if not boxes:
        raise ValueError("no part has a bounding box; nothing to tessellate")
    diag = math.dist(lo, hi)
    deflection = diag * DEFLECTION_RATIO
    span = [hi[k] - lo[k] for k in range(3)]

    unmeasured = []
    volumes = {}
    for p in loaded:
        if id(p) not in boxes:
            continue
        try:
            volumes[id(p)] = float(occ.volume_props(p.shape)["volume"])
        except Exception as exc:
            # Kept rather than swallowed: ranking keys off volume, so a silent
            # zero would quietly drop real parts to the bottom of the list.
            # Reported by id, because this travels with the manifest and the
            # part's name does not leave the caller.
            unmeasured.append({
                "id": id_of(p) if id_of else None,
                "error": "%s: %s" % (type(exc).__name__, exc),
            })
            volumes[id(p)] = 0.0

    ranked = [p for p in loaded if id(p) in boxes]
    dropped = 0
    if max_parts and len(ranked) > max_parts:
        ranked = sorted(ranked, key=lambda p: -volumes[id(p)])[:max_parts]
        dropped = len(boxes) - len(ranked)

    records = []
    names = {}
    incomplete = False
    for p in ranked:
        if deadline is not None and time.time() > deadline:
            incomplete = True
            break
        tri_v, tri_i = _triangles(occ, p.shape, deflection)
        seg = _edges(p.shape, deflection)
        if id_of:
            names[id_of(p)] = {"name": p.name or "(unnamed)", "path": p.path}
        records.append({
            "id": id_of(p) if id_of else None,
            "volume_mm3": round(volumes[id(p)], 3),
            "bbox": [round(v, 3) for v in boxes[id(p)]],
            "color": list(p.color) if p.color else None,
            "nv": len(tri_v),
            "verts": _b64(b"".join(
                _quantise([v[k] for v in tri_v], lo[k], span[k]) for k in range(3))),
            "tris": _b64(b"".join(struct.pack("<I", i) for i in tri_i)),
            "ns": len(seg),
            "segs": _b64(b"".join(
                _quantise([s[k] for s in seg], lo[k], span[k]) for k in range(3))),
        })

    doc = {
        "model": name,
        "source": source,
        "lo": [round(v, 4) for v in lo],
        "span": [round(v, 4) for v in span],
        "deflection_mm": round(deflection, 4),
        "part_count_total": len(loaded),
        "unmeasured": unmeasured,
        "parts": records,
    }

    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "%s.json" % name)
    with open(path, "w") as fh:
        json.dump(doc, fh, separators=(",", ":"))

    if names_out:
        os.makedirs(os.path.dirname(os.path.abspath(names_out)) or ".", exist_ok=True)
        with open(names_out, "w") as fh:
            json.dump({"model": name, "parts": names}, fh, separators=(",", ":"))

    return {
        "file": path,
        "names_file": names_out or None,
        "bytes": os.path.getsize(path),
        "deflection_mm": round(deflection, 4),
        "angular": ANGULAR,
        "lo": doc["lo"],
        "span": doc["span"],
        "parts_total": len(boxes),
        "parts_tessellated": len(records),
        "parts_dropped_by_max": dropped,
        "triangles": sum(_ntris(r) for r in records),
        "unmeasured": unmeasured,
        "incomplete": incomplete or bool(dropped),
        "seconds": round(time.time() - t0, 2),
        # Per-part sizes, so a caller can decide what to upload without
        # re-reading a 6 MB document to find out.
        "parts": [
            {"id": r["id"], "nv": r["nv"], "ntris": _ntris(r), "ns": r["ns"]}
            for r in records
        ],
    }


def collect(path, out_dir, max_parts=0, deadline=None, names_out=None):
    """As :func:`collect_from`, loading the STEP file itself."""
    import occenv
    occenv.ensure_occ()
    import occshapes as O
    import stepcore
    import stepjoin

    sf = stepcore.load(path)
    parts = O.load_parts(path)
    index = stepjoin.build_index(sf, parts, occ_bbox=O.bbox_or_none)
    stem = os.path.splitext(os.path.basename(path))[0]
    return collect_from(parts, out_dir, name=stem, source=os.path.basename(path),
                        id_of=index.id_for_part, max_parts=max_parts,
                        deadline=deadline, names_out=names_out)


def main(argv=None):
    import argparse

    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("file")
    ap.add_argument("-o", "--out", required=True, help="output directory")
    ap.add_argument("--max-parts", type=int, default=0,
                    help="keep only the N largest by volume (0 = all)")
    ap.add_argument("--names-out", default=None,
                    help="also write the id-to-name sidecar here; without it "
                         "the geometry carries opaque ids and nothing else")
    a = ap.parse_args(argv)
    print(json.dumps(collect(a.file, a.out, a.max_parts, names_out=a.names_out),
                     indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
