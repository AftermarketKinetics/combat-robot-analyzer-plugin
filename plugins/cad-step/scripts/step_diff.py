#!/usr/bin/env python3
"""Compare two STEP files -- what was added, removed, moved or reshaped.

Matches instances by their path through the assembly tree, so a part that only
moved is reported as moved rather than as one removal plus one addition.
"""

from __future__ import annotations

import math
import sys

import _common as C
import stepcore
import stepfeatures as F


def snapshot(path, holes=True):
    sf = stepcore.load(path)
    nodes = {}
    for root in sf.assembly():
        for n in root.walk():
            key = n.path()
            i = 2
            base = key
            while key in nodes:
                key = "%s#%d" % (base, i)
                i += 1
            solids = []
            for s in n.solids:
                rec = {"id": s.id, "name": s.name, "faces": len(s.faces),
                       "surfaces": dict(s.surface_census())}
                if holes:
                    hs = F.holes_of(sf, s)
                    rec["holes"] = sorted(round(h["diameter"], 4) for h in hs)
                solids.append(rec)
            nodes[key] = {
                "product": n.product,
                "origin": list(n.world.t),
                "rotation": n.world.axis_angle(),
                "solids": solids,
                "faces": sum(len(s.faces) for s in n.solids),
            }
    return {"path": path, "nodes": nodes, "units": sf.units()["length"],
            "products": sorted({v["product"] for v in nodes.values()})}


def collect(path_a, path_b, tol=1e-4, holes=True):
    a = snapshot(path_a, holes)
    b = snapshot(path_b, holes)
    keys_a, keys_b = set(a["nodes"]), set(b["nodes"])

    added = sorted(keys_b - keys_a)
    removed = sorted(keys_a - keys_b)
    moved, reshaped, unchanged = [], [], 0

    for k in sorted(keys_a & keys_b):
        na, nb = a["nodes"][k], b["nodes"][k]
        d = [nb["origin"][i] - na["origin"][i] for i in range(3)]
        dist = math.sqrt(sum(x * x for x in d))
        drot = abs(nb["rotation"][1] - na["rotation"][1])
        changed = False
        if dist > tol or drot > 1e-3:
            moved.append({"path": k, "product": na["product"],
                          "delta": d, "distance": dist,
                          "rotation_delta_deg": drot,
                          "from": na["origin"], "to": nb["origin"]})
            changed = True
        fa = {s["name"] or str(i): s for i, s in enumerate(na["solids"])}
        fb = {s["name"] or str(i): s for i, s in enumerate(nb["solids"])}
        shape_changes = []
        for name in sorted(set(fa) | set(fb)):
            sa, sb = fa.get(name), fb.get(name)
            if sa is None:
                shape_changes.append({"solid": name, "change": "added"})
            elif sb is None:
                shape_changes.append({"solid": name, "change": "removed"})
            else:
                bits = []
                if sa["faces"] != sb["faces"]:
                    bits.append("faces %d -> %d" % (sa["faces"], sb["faces"]))
                if holes and sa.get("holes") != sb.get("holes"):
                    only_a = _multiset_diff(sa["holes"], sb["holes"])
                    only_b = _multiset_diff(sb["holes"], sa["holes"])
                    if only_a:
                        bits.append("holes gone: %s" % _dias(only_a))
                    if only_b:
                        bits.append("holes new: %s" % _dias(only_b))
                if bits:
                    shape_changes.append({"solid": name, "change": "; ".join(bits)})
        if shape_changes:
            reshaped.append({"path": k, "product": na["product"],
                             "changes": shape_changes})
            changed = True
        if not changed:
            unchanged += 1

    return {"a": path_a, "b": path_b, "added": added, "removed": removed,
            "moved": moved, "reshaped": reshaped, "unchanged": unchanged,
            "products_added": sorted(set(b["products"]) - set(a["products"])),
            "products_removed": sorted(set(a["products"]) - set(b["products"]))}


def _multiset_diff(xs, ys):
    rest = list(ys)
    out = []
    for x in xs:
        if x in rest:
            rest.remove(x)
        else:
            out.append(x)
    return out


def _dias(vals):
    return ", ".join("D%.3f" % v for v in vals[:8]) + (" ..." if len(vals) > 8 else "")


def show(d):
    print("A: %s" % d["a"])
    print("B: %s" % d["b"])
    if d["products_added"]:
        C.heading("Products only in B")
        for p in d["products_added"]:
            print("  + %s" % p)
    if d["products_removed"]:
        C.heading("Products only in A")
        for p in d["products_removed"]:
            print("  - %s" % p)
    if d["added"]:
        C.heading("Instances added")
        for p in d["added"]:
            print("  + %s" % p)
    if d["removed"]:
        C.heading("Instances removed")
        for p in d["removed"]:
            print("  - %s" % p)
    if d["moved"]:
        C.heading("Instances moved")
        C.table([[m["product"], C.fmt_xyz(m["from"], 3), C.fmt_xyz(m["to"], 3),
                  "%.4f" % m["distance"],
                  "%.3f" % m["rotation_delta_deg"]] for m in d["moved"]],
                ["part", "from", "to", "moved mm", "d-rot deg"])
    if d["reshaped"]:
        C.heading("Geometry changed")
        for r in d["reshaped"]:
            print("  %s" % r["path"])
            for c in r["changes"]:
                print("      %s: %s" % (c["solid"], c["change"]))
    C.heading("Summary")
    print("  %d added, %d removed, %d moved, %d reshaped, %d unchanged"
          % (len(d["added"]), len(d["removed"]), len(d["moved"]),
             len(d["reshaped"]), d["unchanged"]))


def main(argv=None):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("old")
    ap.add_argument("new")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--tol", type=float, default=1e-4,
                    help="movement below this is not reported (mm)")
    ap.add_argument("--no-holes", action="store_true",
                    help="skip hole comparison (much faster on big assemblies)")
    a = ap.parse_args(argv)
    C.emit(collect(a.old, a.new, a.tol, not a.no_holes), a.json, show)


if __name__ == "__main__":
    sys.exit(main())
