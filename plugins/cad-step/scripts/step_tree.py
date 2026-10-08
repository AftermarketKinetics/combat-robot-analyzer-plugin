#!/usr/bin/env python3
"""Print the assembly (product) tree of a STEP file.

Shows every instance, how many solids it owns, and optionally where each
instance ends up in assembly coordinates.
"""

from __future__ import annotations

import sys

import _common as C
import stepcore


def collect(path):
    sf = stepcore.load(path)
    colors = sf.colors()

    def node_color(node):
        for s in node.solids:
            c = colors.get(s.id)
            if c:
                return {"rgb": [c[0], c[1], c[2]], "style": c[3]}
        return None

    def pack(n):
        return {
            "product": n.product,
            "description": n.description,
            "instance": n.instance,
            "depth": n.depth,
            "solids": [{"id": s.id, "name": s.name, "faces": len(s.faces)}
                       for s in n.solids],
            "origin": list(n.world.t),
            "rotated": not n.local.is_identity(),
            "appearance": node_color(n),
            "children": [pack(c) for c in n.children],
        }

    roots = [pack(r) for r in sf.assembly()]
    total = 0

    def count(ns):
        nonlocal total
        for n in ns:
            total += 1
            count(n["children"])
    count(roots)
    return {"path": path, "instances": total, "roots": roots}


def show(d, max_depth=None, world=False, solids=False):
    def walk(n):
        if max_depth is not None and n["depth"] > max_depth:
            return
        pad = "  " * n["depth"]
        bits = []
        if n["solids"]:
            bits.append("%d solid%s" % (len(n["solids"]),
                                        "" if len(n["solids"]) == 1 else "s"))
        if world:
            bits.append("@ %s" % C.fmt_xyz(n["origin"], 3))
        if n["appearance"] and n["appearance"]["style"]:
            bits.append(n["appearance"]["style"])
        label = n["product"]
        if n["instance"] and n["instance"] != n["product"]:
            label += "  [%s]" % n["instance"]
        print("%s%s%s" % (pad, label, ("   " + ", ".join(bits)) if bits else ""))
        if solids:
            for s in n["solids"]:
                print("%s  - solid #%d %r (%d faces)"
                      % (pad, s["id"], s["name"], s["faces"]))
        for c in n["children"]:
            walk(c)

    for r in d["roots"]:
        walk(r)
    print("")
    print("%d instances total" % d["instances"])


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--depth", type=int, default=None, help="limit tree depth")
    ap.add_argument("--world", action="store_true",
                    help="show each instance origin in assembly coordinates")
    ap.add_argument("--solids", action="store_true", help="list solid bodies")
    a = ap.parse_args(argv)
    data = collect(a.file)
    C.emit(data, a.json, lambda d: show(d, a.depth, a.world, a.solids))


if __name__ == "__main__":
    sys.exit(main())
