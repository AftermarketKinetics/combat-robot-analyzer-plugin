#!/usr/bin/env python3
"""stepcore -- dependency-free reader for ISO 10303-21 (STEP / .stp / .step) files.

No third-party imports.  Parses the exchange structure directly so that
structural questions (assembly tree, placements, surfaces, colours, units)
can be answered in well under a second even on multi-megabyte assemblies.

Design notes
------------
* Quoted strings and ``/* comments */`` are masked out in one pass before any
  record splitting, so a ``;`` or ``#`` inside a string can never confuse us.
* Entity parameters are parsed lazily -- loading a 9 MB assembly only splits
  records and indexes types.
* Complex instances (``#68=( A(...) B(...) C(...) )``) are parsed eagerly and
  indexed under every subtype.  All assembly transforms and all unit/context
  definitions live inside those, so missing them means missing the geometry.
"""

from __future__ import annotations

import math
import re

__all__ = [
    "Ref", "Enum", "Typed", "STAR", "Entity", "StepFile", "Vec", "Frame",
    "Transform", "AssemblyNode", "Solid", "Face", "load",
]


# --------------------------------------------------------------------------
# value types
# --------------------------------------------------------------------------

class Ref(int):
    """An entity reference (``#123``)."""
    __slots__ = ()

    def __repr__(self):
        return "#%d" % int(self)


class Enum(str):
    """A STEP enumeration value (``.BOTH.`` -> ``Enum('BOTH')``)."""
    __slots__ = ()

    def __repr__(self):
        return ".%s." % str(self)


class _Star(object):
    __slots__ = ()

    def __repr__(self):
        return "*"


STAR = _Star()


class Typed(object):
    """A typed parameter such as ``LENGTH_MEASURE(1.0)``."""
    __slots__ = ("name", "params")

    def __init__(self, name, params):
        self.name = name
        self.params = params

    def __repr__(self):
        return "%s(%s)" % (self.name, ", ".join(repr(p) for p in self.params))


class Entity(object):
    """One ``#id = TYPE(...);`` record."""
    __slots__ = ("id", "type", "types", "_raw", "_params", "_file")

    def __init__(self, eid, etype, raw, sfile, types=None, params=None):
        self.id = eid
        self.type = etype
        self.types = types or (etype,)
        self._raw = raw
        self._params = params
        self._file = sfile

    @property
    def params(self):
        if self._params is None:
            self._params = self._file._parse_body(self._raw)[1]
        return self._params

    def p(self, i, default=None):
        """Parameter *i*, or *default* when absent/``$``."""
        try:
            v = self.params[i]
        except IndexError:
            return default
        return default if v is None else v

    def sub(self, name):
        """For complex instances: the parameter list of subtype *name*."""
        if self._params is None:
            self.params  # force parse
        for part in self._complex_parts():
            if part.name == name:
                return part.params
        return None

    def _complex_parts(self):
        if len(self.types) > 1 and isinstance(self._params, list):
            return [p for p in self._params if isinstance(p, Typed)]
        return []

    def __repr__(self):
        return "#%d=%s" % (self.id, self.type)


# --------------------------------------------------------------------------
# lexer / parser
# --------------------------------------------------------------------------

_MASK = re.compile(r"/\*.*?\*/|'(?:[^']|'')*'", re.S)
_RECORD = re.compile(r"#(\d+)\s*=\s*(.*?);", re.S)
_SIMPLE_TYPE = re.compile(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*\(")
_TOKEN = re.compile(
    r"\x01(\d+)\x01"                 # masked string
    r"|#(\d+)"                       # reference
    r"|\.([A-Za-z0-9_]*)\."          # enumeration
    r"|([-+]?(?:[0-9]+\.[0-9]*(?:[eE][-+]?[0-9]+)?"
    r"|\.[0-9]+(?:[eE][-+]?[0-9]+)?"
    r"|[0-9]+(?:[eE][-+]?[0-9]+)))"  # real
    r"|([-+]?[0-9]+)"                # integer
    r"|([A-Za-z_][A-Za-z0-9_]*)"     # keyword / typed parameter
    r"|([(),$*])"                    # punctuation
)

_STEP_ESCAPE = re.compile(r"\\X2\\((?:[0-9A-Fa-f]{4})+)\\X0\\|\\X\\([0-9A-Fa-f]{2})|\\S\\(.)")


def _decode(text):
    """Undo STEP string escapes (``''``, ``\\X2\\…\\X0\\``, ``\\X\\hh``)."""
    text = text.replace("''", "'")
    if "\\" not in text:
        return text

    def rep(m):
        if m.group(1):
            hexs = m.group(1)
            return "".join(chr(int(hexs[i:i + 4], 16)) for i in range(0, len(hexs), 4))
        if m.group(2):
            return chr(int(m.group(2), 16))
        return m.group(3)

    return _STEP_ESCAPE.sub(rep, text)


class StepFile(object):
    """A parsed STEP physical file."""

    def __init__(self, path, text=None):
        self.path = path
        if text is None:
            with open(path, "r", errors="replace") as fh:
                text = fh.read()
        self._strings = []
        masked = self._mask(text)
        self.header = {}
        self.entities = {}
        self.by_type = {}
        self._refs_in = None
        self._parse_header(masked)
        self._parse_data(masked)
        self._cache = {}

    # -- masking -----------------------------------------------------------

    def _mask(self, text):
        strings = self._strings

        def rep(m):
            tok = m.group(0)
            if tok.startswith("/*"):
                return " "
            strings.append(_decode(tok[1:-1]))
            return "\x01%d\x01" % (len(strings) - 1)

        return _MASK.sub(rep, text)

    # -- sections ----------------------------------------------------------

    def _parse_header(self, masked):
        m = re.search(r"HEADER;(.*?)ENDSEC;", masked, re.S)
        if not m:
            return
        for rec in re.finditer(r"([A-Za-z_][A-Za-z0-9_]*)\s*\((.*?)\)\s*;", m.group(1), re.S):
            name = rec.group(1).upper()
            try:
                vals = self._parse_args(rec.group(2))
            except Exception:
                vals = []
            self.header[name] = vals
        sch = self.header.get("FILE_SCHEMA")
        self.schema = None
        if sch and sch[0]:
            first = sch[0][0] if isinstance(sch[0], list) else sch[0]
            self.schema = str(first)

    def _parse_data(self, masked):
        ents = self.entities
        by_type = self.by_type
        pos = masked.find("DATA;")
        chunk = masked[pos:] if pos >= 0 else masked
        for m in _RECORD.finditer(chunk):
            eid = int(m.group(1))
            raw = m.group(2)
            sm = _SIMPLE_TYPE.match(raw)
            if sm:
                etype = sm.group(1).upper()
                ent = Entity(eid, etype, raw, self)
                by_type.setdefault(etype, []).append(eid)
            else:
                etype, params = self._parse_body(raw)
                names = tuple(p.name for p in params if isinstance(p, Typed))
                ent = Entity(eid, names[0] if names else "?", raw, self,
                             types=names or ("?",), params=params)
                for n in names:
                    by_type.setdefault(n, []).append(eid)
            ents[eid] = ent

    # -- parameter parsing -------------------------------------------------

    def _parse_body(self, raw):
        sm = _SIMPLE_TYPE.match(raw)
        if sm:
            name = sm.group(1).upper()
            return name, self._parse_args(raw[sm.end():raw.rfind(")")])
        # complex instance: ( A(..) B(..) )
        inner = raw.strip()
        inner = inner[inner.find("(") + 1:inner.rfind(")")]
        return "?", self._parse_args(inner)

    def _parse_args(self, text):
        toks = list(_TOKEN.finditer(text))
        vals, _ = self._parse_seq(toks, 0, len(toks))
        return vals

    def _parse_seq(self, toks, i, end, stop=None):
        out = []
        n = end
        while i < n:
            t = toks[i]
            g = t.lastindex
            txt = t.group(0)
            if g == 7:  # punctuation
                if txt == ",":
                    i += 1
                    continue
                if txt == ")":
                    return out, i + 1
                if txt == "(":
                    inner, i = self._parse_seq(toks, i + 1, n)
                    out.append(inner)
                    continue
                if txt == "$":
                    out.append(None)
                    i += 1
                    continue
                if txt == "*":
                    out.append(STAR)
                    i += 1
                    continue
            elif g == 1:
                out.append(self._strings[int(t.group(1))])
                i += 1
                continue
            elif g == 2:
                out.append(Ref(int(t.group(2))))
                i += 1
                continue
            elif g == 3:
                v = t.group(3)
                # .T./.F. are the STEP encoding of BOOLEAN/LOGICAL
                out.append(True if v == "T" else False if v == "F" else Enum(v))
                i += 1
                continue
            elif g == 4:
                out.append(float(t.group(4)))
                i += 1
                continue
            elif g == 5:
                out.append(int(t.group(5)))
                i += 1
                continue
            elif g == 6:
                name = t.group(6).upper()
                if i + 1 < n and toks[i + 1].group(0) == "(":
                    inner, i = self._parse_seq(toks, i + 2, n)
                    out.append(Typed(name, inner))
                    continue
                if name in ("T", "TRUE"):
                    out.append(True)
                elif name in ("F", "FALSE"):
                    out.append(False)
                else:
                    out.append(Enum(name))
                i += 1
                continue
            i += 1
        return out, i

    # -- lookup ------------------------------------------------------------

    def __getitem__(self, eid):
        return self.entities[int(eid)]

    def get(self, eid, default=None):
        return self.entities.get(int(eid), default) if eid is not None else default

    def of_type(self, *types):
        """Yield every entity whose type (or complex subtype) matches."""
        for t in types:
            for eid in self.by_type.get(t.upper(), ()):
                yield self.entities[eid]

    def count(self, etype):
        return len(self.by_type.get(etype.upper(), ()))

    def type_census(self):
        return sorted(((t, len(v)) for t, v in self.by_type.items()),
                      key=lambda kv: -kv[1])

    def refs_out(self, eid):
        raw = self.entities[int(eid)]._raw
        return [int(x) for x in re.findall(r"#(\d+)", raw)]

    def refs_in(self, eid):
        if self._refs_in is None:
            back = {}
            for e in self.entities.values():
                for r in re.findall(r"#(\d+)", e._raw):
                    back.setdefault(int(r), []).append(e.id)
            self._refs_in = back
        return self._refs_in.get(int(eid), [])

    # -- units -------------------------------------------------------------

    def _unit_args(self, ent, name):
        if len(ent.types) > 1:
            return ent.sub(name) or []
        return ent.params

    def _length_mm(self, ref, depth=0):
        """Millimetres per one of the length unit at *ref* (None if not length)."""
        e = self.get(ref)
        if e is None or depth > 6:
            return None
        if "SI_UNIT" in e.types:
            a = self._unit_args(e, "SI_UNIT")
            prefix = unit = None
            for v in a:
                if isinstance(v, Enum):
                    if str(v) in ("METRE", "RADIAN", "STERADIAN", "GRAM", "SECOND"):
                        unit = str(v)
                    else:
                        prefix = str(v)
            if unit != "METRE":
                return None
            return {"MILLI": 1.0, "CENTI": 10.0, "DECI": 100.0, "MICRO": 1e-3,
                    "KILO": 1e6, None: 1000.0}.get(prefix, 1000.0)
        if "CONVERSION_BASED_UNIT" in e.types:
            a = self._unit_args(e, "CONVERSION_BASED_UNIT")
            if len(a) < 2:
                return None
            mw = a[1]
            mw = self.get(mw).params if isinstance(mw, Ref) else (
                mw.params if isinstance(mw, Typed) else [])
            if len(mw) < 2:
                return None
            v = mw[0]
            base = v.params[0] if isinstance(v, Typed) else v
            sub = self._length_mm(mw[1], depth + 1)
            try:
                return float(base) * (sub if sub is not None else 1.0)
            except (TypeError, ValueError):
                return None
        return None

    def _unit_name(self, ref):
        e = self.get(ref)
        if e is None:
            return "?"
        if "CONVERSION_BASED_UNIT" in e.types:
            a = self._unit_args(e, "CONVERSION_BASED_UNIT")
            return str(a[0]) if a else "?"
        a = self._unit_args(e, "SI_UNIT")
        parts = [str(v).lower() for v in a if isinstance(v, Enum)]
        return "".join(parts) if parts else "?"

    def units(self):
        """Global units in force: ``{'length': (name, mm_per_unit), ...}``.

        Reads ``GLOBAL_UNIT_ASSIGNED_CONTEXT`` rather than guessing from the
        first ``SI_UNIT`` -- files routinely declare several length units and
        only the context says which one the coordinates are in (the SDP-SI
        sample declares INCH that way).
        """
        if "units" in self._cache:
            return self._cache["units"]
        out = {"length": ("millimetre", 1.0), "angle": ("radian", 1.0),
               "tolerance": None, "assumed": True}
        candidates = []
        for ctx in self.of_type("GLOBAL_UNIT_ASSIGNED_CONTEXT"):
            a = self._unit_args(ctx, "GLOBAL_UNIT_ASSIGNED_CONTEXT")
            lst = a[0] if a and isinstance(a[0], list) else []
            candidates.append([int(r) for r in lst if isinstance(r, Ref)])
        if not candidates:
            candidates = [[e.id for e in self.of_type("SI_UNIT", "CONVERSION_BASED_UNIT")]]
        for group in candidates:
            for uid in group:
                mm = self._length_mm(uid)
                if mm is not None:
                    out["length"] = (self._unit_name(uid), mm)
                    out["assumed"] = False
                    break
            if not out["assumed"]:
                for uid in group:
                    e = self.get(uid)
                    if e is not None and "PLANE_ANGLE_UNIT" in e.types:
                        if "CONVERSION_BASED_UNIT" in e.types:
                            a = self._unit_args(e, "CONVERSION_BASED_UNIT")
                            mw = self.get(a[1]).params if len(a) > 1 and isinstance(a[1], Ref) else []
                            v = mw[0] if mw else None
                            base = v.params[0] if isinstance(v, Typed) else v
                            try:
                                out["angle"] = (str(a[0]), float(base))
                            except (TypeError, ValueError):
                                pass
                        else:
                            out["angle"] = ("radian", 1.0)
                        break
                break
        for e in self.of_type("UNCERTAINTY_MEASURE_WITH_UNIT"):
            v = e.params[0] if e.params else None
            base = v.params[0] if isinstance(v, Typed) else v
            try:
                out["tolerance"] = float(base)
            except (TypeError, ValueError):
                pass
            break
        self._cache["units"] = out
        return out

    # -- geometry accessors ------------------------------------------------

    def point3(self, ref):
        e = self.get(ref)
        if e is None:
            return None
        c = e.p(1)
        if not isinstance(c, list):
            return None
        vals = [float(x) for x in c[:3]]
        while len(vals) < 3:
            vals.append(0.0)
        return Vec(vals[0], vals[1], vals[2])

    def direction(self, ref):
        e = self.get(ref)
        if e is None:
            return None
        c = e.p(1)
        if not isinstance(c, list):
            return None
        vals = [float(x) for x in c[:3]]
        while len(vals) < 3:
            vals.append(0.0)
        return Vec(vals[0], vals[1], vals[2]).unit()

    def axis2(self, ref):
        """``AXIS2_PLACEMENT_3D`` -> :class:`Frame`."""
        e = self.get(ref)
        if e is None:
            return None
        origin = self.point3(e.p(1)) or Vec(0, 0, 0)
        z = self.direction(e.p(2)) if e.p(2) is not None else None
        x = self.direction(e.p(3)) if e.p(3) is not None else None
        return Frame(origin, z, x, name=e.p(0) or "")


# --------------------------------------------------------------------------
# small vector / frame maths (kept tuple-based; no numpy)
# --------------------------------------------------------------------------

class Vec(tuple):
    __slots__ = ()

    def __new__(cls, x, y=None, z=None):
        if y is None:
            x, y, z = x
        return tuple.__new__(cls, (float(x), float(y), float(z)))

    x = property(lambda s: s[0])
    y = property(lambda s: s[1])
    z = property(lambda s: s[2])

    def __add__(self, o):
        return Vec(self[0] + o[0], self[1] + o[1], self[2] + o[2])

    def __sub__(self, o):
        return Vec(self[0] - o[0], self[1] - o[1], self[2] - o[2])

    def __mul__(self, k):
        return Vec(self[0] * k, self[1] * k, self[2] * k)

    __rmul__ = __mul__

    def dot(self, o):
        return self[0] * o[0] + self[1] * o[1] + self[2] * o[2]

    def cross(self, o):
        return Vec(self[1] * o[2] - self[2] * o[1],
                   self[2] * o[0] - self[0] * o[2],
                   self[0] * o[1] - self[1] * o[0])

    def norm(self):
        return math.sqrt(self.dot(self))

    def unit(self):
        n = self.norm()
        return Vec(0, 0, 1) if n < 1e-15 else Vec(self[0] / n, self[1] / n, self[2] / n)

    def round(self, nd=6):
        return Vec(round(self[0], nd), round(self[1], nd), round(self[2], nd))

    def fmt(self, nd=4):
        return "(%.*f, %.*f, %.*f)" % (nd, self[0], nd, self[1], nd, self[2])

    def label(self, tol=1e-6):
        """Human name for an axis direction: ``+Z``, ``-Y``, or a tuple."""
        for i, ax in enumerate("XYZ"):
            for s, sign in ((1.0, "+"), (-1.0, "-")):
                probe = [0.0, 0.0, 0.0]
                probe[i] = s
                if (self - Vec(probe)).norm() < 1e-6:
                    return sign + ax
        return self.fmt(4)


IDENTITY_R = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


class Transform(object):
    """Rigid transform: rotation matrix (rows) + translation."""
    __slots__ = ("r", "t")

    def __init__(self, r=IDENTITY_R, t=(0.0, 0.0, 0.0)):
        self.r = tuple(tuple(float(v) for v in row) for row in r)
        self.t = Vec(t)

    @staticmethod
    def identity():
        return Transform()

    @staticmethod
    def from_frame(frame):
        """Frame axes become the columns of R (local -> parent)."""
        x, y, z = frame.x, frame.y, frame.z
        r = ((x[0], y[0], z[0]),
             (x[1], y[1], z[1]),
             (x[2], y[2], z[2]))
        return Transform(r, frame.origin)

    def apply(self, p):
        r, t = self.r, self.t
        return Vec(r[0][0] * p[0] + r[0][1] * p[1] + r[0][2] * p[2] + t[0],
                   r[1][0] * p[0] + r[1][1] * p[1] + r[1][2] * p[2] + t[1],
                   r[2][0] * p[0] + r[2][1] * p[1] + r[2][2] * p[2] + t[2])

    def apply_dir(self, d):
        r = self.r
        return Vec(r[0][0] * d[0] + r[0][1] * d[1] + r[0][2] * d[2],
                   r[1][0] * d[0] + r[1][1] * d[1] + r[1][2] * d[2],
                   r[2][0] * d[0] + r[2][1] * d[1] + r[2][2] * d[2])

    def __mul__(self, o):
        """``(a * b).apply(p) == a.apply(b.apply(p))``."""
        a, b = self.r, o.r
        r = tuple(tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3))
                  for i in range(3))
        return Transform(r, self.apply(o.t))

    def inverse(self):
        r = self.r
        rt = ((r[0][0], r[1][0], r[2][0]),
              (r[0][1], r[1][1], r[2][1]),
              (r[0][2], r[1][2], r[2][2]))
        inv = Transform(rt, (0, 0, 0))
        return Transform(rt, inv.apply_dir(self.t) * -1.0)

    def is_identity(self, tol=1e-9):
        if self.t.norm() > tol:
            return False
        for i in range(3):
            for j in range(3):
                if abs(self.r[i][j] - IDENTITY_R[i][j]) > tol:
                    return False
        return True

    def axis_angle(self):
        """Return (axis, degrees)."""
        r = self.r
        tr = r[0][0] + r[1][1] + r[2][2]
        c = max(-1.0, min(1.0, (tr - 1.0) / 2.0))
        ang = math.degrees(math.acos(c))
        if ang < 1e-9:
            return Vec(0, 0, 1), 0.0
        if abs(180.0 - ang) < 1e-6:
            best, bi = -1.0, 0
            for i in range(3):
                v = r[i][i]
                if v > best:
                    best, bi = v, i
            axis = [0.0, 0.0, 0.0]
            axis[bi] = math.sqrt(max(0.0, (r[bi][bi] + 1.0) / 2.0))
            for j in range(3):
                if j != bi and axis[bi] > 1e-9:
                    axis[j] = r[bi][j] / (2.0 * axis[bi])
            return Vec(axis).unit(), 180.0
        s = 2.0 * math.sin(math.radians(ang))
        return Vec((r[2][1] - r[1][2]) / s,
                   (r[0][2] - r[2][0]) / s,
                   (r[1][0] - r[0][1]) / s).unit(), ang

    def describe(self):
        axis, ang = self.axis_angle()
        if ang < 1e-6:
            rot = "no rotation"
        else:
            rot = "%.3f deg about %s" % (ang, axis.label())
        return "at %s, %s" % (self.t.fmt(4), rot)


class Frame(object):
    """An ``AXIS2_PLACEMENT_3D``: origin plus orthonormal axes."""
    __slots__ = ("origin", "z", "x", "y", "name")

    def __init__(self, origin, z=None, x=None, name=""):
        self.origin = Vec(origin)
        z = Vec(z).unit() if z is not None else Vec(0, 0, 1)
        if x is None:
            ref = Vec(1, 0, 0) if abs(z[0]) < 0.9 else Vec(0, 1, 0)
            x = ref - z * ref.dot(z)
        else:
            x = Vec(x)
            x = x - z * x.dot(z)
        if x.norm() < 1e-12:
            ref = Vec(1, 0, 0) if abs(z[0]) < 0.9 else Vec(0, 1, 0)
            x = ref - z * ref.dot(z)
        self.z = z
        self.x = x.unit()
        self.y = z.cross(self.x).unit()
        self.name = name

    def transform(self):
        return Transform.from_frame(self)

    def describe(self):
        return "origin %s  Z=%s  X=%s" % (self.origin.fmt(4), self.z.label(), self.x.label())

    def __repr__(self):
        return "Frame(%s, Z=%s)" % (self.origin.fmt(3), self.z.label())


# --------------------------------------------------------------------------
# B-rep view
# --------------------------------------------------------------------------

class Face(object):
    __slots__ = ("id", "surface_type", "surface_id", "frame", "radius",
                 "radius2", "half_angle", "sense", "_owner")

    def __init__(self, fid, stype, sid, frame, radius=None, radius2=None,
                 half_angle=None, sense=True, owner=None):
        self.id = fid
        self.surface_type = stype
        self.surface_id = sid
        self.frame = frame
        self.radius = radius
        self.radius2 = radius2
        self.half_angle = half_angle
        self.sense = sense
        self._owner = owner

    def vertices(self):
        return self._owner.face_vertices(self.id) if self._owner else []

    def __repr__(self):
        r = "" if self.radius is None else " r=%.4f" % self.radius
        return "<Face #%d %s%s>" % (self.id, self.surface_type, r)


class Solid(object):
    __slots__ = ("id", "name", "shell_id", "faces", "_owner", "product")

    def __init__(self, sid, name, shell_id, faces, owner, product=None):
        self.id = sid
        self.name = name
        self.shell_id = shell_id
        self.faces = faces
        self._owner = owner
        self.product = product

    def bounds(self):
        return self._owner.solid_bounds(self)

    def surface_census(self):
        c = {}
        for f in self.faces:
            c[f.surface_type] = c.get(f.surface_type, 0) + 1
        return sorted(c.items(), key=lambda kv: -kv[1])

    def __repr__(self):
        return "<Solid #%d %r faces=%d>" % (self.id, self.name, len(self.faces))


class AssemblyNode(object):
    """One *occurrence* of a product in the assembly tree.

    ``pd_id`` identifies the product, which repeats; ``nauo_chain`` identifies
    this particular placement of it and does not.  Eight identical standoffs
    share a ``pd_id`` and a :meth:`path`, but have eight distinct chains.
    """

    __slots__ = ("product", "description", "instance", "pd_id", "local",
                 "world", "children", "solids", "depth", "parent",
                 "nauo_id", "nauo_chain")

    def __init__(self, product, description, instance, pd_id, local, world,
                 depth=0, parent=None, nauo_id=None, nauo_chain=()):
        self.product = product
        self.description = description
        self.instance = instance
        self.pd_id = pd_id
        self.local = local
        self.world = world
        self.children = []
        self.solids = []
        self.depth = depth
        self.parent = parent
        # the NEXT_ASSEMBLY_USAGE_OCCURRENCE that placed this instance, and
        # the root-to-leaf tuple of them; a root node has None and ()
        self.nauo_id = nauo_id
        self.nauo_chain = nauo_chain

    def path(self):
        names, n = [], self
        while n is not None:
            names.append(n.product)
            n = n.parent
        return "/".join(reversed(names))

    def walk(self):
        yield self
        for c in self.children:
            for n in c.walk():
                yield n

    def __repr__(self):
        return "<Node %s @%s>" % (self.product, self.world.t.fmt(2))


# --------------------------------------------------------------------------
# structural analysis mixed into StepFile
# --------------------------------------------------------------------------

_SURFACE_TYPES = (
    "PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE", "TOROIDAL_SURFACE",
    "SPHERICAL_SURFACE", "B_SPLINE_SURFACE_WITH_KNOTS", "B_SPLINE_SURFACE",
    "SURFACE_OF_REVOLUTION", "SURFACE_OF_LINEAR_EXTRUSION", "OFFSET_SURFACE",
    "RECTANGULAR_TRIMMED_SURFACE", "DEGENERATE_TOROIDAL_SURFACE",
)

_SOLID_TYPES = ("MANIFOLD_SOLID_BREP", "BREP_WITH_VOIDS",
                "SHELL_BASED_SURFACE_MODEL", "FACETED_BREP")


def _face_of(sfile, fid):
    e = sfile.get(fid)
    if e is None or e.type not in ("ADVANCED_FACE", "FACE_SURFACE"):
        return None
    surf_id = e.p(2)
    se = sfile.get(surf_id)
    if se is None:
        return None
    stype = se.type
    for t in se.types:
        if t in _SURFACE_TYPES:
            stype = t
            break
    frame = radius = radius2 = half_angle = None
    if stype in ("PLANE", "CYLINDRICAL_SURFACE", "CONICAL_SURFACE",
                 "TOROIDAL_SURFACE", "SPHERICAL_SURFACE"):
        frame = sfile.axis2(se.p(1))
        if stype == "CYLINDRICAL_SURFACE":
            radius = float(se.p(2, 0.0))
        elif stype == "SPHERICAL_SURFACE":
            radius = float(se.p(2, 0.0))
        elif stype == "CONICAL_SURFACE":
            radius = float(se.p(2, 0.0))
            half_angle = math.degrees(float(se.p(3, 0.0)))
        elif stype == "TOROIDAL_SURFACE":
            radius = float(se.p(2, 0.0))
            radius2 = float(se.p(3, 0.0))
    sense = e.p(3, True)
    if not isinstance(sense, bool):
        sense = str(sense).upper() not in ("F", "FALSE")
    return Face(int(fid), stype, int(surf_id), frame, radius, radius2,
                half_angle, sense, owner=sfile)


def _solids(self):
    """All solids in the file, keyed by entity id."""
    if "solids" in self._cache:
        return self._cache["solids"]
    out = []
    for e in self.of_type(*_SOLID_TYPES):
        shell_ids = []
        for param in e.params:
            if isinstance(param, Ref):
                shell_ids.append(int(param))
            elif isinstance(param, list):
                shell_ids += [int(v) for v in param if isinstance(v, Ref)]
        faces = []
        for sid in shell_ids:
            sh = self.get(sid)
            if sh is None or sh.type not in ("CLOSED_SHELL", "OPEN_SHELL",
                                             "CONNECTED_FACE_SET"):
                continue
            flist = sh.p(1)
            if not isinstance(flist, list):
                flist = next((x for x in sh.params if isinstance(x, list)), [])
            for fid in flist:
                if not isinstance(fid, Ref):
                    continue
                f = _face_of(self, fid)
                if f is not None:
                    faces.append(f)
        out.append(Solid(e.id, str(e.p(0) or ""), shell_ids[0] if shell_ids else None,
                         faces, self))
    out.sort(key=lambda s: s.id)
    self._cache["solids"] = out
    return out


def _face_vertices(self, fid):
    """Every ``CARTESIAN_POINT`` reachable from a face's edge loops."""
    e = self.get(fid)
    if e is None:
        return []
    pts = []
    bounds = e.p(1)
    if not isinstance(bounds, list):
        bounds = next((x for x in e.params if isinstance(x, list)), [])
    for b in bounds:
        if not isinstance(b, Ref):
            continue
        be = self.get(b)
        if be is None:
            continue
        loop = self.get(be.p(1))
        if loop is None:
            continue
        oel = loop.p(1)
        if not isinstance(oel, list):
            oel = next((x for x in loop.params if isinstance(x, list)), [])
        for oe in oel:
            if not isinstance(oe, Ref):
                continue
            oee = self.get(oe)
            if oee is None:
                continue
            ec = self.get(oee.p(3)) if oee.type == "ORIENTED_EDGE" else oee
            if ec is None:
                continue
            for vref in (ec.p(1), ec.p(2)):
                v = self.get(vref)
                if v is not None and v.type == "VERTEX_POINT":
                    p = self.point3(v.p(1))
                    if p is not None:
                        pts.append(p)
    return pts


def _solid_bounds(self, solid):
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    for f in solid.faces:
        for p in self.face_vertices(f.id):
            for i in range(3):
                if p[i] < lo[i]:
                    lo[i] = p[i]
                if p[i] > hi[i]:
                    hi[i] = p[i]
    if lo[0] == float("inf"):
        return None
    return Vec(lo), Vec(hi)


def _vertex_bounds(self):
    """Bounds over every VERTEX_POINT in the file (cheap whole-model extent)."""
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = 0
    for e in self.of_type("VERTEX_POINT"):
        p = self.point3(e.p(1))
        if p is None:
            continue
        seen += 1
        for i in range(3):
            if p[i] < lo[i]:
                lo[i] = p[i]
            if p[i] > hi[i]:
                hi[i] = p[i]
    if not seen:
        return None
    return Vec(lo), Vec(hi)


def _world_bounds(self):
    """Assembly-space bounds from solid vertices, transformed per instance.

    Vertex-based, so silhouettes of curved faces can fall slightly outside;
    use the OCC tier (``--exact``) when the true extent matters.
    """
    lo = [float("inf")] * 3
    hi = [float("-inf")] * 3
    seen = False
    for root in self.assembly():
        for node in root.walk():
            for solid in node.solids:
                b = self.solid_bounds(solid)
                if b is None:
                    continue
                (l, h) = b
                corners = [Vec(x, y, z) for x in (l[0], h[0])
                           for y in (l[1], h[1]) for z in (l[2], h[2])]
                for c in corners:
                    w = node.world.apply(c)
                    seen = True
                    for i in range(3):
                        if w[i] < lo[i]:
                            lo[i] = w[i]
                        if w[i] > hi[i]:
                            hi[i] = w[i]
    if not seen:
        return self.vertex_bounds()
    return Vec(lo), Vec(hi)


def _products(self):
    """``{product_definition_id: (name, description)}``."""
    if "products" in self._cache:
        return self._cache["products"]
    out = {}
    for pd in self.of_type("PRODUCT_DEFINITION"):
        name = desc = ""
        pdf = self.get(pd.p(2))
        if pdf is not None and pdf.type == "PRODUCT_DEFINITION_FORMATION":
            prod = None
            for r in self.refs_out(pdf.id):
                cand = self.get(r)
                if cand is not None and cand.type == "PRODUCT":
                    prod = cand
                    break
            if prod is not None:
                name = str(prod.p(0) or "")
                desc = str(prod.p(1) or "")
        if not name:
            name = str(pd.p(0) or "?")
            desc = str(pd.p(1) or "")
        out[pd.id] = (name, desc)
    self._cache["products"] = out
    return out


def _instance_transform(self, nauo_id):
    """Local transform for a NEXT_ASSEMBLY_USAGE_OCCURRENCE, or None."""
    if "nauo_xf" not in self._cache:
        # PRODUCT_DEFINITION_SHAPE -> its definition (the NAUO)
        pds_for = {}
        for pds in self.of_type("PRODUCT_DEFINITION_SHAPE"):
            d = pds.p(2)
            if isinstance(d, Ref):
                pds_for[int(d)] = pds.id
        xf = {}
        for cd in self.of_type("CONTEXT_DEPENDENT_SHAPE_REPRESENTATION"):
            rel, pds = cd.p(0), cd.p(1)
            rel_e = self.get(rel)
            pds_e = self.get(pds)
            if rel_e is None or pds_e is None:
                continue
            args = rel_e.sub("REPRESENTATION_RELATIONSHIP_WITH_TRANSFORMATION")
            if not args:
                continue
            itd = self.get(args[0])
            if itd is None or itd.type != "ITEM_DEFINED_TRANSFORMATION":
                continue
            frm = self.axis2(itd.p(2))
            to = self.axis2(itd.p(3))
            if frm is None or to is None:
                continue
            t = Transform.from_frame(frm).inverse() * Transform.from_frame(to)
            target = pds_e.p(2)
            if isinstance(target, Ref):
                xf[int(target)] = t
        self._cache["nauo_xf"] = xf
    return self._cache["nauo_xf"].get(int(nauo_id))


def _shape_reps(self):
    """``{product_definition_id: [representation ids]}`` incl. related breps."""
    if "shape_reps" in self._cache:
        return self._cache["shape_reps"]
    direct = {}
    for sdr in self.of_type("SHAPE_DEFINITION_REPRESENTATION"):
        pds = self.get(sdr.p(0))
        rep = sdr.p(1)
        if pds is None or not isinstance(rep, Ref):
            continue
        tgt = pds.p(2)
        if isinstance(tgt, Ref):
            direct.setdefault(int(tgt), []).append(int(rep))
    # plain (non-transforming) SHAPE_REPRESENTATION_RELATIONSHIP links a
    # SHAPE_REPRESENTATION to the ADVANCED_BREP_SHAPE_REPRESENTATION holding
    # the actual solids.
    linked = {}
    for e in self.of_type("SHAPE_REPRESENTATION_RELATIONSHIP"):
        if len(e.types) > 1:
            continue  # transforming variant: that is assembly placement
        a, b = e.p(2), e.p(3)
        if isinstance(a, Ref) and isinstance(b, Ref):
            linked.setdefault(int(a), []).append(int(b))
            linked.setdefault(int(b), []).append(int(a))
    out = {}
    for pd, reps in direct.items():
        acc, stack, seen = [], list(reps), set()
        while stack:
            r = stack.pop()
            if r in seen:
                continue
            seen.add(r)
            acc.append(r)
            stack.extend(linked.get(r, []))
        out[pd] = acc
    self._cache["shape_reps"] = out
    return out


def _product_solids(self):
    """``{product_definition_id: [Solid]}``."""
    if "product_solids" in self._cache:
        return self._cache["product_solids"]
    by_id = {s.id: s for s in self.solids()}
    out = {}
    for pd, reps in self.shape_reps().items():
        found = []
        for r in reps:
            re_ = self.get(r)
            if re_ is None:
                continue
            items = re_.p(1)
            if not isinstance(items, list):
                items = next((x for x in re_.params if isinstance(x, list)), [])
            for item in items:
                if isinstance(item, Ref) and int(item) in by_id:
                    s = by_id[int(item)]
                    if s not in found:
                        found.append(s)
        out[pd] = found
    for pd, ss in out.items():
        for s in ss:
            if s.product is None:
                s.product = pd
    self._cache["product_solids"] = out
    return out


def _assembly(self):
    """Build the product tree with accumulated world transforms."""
    if "assembly" in self._cache:
        return self._cache["assembly"]
    products = self.products()
    psolids = self.product_solids()
    edges = []
    for nauo in self.of_type("NEXT_ASSEMBLY_USAGE_OCCURRENCE"):
        parent, child = nauo.p(3), nauo.p(4)
        if not isinstance(parent, Ref) or not isinstance(child, Ref):
            continue
        edges.append((nauo.id, int(parent), int(child), str(nauo.p(0) or "")))
    children = {}
    has_parent = set()
    for nid, parent, child, label in edges:
        children.setdefault(parent, []).append((nid, child, label))
        has_parent.add(child)
    roots = [pd for pd in products if pd not in has_parent]
    if not roots and products:
        roots = [min(products)]

    def build(pd, instance, local, world, depth, parent, chain, nid, nauos):
        name, desc = products.get(pd, ("?", ""))
        node = AssemblyNode(name, desc, instance, pd, local, world, depth,
                            parent, nid, nauos)
        node.solids = psolids.get(pd, [])
        if pd in chain:
            return node
        chain = chain | {pd}
        for cnid, child, label in sorted(children.get(pd, []),
                                         key=lambda t: products.get(t[1], ("",))[0]):
            t = self.instance_transform(cnid) or Transform.identity()
            node.children.append(
                build(child, label, t, world * t, depth + 1, node, chain,
                      cnid, nauos + (cnid,)))
        return node

    tree = [build(r, products.get(r, ("?", ""))[0], Transform.identity(),
                  Transform.identity(), 0, None, frozenset(), None, ())
            for r in sorted(roots)]
    self._cache["assembly"] = tree
    return tree


def _colors(self):
    """``{styled entity id: (r, g, b, style_name)}`` with 0-1 components."""
    if "colors" in self._cache:
        return self._cache["colors"]
    out = {}

    def find_rgb(eid, depth=0):
        e = self.get(eid)
        if e is None or depth > 8:
            return None
        if e.type == "COLOUR_RGB":
            return (float(e.p(1, 0.0)), float(e.p(2, 0.0)), float(e.p(3, 0.0)),
                    str(e.p(0) or ""))
        if e.type.endswith("_COLOUR") and e.type != "COLOUR_RGB":
            nested = [r for r in self.refs_out(e.id)]
            for r in nested:
                got = find_rgb(r, depth + 1)
                if got:
                    return got
            return None
        for r in self.refs_out(e.id):
            got = find_rgb(r, depth + 1)
            if got:
                return got
        return None

    def style_name(eid, depth=0):
        e = self.get(eid)
        if e is None or depth > 8:
            return ""
        if e.type == "FILL_AREA_STYLE" and e.p(0):
            return str(e.p(0))
        for r in self.refs_out(e.id):
            got = style_name(r, depth + 1)
            if got:
                return got
        return ""

    for si in self.of_type("STYLED_ITEM", "OVER_RIDING_STYLED_ITEM"):
        target = si.params[-1] if si.params else None
        if not isinstance(target, Ref):
            continue
        styles = si.p(1) or []
        rgb = None
        for s in styles:
            rgb = find_rgb(s)
            if rgb:
                nm = style_name(s) or rgb[3]
                out[int(target)] = (rgb[0], rgb[1], rgb[2], nm)
                break
    self._cache["colors"] = out
    return out


StepFile.solids = _solids
StepFile.face_vertices = _face_vertices
StepFile.solid_bounds = _solid_bounds
StepFile.vertex_bounds = _vertex_bounds
StepFile.world_bounds = _world_bounds
StepFile.products = _products
StepFile.instance_transform = _instance_transform
StepFile.shape_reps = _shape_reps
StepFile.product_solids = _product_solids
StepFile.assembly = _assembly
StepFile.colors = _colors


def load(path):
    """Parse *path* and return a :class:`StepFile`."""
    return StepFile(path)


if __name__ == "__main__":
    import sys
    import time
    t0 = time.time()
    sf = load(sys.argv[1])
    print("%s: %d entities in %.2fs" % (sf.path, len(sf.entities), time.time() - t0))
    print("schema:", sf.schema, "units:", sf.units())
    for t, n in sf.type_census()[:10]:
        print("  %-42s %d" % (t, n))
