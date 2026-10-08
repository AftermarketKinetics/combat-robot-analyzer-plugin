#!/usr/bin/env python3
"""Where every part sits and which way it points.

For each assembly instance this reports the world-space origin, the direction
its local +Z and +X axes end up pointing, and the rotation as an axis/angle
pair.  ``--relative-to`` re-expresses everything in another part's frame,
which is how you answer "how far is the motor from the rail, along the rail".
"""

from __future__ import annotations

import sys

import _common as C
import stepcore


def collect(path, parts=None, relative_to=None, leaves_only=False):
    return collect_from(stepcore.load(path), parts, relative_to, leaves_only)


def collect_from(sf, parts=None, relative_to=None, leaves_only=False,
                 id_of=None):
    """As :func:`collect`, but reusing a :class:`stepcore.StepFile`.

    *id_of* maps a :class:`stepcore.AssemblyNode` to a stable part id; pass it
    to get a ``part_id`` on every placement, which is what distinguishes eight
    identical standoffs that share a name and a path.
    """
    nodes = [n for r in sf.assembly() for n in r.walk()]

    ref = None
    ref_name = None
    if relative_to:
        for n in nodes:
            if C.match(n.product, [relative_to], default=False):
                ref = n.world.inverse()
                ref_name = n.product
                break
        if ref is None:
            raise SystemExit("no part matching %r (try step_tree.py)" % relative_to)

    rows = []
    for n in nodes:
        if leaves_only and n.children:
            continue
        if not C.match(n.product, parts):
            continue
        w = n.world if ref is None else (ref * n.world)
        axis, ang = w.axis_angle()
        row = {
            "product": n.product,
            "instance": n.instance,
            "path": n.path(),
            "depth": n.depth,
            "solids": len(n.solids),
            "origin": list(w.t),
            "z_axis": list(w.apply_dir(stepcore.Vec(0, 0, 1))),
            "x_axis": list(w.apply_dir(stepcore.Vec(1, 0, 0))),
            "rotation_axis": list(axis),
            "rotation_deg": ang,
            "local_origin": list(n.local.t),
        }
        if id_of is not None:
            row["part_id"] = id_of(n)
        rows.append(row)
    return {"path": sf.path, "relative_to": ref_name, "placements": rows}


def show(d):
    if d["relative_to"]:
        print("Coordinates expressed in the frame of %r" % d["relative_to"])
    rows = []
    for p in d["placements"]:
        rows.append([
            "  " * p["depth"] + p["product"],
            C.fmt_xyz(p["origin"], 4),
            stepcore.Vec(p["z_axis"]).label(),
            stepcore.Vec(p["x_axis"]).label(),
            "-" if p["rotation_deg"] < 1e-6 else
            "%.2f deg / %s" % (p["rotation_deg"], stepcore.Vec(p["rotation_axis"]).label()),
            p["solids"] or "",
        ])
    C.table(rows, ["part", "origin", "+Z", "+X", "rotation", "solids"])
    print("")
    print("%d placements" % len(d["placements"]))


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--part", action="append", default=[],
                    help="only parts whose name matches (substring or glob; repeatable)")
    ap.add_argument("--relative-to", metavar="PART",
                    help="express all coordinates in this part's frame")
    ap.add_argument("--leaves", action="store_true",
                    help="only leaf instances (skip subassembly nodes)")
    a = ap.parse_args(argv)
    C.emit(collect(a.file, a.part, a.relative_to, a.leaves), a.json, show)


if __name__ == "__main__":
    sys.exit(main())
