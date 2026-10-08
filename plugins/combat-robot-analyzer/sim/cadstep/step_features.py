#!/usr/bin/env python3
"""Hole tables, bolt circles and plane stacks extracted from a STEP file.

Groups coaxial cylindrical faces into holes, reports diameter / depth /
through-vs-blind / counterbore / countersink, guesses the fastener that fits,
and finds bolt-circle patterns.  ``--world`` reports positions in assembly
coordinates instead of each part's own frame.
"""

from __future__ import annotations

import sys

import _common as C
import stepcore
import stepfeatures as F


def collect(path, parts=None, world=False, bosses=False, min_dia=0.0,
            max_dia=None, planes=False, min_circle=3, blends=False):
    return collect_from(stepcore.load(path), parts, world, bosses, min_dia,
                        max_dia, planes, min_circle, blends=blends)


def collect_from(sf, parts=None, world=False, bosses=False, min_dia=0.0,
                 max_dia=None, planes=False, min_circle=3, id_of=None,
                 blends=False, deadline=None):
    """As :func:`collect`, but reusing a :class:`stepcore.StepFile`.

    *id_of* maps a :class:`stepcore.AssemblyNode` to a stable part id; pass it
    to get a ``part_id`` on every hole and plane-stack record.

    *blends* adds a ``blends`` list of fillets and rounds (see
    :func:`stepfeatures.blends_of`).  It is off by default and the CLI never
    asks for it, so the tool's output is unchanged; a caller sizing a mesh
    wants it, because a fillet drives the timestep as hard as a hole does.

    *deadline* is an absolute ``time.time()`` checked between solids; the
    result then carries ``incomplete`` and how many solids were reached.
    """
    node_of = {}
    for root in sf.assembly():
        for n in root.walk():
            for s in n.solids:
                node_of.setdefault(s.id, n)

    solids = sf.solids()
    records, plane_groups, blend_records, skipped = [], [], [], 0
    reached = 0
    for s in solids:
        if C.expired(deadline):
            break
        reached += 1
        node = node_of.get(s.id)
        name = node.product if node else (s.name or "solid #%d" % s.id)
        if not C.match(name, parts) and not C.match(s.name, parts):
            skipped += 1
            continue
        xf = node.world if (world and node) else None
        for r in F.holes_of(sf, s, include_bosses=bosses):
            if r["diameter"] < min_dia:
                continue
            if max_dia is not None and r["diameter"] > max_dia:
                continue
            r["part"] = name
            r["solid_name"] = s.name
            if id_of is not None:
                r["part_id"] = id_of(node)
            if xf is not None:
                r["center"] = list(xf.apply(stepcore.Vec(r["center"])))
                r["start"] = list(xf.apply(stepcore.Vec(r["start"])))
                r["end"] = list(xf.apply(stepcore.Vec(r["end"])))
                r["axis"] = list(xf.apply_dir(stepcore.Vec(r["axis"])))
            records.append(r)
        if blends:
            for b in F.blends_of(sf, s):
                b["part"] = name
                if id_of is not None:
                    b["part_id"] = id_of(node)
                if xf is not None:
                    b["center"] = list(xf.apply(stepcore.Vec(b["center"])))
                blend_records.append(b)
        if planes:
            for g in F.plane_stack(sf, s):
                g["part"] = name
                g["solid"] = s.id
                if id_of is not None:
                    g["part_id"] = id_of(node)
                plane_groups.append(g)

    records.sort(key=lambda r: (r["part"], -r["diameter"],
                                r["center"][0], r["center"][1], r["center"][2]))
    blend_records.sort(key=lambda b: (b["radius"], b["part"]))
    out_blends = {"blends": blend_records} if blends else {}
    return {
        "path": sf.path,
        "frame": "assembly" if world else "part-local",
        "holes": records,
        "bolt_circles": F.bolt_circles(records, min_count=min_circle),
        "plane_stacks": plane_groups,
        "parts_skipped": skipped,
        **out_blends,
        **({"solids_reached": reached, "solids_total": len(solids),
            "incomplete": reached < len(solids)} if deadline is not None else {}),
    }


def show(d):
    print("Coordinates: %s frame" % d["frame"])
    rows = []
    for r in d["holes"]:
        extra = []
        if r["counterbore"]:
            extra.append("c'bore D%.2f x %.2f"
                         % (r["counterbore"]["diameter"], r["counterbore"]["depth"]))
        if r["countersink"]:
            ia = r["countersink"]["included_angle"]
            extra.append("c'sink %s" % ("%.0f deg" % ia if ia else "?"))
        if r["fastener"]:
            extra.append(r["fastener"][0])
        rows.append([
            r["part"],
            r["kind"],
            "%.3f" % r["diameter"],
            "%.3f" % r["depth"],
            {True: "through", False: "blind", None: "?"}[r["through"]],
            stepcore.Vec(r["axis"]).label(),
            C.fmt_xyz(r["center"], 4),
            "; ".join(extra),
        ])
    C.table(rows, ["part", "kind", "dia", "depth", "type", "axis", "center", "notes"])
    print("")
    print("%d features" % len(d["holes"]))

    if d["bolt_circles"]:
        C.heading("Bolt circles")
        C.table([[
            b["count"],
            "%.3f" % b["hole_diameter"],
            "%.3f" % b["bolt_circle_diameter"],
            "%.3f" % (b["bolt_circle_diameter"] / 2.0),
            C.fmt_xyz(b["center"], 4),
            stepcore.Vec(b["axis"]).label(),
            ("even, %.1f deg" % b["spacing_deg"]) if b["even_spacing"] else "uneven",
        ] for b in d["bolt_circles"]],
            ["holes", "hole dia", "BC dia", "BC radius", "center", "axis", "spacing"])

    if d["plane_stacks"]:
        C.heading("Parallel plane stacks (thicknesses)")
        C.table([[
            g["part"], stepcore.Vec(g["normal"]).label(),
            "%.3f" % g["extent"],
            ", ".join("%.3f" % x for x in g["gaps"][:6]),
        ] for g in d["plane_stacks"][:30]],
            ["part", "normal", "extent", "gaps"])


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[],
                    help="restrict to matching part/solid names (repeatable)")
    ap.add_argument("--world", action="store_true",
                    help="report positions in assembly coordinates")
    ap.add_argument("--bosses", action="store_true",
                    help="also report external cylinders (shafts, pins, bosses)")
    ap.add_argument("--min-dia", type=float, default=0.0)
    ap.add_argument("--max-dia", type=float, default=None)
    ap.add_argument("--planes", action="store_true",
                    help="also report parallel plane stacks (plate thicknesses)")
    ap.add_argument("--min-circle", type=int, default=3,
                    help="minimum holes to call something a bolt circle")
    a = ap.parse_args(argv)
    data = collect(a.file, a.part, a.world, a.bosses, a.min_dia, a.max_dia,
                   a.planes, a.min_circle)
    C.emit(data, a.json, show)


if __name__ == "__main__":
    sys.exit(main())
