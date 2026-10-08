#!/usr/bin/env python3
"""Query the raw entity graph of a STEP file in readable form.

Instead of grepping ``#`` soup by hand, ask for entities by id, by type, or by
text, and walk references in either direction with values resolved inline.
"""

from __future__ import annotations

import math
import re
import sys

import _common as C
import stepcore


def fmt_value(sf, v, depth, max_depth, seen):
    if isinstance(v, stepcore.Ref):
        if depth >= max_depth:
            return "#%d" % int(v)
        e = sf.get(v)
        if e is None:
            return "#%d(missing)" % int(v)
        if int(v) in seen:
            return "#%d(...)" % int(v)
        return "#%d=%s" % (int(v), fmt_entity_inline(sf, e, depth + 1, max_depth,
                                                     seen | {int(v)}))
    if isinstance(v, list):
        return "(" + ", ".join(fmt_value(sf, x, depth, max_depth, seen) for x in v) + ")"
    if isinstance(v, stepcore.Typed):
        return "%s(%s)" % (v.name, ", ".join(
            fmt_value(sf, x, depth, max_depth, seen) for x in v.params))
    if v is None:
        return "$"
    if isinstance(v, bool):
        return ".T." if v else ".F."
    if isinstance(v, stepcore.Enum):
        return ".%s." % v
    if isinstance(v, str):
        return "'%s'" % v
    if isinstance(v, float):
        return C.fmt_num(v, 8)
    return str(v)


def fmt_entity_inline(sf, e, depth, max_depth, seen=frozenset()):
    return "%s(%s)" % (e.type, ", ".join(
        fmt_value(sf, p, depth, max_depth, seen | {e.id}) for p in e.params))


def describe(sf, e):
    """Extra human context for the geometry types worth recognising."""
    t = e.type
    try:
        if t == "CARTESIAN_POINT":
            return "point %s" % sf.point3(e.id).fmt(4)
        if t == "DIRECTION":
            d = sf.direction(e.id)
            return "direction %s (%s)" % (d.fmt(4), d.label())
        if t == "AXIS2_PLACEMENT_3D":
            return sf.axis2(e.id).describe()
        if t == "CYLINDRICAL_SURFACE":
            f = sf.axis2(e.p(1))
            return "cylinder D=%s, axis %s through %s" % (
                C.fmt_num(float(e.p(2)) * 2, 4), f.z.label(), f.origin.fmt(4))
        if t == "CONICAL_SURFACE":
            f = sf.axis2(e.p(1))
            return "cone R=%s, half-angle %.3f deg, axis %s at %s" % (
                C.fmt_num(float(e.p(2)), 4), math.degrees(float(e.p(3))),
                f.z.label(), f.origin.fmt(4))
        if t == "PLANE":
            f = sf.axis2(e.p(1))
            return "plane, normal %s, offset %s" % (
                f.z.label(), C.fmt_num(f.origin.dot(f.z), 4))
        if t == "CIRCLE":
            f = sf.axis2(e.p(1))
            return "circle D=%s at %s, axis %s" % (
                C.fmt_num(float(e.p(2)) * 2, 4), f.origin.fmt(4), f.z.label())
        if t == "MANIFOLD_SOLID_BREP":
            return "solid %r" % (e.p(0) or "")
        if t == "PRODUCT":
            return "product %r (%s)" % (e.p(0), e.p(1))
    except Exception:
        pass
    return None


def collect(path, ids=(), types=(), grep=None, resolve=1, limit=50,
            refs_to=None, refs_from=None):
    sf = stepcore.load(path)
    picked = []

    for i in ids:
        e = sf.get(i)
        if e is None:
            raise SystemExit("no entity #%s in %s" % (i, path))
        picked.append(e)
    for t in types:
        for e in sf.of_type(t):
            picked.append(e)
    if grep:
        rx = re.compile(grep, re.I)
        for e in sf.entities.values():
            if rx.search(e._raw) or rx.search(e.type):
                picked.append(e)
    if refs_to is not None:
        for eid in sf.refs_in(refs_to):
            picked.append(sf.entities[eid])
    if refs_from is not None:
        for eid in sf.refs_out(refs_from):
            e = sf.get(eid)
            if e is not None:
                picked.append(e)

    seen, uniq = set(), []
    for e in picked:
        if e.id in seen:
            continue
        seen.add(e.id)
        uniq.append(e)
    truncated = len(uniq) > limit
    uniq = uniq[:limit]

    return {
        "path": path,
        "count": len(uniq),
        "truncated": truncated,
        "entities": [{
            "id": e.id,
            "type": e.type,
            "types": list(e.types),
            "text": fmt_entity_inline(sf, e, 0, resolve),
            "note": describe(sf, e),
            "referenced_by": sf.refs_in(e.id)[:12],
        } for e in uniq],
    }


def show(d):
    for e in d["entities"]:
        print("#%d = %s" % (e["id"], e["text"]))
        if e["note"]:
            print("      -> %s" % e["note"])
        if e["referenced_by"]:
            print("      used by: %s" % ", ".join("#%d" % r for r in e["referenced_by"]))
    print("")
    print("%d entities%s" % (d["count"], " (truncated; raise --limit)"
                             if d["truncated"] else ""))


def main(argv=None):
    ap = C.base_parser(__doc__)
    ap.add_argument("--id", action="append", type=int, default=[],
                    help="entity id, repeatable")
    ap.add_argument("--type", action="append", default=[],
                    help="entity type, e.g. CYLINDRICAL_SURFACE")
    ap.add_argument("--grep", help="regex over the raw entity text")
    ap.add_argument("--refs-to", type=int, metavar="ID",
                    help="entities that reference ID")
    ap.add_argument("--refs-from", type=int, metavar="ID",
                    help="entities that ID references")
    ap.add_argument("--resolve", type=int, default=1,
                    help="how many reference levels to expand inline (default 1)")
    ap.add_argument("--limit", type=int, default=50)
    a = ap.parse_args(argv)
    if not (a.id or a.type or a.grep or a.refs_to is not None or a.refs_from is not None):
        ap.error("give at least one of --id/--type/--grep/--refs-to/--refs-from")
    C.emit(collect(a.file, a.id, a.type, a.grep, a.resolve, a.limit,
                   a.refs_to, a.refs_from), a.json, show)


if __name__ == "__main__":
    sys.exit(main())
