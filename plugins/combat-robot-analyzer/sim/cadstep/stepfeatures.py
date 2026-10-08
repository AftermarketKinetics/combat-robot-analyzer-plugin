#!/usr/bin/env python3
"""Turn raw B-rep surfaces into machining features: holes, bosses, bolt circles.

A "hole" in a STEP file is not an entity -- it is a set of cylindrical faces
that happen to share an axis.  This module does that grouping, works out the
axial extent from the faces' own vertices, decides hole-vs-boss from face
orientation, then looks for counterbores, countersinks and bolt circles.
"""

from __future__ import annotations

import math

from stepcore import Vec

# nominal diameter (mm) -> description.  Tap/clearance sizes for the fasteners
# that actually turn up in hobby/robot CAD.
FASTENER_TABLE = [
    (1.6, "M2 tap"), (2.0, "M2 free-fit / M2 nominal"), (2.2, "M2 close clearance"),
    (2.4, "M2 normal clearance"), (2.05, "M2.5 tap"), (2.5, "M3 tap"),
    (2.7, "M2.5 close clearance"), (2.9, "M2.5 normal clearance"),
    (3.0, "M3 nominal / 3 mm shaft"), (3.2, "M3 close clearance"),
    (3.4, "M3 normal clearance"), (3.3, "M4 tap"), (4.0, "M4 nominal / 4 mm shaft"),
    (4.3, "M4 close clearance"), (4.5, "M4 normal clearance"),
    (4.2, "M5 tap"), (5.0, "M5 nominal / 5 mm shaft"), (5.3, "M5 close clearance"),
    (5.5, "M5 normal clearance"), (5.0, "M6 tap"), (6.0, "M6 nominal / 6 mm shaft"),
    (6.4, "M6 close clearance"), (6.6, "M6 normal clearance"),
    (6.8, "M8 tap"), (8.0, "M8 nominal / 8 mm shaft"), (8.4, "M8 close clearance"),
    (9.0, "M8 normal clearance"), (10.0, "M10 nominal / 10 mm shaft"),
    (2.26, "#4-40 tap"), (2.95, "#4 clearance"), (2.71, "#6-32 tap"),
    (3.66, "#6 clearance"), (3.45, "#8-32 tap"), (4.29, "#8 clearance"),
    (5.11, "1/4-20 tap"), (6.76, "1/4 clearance"), (3.175, '1/8" shaft'),
    (4.7625, '3/16" shaft'), (6.35, '1/4" shaft'),
]

BEARING_BORES = {8.0: "608 bore", 10.0: "6000/6900 bore", 12.0: "6001 bore",
                 15.0: "6002 bore", 19.05: '3/4" bore', 22.0: "6900 OD",
                 26.0: "608 OD", 32.0: "6002 OD"}

AXIS_TOL = 1e-4
RADIUS_TOL = 1e-4


def _canonical_axis(d):
    """Flip the direction so opposite-facing coaxial faces group together."""
    for c in d:
        if abs(c) > 1e-9:
            return d if c > 0 else d * -1.0
    return d


def _axis_key(frame, ndigits=3):
    d = _canonical_axis(frame.z.unit())
    o = frame.origin
    perp = o - d * o.dot(d)          # closest point on the axis to the origin
    return (round(d[0], 6), round(d[1], 6), round(d[2], 6),
            round(perp[0], ndigits), round(perp[1], ndigits), round(perp[2], ndigits))


def _fastener_guess(diameter, tol=0.12):
    hits = [name for d, name in FASTENER_TABLE if abs(d - diameter) <= tol]
    if diameter in BEARING_BORES:
        hits.append(BEARING_BORES[diameter])
    for d, name in BEARING_BORES.items():
        if abs(d - diameter) <= tol and name not in hits:
            hits.append(name)
    seen, out = set(), []
    for h in hits:
        if h not in seen:
            seen.add(h)
            out.append(h)
    return out


class Cylinder(object):
    __slots__ = ("axis", "point", "radius", "faces", "tmin", "tmax",
                 "internal", "solid_id")

    @property
    def diameter(self):
        return self.radius * 2.0

    @property
    def depth(self):
        return self.tmax - self.tmin

    def center(self):
        return self.point + self.axis * ((self.tmin + self.tmax) / 2.0)

    def start(self):
        return self.point + self.axis * self.tmin

    def end(self):
        return self.point + self.axis * self.tmax


def cylinders_of(sf, solid, min_arc_faces=1):
    """Group a solid's cylindrical faces into coaxial, equal-radius clusters.

    Cached: this walks every face's vertices, which is the expensive half of
    feature extraction, and both :func:`holes_of` and :func:`blends_of` need
    the same answer.
    """
    return _memo(sf, "cylinders", (solid.id, min_arc_faces),
                 lambda: _cylinders_of(sf, solid, min_arc_faces))


def _cylinders_of(sf, solid, min_arc_faces=1):
    groups = {}
    for f in solid.faces:
        if f.surface_type != "CYLINDRICAL_SURFACE" or f.frame is None:
            continue
        key = _axis_key(f.frame) + (round(f.radius, 5),)
        groups.setdefault(key, []).append(f)

    out = []
    for key, faces in groups.items():
        d = _canonical_axis(faces[0].frame.z.unit())
        p = faces[0].frame.origin
        p = p - d * p.dot(d)
        ts = []
        inward = 0
        for f in faces:
            for v in sf.face_vertices(f.id):
                ts.append((v - p).dot(d))
            # ADVANCED_FACE sense=False flips the surface normal inward, which
            # means the material is outside the cylinder: a hole, not a boss.
            if not f.sense:
                inward += 1
        if not ts:
            continue
        c = Cylinder()
        c.axis, c.point, c.radius = d, p, faces[0].radius
        c.faces = [f.id for f in faces]
        c.tmin, c.tmax = min(ts), max(ts)
        c.internal = inward * 2 >= len(faces)
        c.solid_id = solid.id
        out.append(c)
    out.sort(key=lambda c: (-c.radius, c.tmin))
    return out


def cones_of(sf, solid):
    out = []
    for f in solid.faces:
        if f.surface_type != "CONICAL_SURFACE" or f.frame is None:
            continue
        pts = sf.face_vertices(f.id)
        d = _canonical_axis(f.frame.z.unit())
        p = f.frame.origin
        p = p - d * p.dot(d)
        ts = [(v - p).dot(d) for v in pts]
        radii = [(v - p - d * ((v - p).dot(d))).norm() for v in pts]
        out.append({
            "face": f.id, "axis": d, "point": p,
            "half_angle": f.half_angle, "radius": f.radius,
            "tmin": min(ts) if ts else 0.0, "tmax": max(ts) if ts else 0.0,
            "rmin": min(radii) if radii else f.radius,
            "rmax": max(radii) if radii else f.radius,
            "internal": not f.sense,
            "key": _axis_key(f.frame),
        })
    return out


def _memo(sf, name, key, build):
    """Cache on the file rather than in the module.

    A module-level dict keyed by ``id(sf)`` outlives the file it describes:
    once that one is collected the next :class:`~stepcore.StepFile` can be
    handed the same address and read someone else's geometry back.  Hanging
    the cache off ``sf`` gives it exactly the right lifetime.
    """
    table = sf._cache.setdefault(name, {})
    if key not in table:
        table[key] = build()
    return table[key]


def solid_points(sf, solid):
    """All vertex positions of a solid (cached)."""
    def build():
        pts = []
        for f in solid.faces:
            pts.extend(sf.face_vertices(f.id))
        return pts
    return _memo(sf, "solid_points", solid.id, build)


def _capped(sf, solid, cyl, tol=0.02):
    """Is either end of the bore closed off by material?

    A blind hole ends in a flat bottom (a planar face normal to the axis whose
    vertices all sit inside the bore) or in a drill point (a cone tapering to
    ~zero radius).  Anything else exits the part.
    """
    a, p0, r = cyl.axis, cyl.point, cyl.radius
    for f in solid.faces:
        if f.frame is None:
            continue
        if f.surface_type == "PLANE":
            n = f.frame.z.unit()
            if abs(abs(n.dot(a)) - 1.0) > 1e-6:
                continue
            pts = sf.face_vertices(f.id)
            if not pts:
                continue
            t = (pts[0] - p0).dot(a)
            if min(abs(t - cyl.tmin), abs(t - cyl.tmax)) > tol:
                continue
            if all((q - p0 - a * ((q - p0).dot(a))).norm() <= r + tol for q in pts):
                return True
        elif f.surface_type == "CONICAL_SURFACE":
            if abs(abs(f.frame.z.unit().dot(a)) - 1.0) > 1e-6:
                continue
            pts = sf.face_vertices(f.id)
            if not pts:
                continue
            radii = [(q - p0 - a * ((q - p0).dot(a))).norm() for q in pts]
            ts = [(q - p0).dot(a) for q in pts]
            if min(radii) < 0.2 and max(radii) <= r + tol and \
                    min(min(abs(t - cyl.tmin), abs(t - cyl.tmax)) for t in ts) <= 0.5:
                return True
    return False


def _through(sf, solid, cyl):
    """True when the bore exits the material at both ends."""
    if not solid.faces:
        return None
    return not _capped(sf, solid, cyl)


def holes_of(sf, solid, include_bosses=False):
    """Classified hole/boss records for one solid."""
    cyls = cylinders_of(sf, solid)
    cones = cones_of(sf, solid)
    by_axis = {}
    for c in cyls:
        by_axis.setdefault(_axis_key_of_cyl(c), []).append(c)

    records = []
    for c in cyls:
        if c.internal is False and not include_bosses:
            continue
        coaxial = [o for o in by_axis[_axis_key_of_cyl(c)] if o is not c]
        counterbore = None
        for o in coaxial:
            if o.radius > c.radius + 1e-6 and o.internal:
                overlap = min(o.tmax, c.tmax) - max(o.tmin, c.tmin)
                if overlap < 1e-3:
                    counterbore = {"diameter": o.diameter, "depth": o.depth,
                                   "at": list(o.center())}
        countersink = None
        for cone in cones:
            if cone["key"] != _axis_key_of_cyl(c) or not cone["internal"]:
                continue
            # the cone must actually open out of *this* bore: its small end
            # has to match the bore radius and sit against one of its ends
            if abs(cone["rmin"] - c.radius) > 0.35:
                continue
            gap = max(cone["tmin"] - c.tmax, c.tmin - cone["tmax"])
            if gap > 0.5:
                continue
            countersink = {
                "included_angle": None if cone["half_angle"] is None
                else round(cone["half_angle"] * 2.0, 2),
                "major_diameter": round(cone["rmax"] * 2.0, 4),
                "minor_diameter": round(cone["rmin"] * 2.0, 4),
            }
        records.append({
            "solid": solid.id,
            "kind": "hole" if c.internal else "boss",
            "diameter": c.diameter,
            "radius": c.radius,
            "depth": c.depth,
            "axis": list(c.axis),
            "center": list(c.center()),
            "start": list(c.start()),
            "end": list(c.end()),
            "through": _through(sf, solid, c),
            "faces": c.faces,
            "counterbore": counterbore,
            "countersink": countersink,
            "fastener": _fastener_guess(c.diameter),
        })
    return records


def blends_of(sf, solid, round_max=1.0):
    """Fillets and rounds -- the other thing that sets how fine a mesh has to be.

    In an explicit solve the stable timestep follows the smallest element, so
    a 0.3 mm edge break can cost ten times what the part is worth.  Holes come
    out of :func:`holes_of`; this is the curvature that is not a hole.

    A toroidal face is a blend by construction -- nothing else in mechanical
    CAD makes one -- and its minor radius *is* the fillet radius, so those are
    taken at any size.  A convex cylindrical face is more equivocal: it is a
    rolled edge, or it is a shaft.  Nothing is a 0.3 mm shaft, so those are
    taken below *round_max* and left alone above it, which costs nothing here
    because a large round was never going to be the smallest feature anyway.

    Concave blends are absent on purpose: :func:`holes_of` already reports
    them, as very small holes.
    """
    out = []
    for f in solid.faces:
        if f.surface_type != "TOROIDAL_SURFACE" or f.radius2 is None:
            continue
        pts = sf.face_vertices(f.id)
        if pts:
            c = Vec(sum(p[0] for p in pts) / len(pts),
                    sum(p[1] for p in pts) / len(pts),
                    sum(p[2] for p in pts) / len(pts))
        elif f.frame is not None:
            c = f.frame.origin
        else:
            continue
        out.append({"solid": solid.id, "kind": "fillet", "surface": "torus",
                    "radius": f.radius2, "major_radius": f.radius,
                    "center": list(c), "faces": [f.id]})
    for c in cylinders_of(sf, solid):
        if c.internal or c.radius > round_max:
            continue
        out.append({"solid": solid.id, "kind": "round", "surface": "cylinder",
                    "radius": c.radius, "major_radius": None,
                    "center": list(c.center()), "faces": list(c.faces)})
    out.sort(key=lambda r: (r["radius"], r["center"]))
    return out


def _axis_key_of_cyl(c):
    return (round(c.axis[0], 6), round(c.axis[1], 6), round(c.axis[2], 6),
            round(c.point[0], 3), round(c.point[1], 3), round(c.point[2], 3))


def bolt_circles(records, min_count=3, tol=1e-3):
    """Find coplanar, equal-diameter, parallel hole sets lying on a circle."""
    groups = {}
    for r in records:
        if r["kind"] != "hole":
            continue
        a = _canonical_axis(Vec(r["axis"]))
        c = Vec(r["center"])
        plane = round(c.dot(a), 3)
        key = (round(a[0], 5), round(a[1], 5), round(a[2], 5),
               round(r["diameter"], 4), plane)
        groups.setdefault(key, []).append(r)

    out = []
    for key, rs in groups.items():
        if len(rs) < min_count:
            continue
        a = Vec(key[0], key[1], key[2])
        pts = [Vec(r["center"]) for r in rs]
        cx = Vec(sum(p[0] for p in pts) / len(pts),
                 sum(p[1] for p in pts) / len(pts),
                 sum(p[2] for p in pts) / len(pts))
        radii = [(p - cx - a * ((p - cx).dot(a))).norm() for p in pts]
        rmean = sum(radii) / len(radii)
        if rmean < 1e-6 or max(abs(r - rmean) for r in radii) > max(0.01, rmean * 0.01):
            continue
        # angular positions for the spacing report
        ref = pts[0] - cx
        ref = (ref - a * ref.dot(a)).unit()
        perp = a.cross(ref).unit()
        angs = []
        for p in pts:
            v = p - cx
            v = v - a * v.dot(a)
            angs.append(math.degrees(math.atan2(v.dot(perp), v.dot(ref))) % 360.0)
        angs.sort()
        gaps = [round((angs[(i + 1) % len(angs)] - angs[i]) % 360.0, 3)
                for i in range(len(angs))]
        out.append({
            "count": len(rs),
            "hole_diameter": key[3],
            "bolt_circle_diameter": rmean * 2.0,
            "center": list(cx),
            "axis": list(a),
            "angles_deg": [round(x, 3) for x in angs],
            "even_spacing": len(set(gaps)) == 1,
            "spacing_deg": gaps[0] if len(set(gaps)) == 1 else None,
            "solids": sorted({r["solid"] for r in rs}),
        })
    out.sort(key=lambda b: -b["count"])
    return out


def plane_stack(sf, solid, tol=1e-4):
    """Parallel plane groups -> the solid's thicknesses along each normal."""
    groups = {}
    for f in solid.faces:
        if f.surface_type != "PLANE" or f.frame is None:
            continue
        n = _canonical_axis(f.frame.z.unit())
        key = (round(n[0], 5), round(n[1], 5), round(n[2], 5))
        offset = round(f.frame.origin.dot(n), 5)
        groups.setdefault(key, {}).setdefault(offset, []).append(f.id)
    out = []
    for key, offs in groups.items():
        if len(offs) < 2:
            continue
        vals = sorted(offs)
        out.append({
            "normal": list(key),
            "offsets": vals,
            "extent": vals[-1] - vals[0],
            "gaps": [round(vals[i + 1] - vals[i], 5) for i in range(len(vals) - 1)],
            "faces": sum(len(v) for v in offs.values()),
        })
    out.sort(key=lambda g: -g["extent"])
    return out
