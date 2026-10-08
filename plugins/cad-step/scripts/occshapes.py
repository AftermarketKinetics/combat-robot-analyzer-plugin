#!/usr/bin/env python3
"""OpenCASCADE-backed loading and measurement helpers.

Import this only after :func:`occenv.ensure_occ` has run.  Everything here
speaks plain Python tuples so the tier-1 (`stepcore`) and tier-2 code can be
mixed freely.
"""

from __future__ import annotations

import math

from OCC.Core.BRep import BRep_Tool
from OCC.Core.BRepBndLib import brepbndlib
from OCC.Core.BRepGProp import brepgprop
from OCC.Core.Bnd import Bnd_Box
from OCC.Core.GProp import GProp_GProps
from OCC.Core.IFSelect import IFSelect_RetDone
from OCC.Core.Quantity import Quantity_Color, Quantity_TOC_RGB
from OCC.Core.STEPCAFControl import STEPCAFControl_Reader
from OCC.Core.STEPControl import STEPControl_Reader
from OCC.Core.TDF import TDF_Label, TDF_LabelSequence
from OCC.Core.TDocStd import TDocStd_Document
from OCC.Core.TopAbs import (TopAbs_EDGE, TopAbs_FACE, TopAbs_SHELL,
                             TopAbs_SOLID, TopAbs_VERTEX)
from OCC.Core.TopExp import TopExp_Explorer
from OCC.Core.TopLoc import TopLoc_Location
from OCC.Core.TopoDS import TopoDS_Shape
from OCC.Core.XCAFDoc import (XCAFDoc_ColorCurv, XCAFDoc_ColorGen,
                              XCAFDoc_ColorSurf, XCAFDoc_DocumentTool)


_PLACEHOLDER = ("COMPOUND", "SOLID", "SHELL", "UNNAMED", "")


def _is_real_name(name):
    """XCAF hands out placeholders (COMPOUND, SOLID, ``=>[0:1:1:14]``) for
    shapes the exporter never labelled."""
    if not name:
        return False
    if name.upper() in _PLACEHOLDER:
        return False
    return not name.startswith("=>") and not name.startswith("[0:")


class Part(object):
    """One leaf shape of an assembly, already located in world coordinates.

    ``entity`` and ``nauo_chain`` are the STEP entity ids recovered from the
    transfer (see :func:`entity_resolver`); they are what lets a caller join
    this part against the text tier's :class:`stepcore.AssemblyNode` without
    going through names.  Both are ``None``/empty when the recovery is
    unavailable, so nothing may depend on them being present.
    """

    __slots__ = ("name", "path", "shape", "color", "entity", "nauo_chain",
                 "world")

    def __init__(self, name, path, shape, color, entity=None, nauo_chain=(),
                 world=None):
        self.name = name
        self.path = path
        self.shape = shape
        self.color = color
        self.entity = entity
        self.nauo_chain = nauo_chain
        self.world = world

    def __repr__(self):
        return "<Part %s>" % self.path


def _trsf_rows(loc):
    """A ``TopLoc_Location`` as a 3x4 row-major tuple, or ``None`` if identity."""
    if loc.IsIdentity():
        return ((1.0, 0.0, 0.0, 0.0), (0.0, 1.0, 0.0, 0.0), (0.0, 0.0, 1.0, 0.0))
    t = loc.Transformation()
    return tuple(tuple(t.Value(i, j) for j in (1, 2, 3, 4)) for i in (1, 2, 3))


def entity_resolver(reader):
    """``shape -> (step_entity_id, step_type_name)`` for a transferred document.

    ``STEPCAFControl_Reader`` keeps the underlying ``STEPControl_Reader`` and
    its work session alive after ``Transfer``, and the session's transfer
    reader remembers which STEP entity produced each shape.  A component
    label's located shape resolves to the ``NEXT_ASSEMBLY_USAGE_OCCURRENCE``
    that placed it -- the only occurrence-unique key a STEP file offers -- and
    a leaf's own shape resolves to its ``MANIFOLD_SOLID_BREP`` or shape
    representation.

    Returns a function, or ``None`` when this OCCT build does not expose the
    plumbing (it has moved between versions).  Callers must treat ``None`` as
    "fall back to matching by geometry".
    """
    try:
        from OCC.Core.StepData import StepData_StepModel
        session = reader.ChangeReader().WS()
        transfer = session.TransferReader()
        model = StepData_StepModel.DownCast(session.Model())
        if transfer is None or model is None:
            return None
        model.IdentLabel  # attribute check before we promise anything
    except Exception:
        return None

    def resolve(shape):
        if shape is None or shape.IsNull():
            return None, None
        try:
            ent = transfer.EntityFromShapeResult(shape, 1)
            if ent is None:
                return None, None
            return int(model.IdentLabel(ent)), ent.DynamicType().Name()
        except Exception:
            # an unmapped shape (XCAF builds compounds of its own) raises
            # rather than returning null on some builds
            return None, None

    return resolve


def load_shape(path):
    """Whole file as a single compound (fast; no names or colours)."""
    reader = STEPControl_Reader()
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise IOError("could not read STEP file: %s" % path)
    reader.TransferRoots()
    return reader.OneShape()


def load_parts(path, with_colors=True, with_entities=True):
    """Walk the XCAF assembly and return located :class:`Part` leaves.

    ``with_entities`` additionally recovers each part's originating STEP
    entity ids (see :func:`entity_resolver`); it costs one map lookup per
    label and never fails the load.

    NOTE: ``TDocStd_Document`` must be handed a plain ``str``.  Passing a
    ``TCollection_ExtendedString`` aborts the interpreter (OCCT 7.9).
    """
    doc = TDocStd_Document("step-doc")
    shape_tool = XCAFDoc_DocumentTool.ShapeTool(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool(doc.Main())

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(with_colors)
    reader.SetNameMode(True)
    reader.SetLayerMode(True)
    if reader.ReadFile(path) != IFSelect_RetDone:
        raise IOError("could not read STEP file: %s" % path)
    reader.Transfer(doc)

    resolve = entity_resolver(reader) if with_entities else None

    def entity_of(shape, want=None):
        if resolve is None:
            return None
        eid, etype = resolve(shape)
        # An identity-located component resolves to the product's own solid
        # rather than to the NAUO, so the caller has to say what it wants.
        if want is not None and etype != want:
            return None
        return eid

    parts = []

    def color_of(label, shape):
        c = Quantity_Color(0.6, 0.6, 0.6, Quantity_TOC_RGB)
        kinds = (XCAFDoc_ColorSurf, XCAFDoc_ColorGen, XCAFDoc_ColorCurv)
        for kind in kinds:
            try:
                if color_tool.GetInstanceColor(shape, kind, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception:
                pass
        for kind in kinds:
            try:
                if color_tool.GetColor(label, kind, c):
                    return (c.Red(), c.Green(), c.Blue())
            except Exception:
                pass
        return None

    def recurse(label, loc, trail, chain):
        name = label.GetLabelName() or "unnamed"
        trail = trail + [name]
        if shape_tool.IsAssembly(label):
            comps = TDF_LabelSequence()
            shape_tool.GetComponents(label, comps)
            for i in range(1, comps.Length() + 1):
                child = comps.Value(i)
                if shape_tool.IsReference(child):
                    referred = TDF_Label()
                    shape_tool.GetReferredShape(child, referred)
                    child_loc = loc.Multiplied(shape_tool.GetLocation(child))
                    cname = child.GetLabelName()
                    nauo = entity_of(shape_tool.GetShape(child),
                                     "StepRepr_NextAssemblyUsageOccurrence")
                    recurse(referred, child_loc,
                            trail[:-1] + [cname] if cname else trail,
                            chain + (nauo,))
                else:
                    recurse(child, loc, trail, chain)
            return
        shape = shape_tool.GetShape(label)
        if shape is None or shape.IsNull():
            return
        placed = shape.Located(loc) if not loc.IsIdentity() else shape
        # XCAF hands out placeholder names like COMPOUND/SOLID for shapes the
        # exporter did not label; fall back to the nearest real name up the trail
        display = name if _is_real_name(name) else ""
        if not display:
            for cand in reversed(trail[:-1]):
                if _is_real_name(cand):
                    display = cand
                    break
            display = display or name
        parts.append(Part(display, "/".join(trail), placed,
                          color_of(label, shape) if with_colors else None,
                          entity_of(shape), chain, _trsf_rows(loc)))

    roots = TDF_LabelSequence()
    shape_tool.GetFreeShapes(roots)
    for i in range(1, roots.Length() + 1):
        recurse(roots.Value(i), TopLoc_Location(), [], ())
    return parts


# -- traversal --------------------------------------------------------------

def iter_sub(shape, kind):
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        yield exp.Current()
        exp.Next()


def iter_solids(shape):
    return iter_sub(shape, TopAbs_SOLID)


def iter_faces(shape):
    return iter_sub(shape, TopAbs_FACE)


def min_edge_length(shape, ignore_below=1e-9):
    """The shortest real edge in a shape, in model units.

    What sets an explicit solver's timestep is the smallest *element*, and
    what forces a small element is a short edge -- so this is the number a
    cost estimate actually wants.  It is strictly better than the smallest
    hole or fillet for the purpose: a sliver left by a bad export is neither,
    and on a real weapon bar it was a 2 um edge that no feature pass could see.

    Degenerate edges (seams, and the zero-length artefacts some exporters
    leave behind) are skipped -- they constrain no element and would otherwise
    report every model as pathological.  Returns None for a shape with no
    usable edges.
    """
    best = None
    for edge in iter_sub(shape, TopAbs_EDGE):
        props = GProp_GProps()
        brepgprop.LinearProperties(edge, props)
        length = props.Mass()
        if length <= ignore_below:
            continue
        if best is None or length < best:
            best = length
    return best


def count(shape, kind):
    n = 0
    exp = TopExp_Explorer(shape, kind)
    while exp.More():
        n += 1
        exp.Next()
    return n


def is_closed(shape):
    """True when *shape* is made of solids whose every shell closes up.

    An open shell has a volume, it just does not mean anything, and it cannot
    be meshed either -- so this is the cheapest useful sanity gate on a part.
    Note that ``TopoDS_Shape.Closed()`` is no use here: it is a flag nobody
    sets on an imported shape, and reads False on perfectly good solids.
    ``BRep_Tool.IsClosed`` on each shell does the real test, that every edge
    is shared by two faces, and costs about 0.2 ms per part.
    """
    if count(shape, TopAbs_SOLID) == 0:
        return False
    shells = list(iter_sub(shape, TopAbs_SHELL))
    return bool(shells) and all(BRep_Tool.IsClosed(s) for s in shells)


def is_valid(shape):
    """Full ``BRepCheck_Analyzer`` validity -- stricter than :func:`is_closed`,
    and slow enough that callers should make it opt-in."""
    from OCC.Core.BRepCheck import BRepCheck_Analyzer
    return bool(BRepCheck_Analyzer(shape).IsValid())


def topology_census(shape):
    return {
        "solids": count(shape, TopAbs_SOLID),
        "faces": count(shape, TopAbs_FACE),
        "edges": count(shape, TopAbs_EDGE),
        "vertices": count(shape, TopAbs_VERTEX),
    }


# -- measurement ------------------------------------------------------------

def bbox(shape, optimal=False):
    """Axis-aligned bounds. ``optimal=True`` is tighter but much slower.

    Triangulation is deliberately not used.  ``BRepBndLib::Add`` prefers an
    existing mesh when it finds one, so a shape that some earlier step has
    tessellated -- drawing a view of it, say -- would afterwards report a box
    off by as much as the chord deflection: 2 mm on parts measured here.  That
    is invisible until two callers share one set of shapes and get different
    answers depending on the order they ran in.  Reading the geometry costs
    nothing extra on an untessellated shape and cannot drift.
    """
    box = Bnd_Box()
    box.SetGap(0.0)
    if optimal:
        brepbndlib.AddOptimal(shape, box)
    else:
        brepbndlib.Add(shape, box, False)
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def bbox_or_none(shape, optimal=False):
    """:func:`bbox`, but ``None`` for a shape that encloses nothing.

    Assemblies carry construction occurrences that own no solid, and OCC's
    ``Bnd_Box.Get`` raises ``Standard_ConstructionError`` on the void box they
    produce rather than returning anything.  One such occurrence anywhere in
    the file took the whole report down, which is a poor trade for a part that
    has no geometry to describe.  Callers that already treat a missing bbox as
    "not measured" should use this.
    """
    box = Bnd_Box()
    box.SetGap(0.0)
    if optimal:
        brepbndlib.AddOptimal(shape, box)
    else:
        brepbndlib.Add(shape, box, False)
    if box.IsVoid():
        return None
    xmin, ymin, zmin, xmax, ymax, zmax = box.Get()
    return (xmin, ymin, zmin), (xmax, ymax, zmax)


def solid_extents(shape, limit=64):
    """``[((lo), (hi)) or None, ...]`` per solid, or ``None`` for one solid.

    A part exported as several disconnected bodies -- mirrored left/right
    armour, a hub with its teeth -- needs its solids told apart downstream:
    a boundary condition placed on the wrong one holds nothing.  One box per
    solid is enough to do that and costs no tessellation.

    **One entry per solid, in solid order, including the ones that could not
    be boxed.**  That is what lets a consumer use a position in this list as
    the name of a body: the same position indexes :func:`solid_volumes`, and
    the same ordering comes back from a STEP import.  This used to append only
    the boxes it got, so a single unboxable body silently shifted every later
    one -- handing the consumer a list that looked fine and meant a different
    body at every index past the gap.  A bodiless occurrence is exactly the
    case ``bbox_or_none`` exists for, so the gap is reachable, not theoretical.

    ``None`` for a single-solid part, where the part's own bbox already says
    everything, so the common case adds nothing to the report; and ``None``
    when no solid could be boxed at all, which carries no more information
    than the part's own bbox already does.  Capped at ``limit``: past a few
    dozen bodies the part is scrap geometry or a mesh import, and the caller
    learns more from the count than from the boxes.
    """
    boxes = []
    for solid in iter_solids(shape):
        boxes.append(bbox_or_none(solid))
        if len(boxes) > limit:
            return None
    if len(boxes) <= 1 or all(b is None for b in boxes):
        return None
    return boxes


def solid_volumes(shape, limit=64):
    """``[volume, ...]`` per solid, or ``None`` if there is one solid.

    The part's own ``volume_props`` measures the *union* of its bodies, so
    where two bodies overlap -- a pin seated into a package, a boss into a
    plate -- the whole is less than the sum of its parts. A consumer checking
    a per-solid decomposition against the part total is therefore comparing
    two different quantities, and will find a discrepancy that is not an
    error. Measured on a SOT-23-5 package: 5.582057 union against 5.600437
    summed, 0.33% apart, from five pins overlapping the body.

    Same shape and same cap as :func:`solid_extents`, so the two index
    together.
    """
    vols = []
    for solid in iter_solids(shape):
        g = GProp_GProps()
        brepgprop.VolumeProperties(solid, g)
        vols.append(g.Mass())
        if len(vols) > limit:
            return None
    return vols if len(vols) > 1 else None


def volume_props(shape):
    g = GProp_GProps()
    brepgprop.VolumeProperties(shape, g)
    com = g.CentreOfMass()
    m = g.MatrixOfInertia()
    inertia = [[m.Value(i, j) for j in (1, 2, 3)] for i in (1, 2, 3)]
    return {
        "volume": g.Mass(),
        "com": (com.X(), com.Y(), com.Z()),
        "inertia": inertia,
    }


def surface_area(shape):
    g = GProp_GProps()
    brepgprop.SurfaceProperties(shape, g)
    return g.Mass()


def principal_axes(inertia):
    """Jacobi eigen-decomposition of a symmetric 3x3 inertia tensor."""
    a = [row[:] for row in inertia]
    v = [[1.0 if i == j else 0.0 for j in range(3)] for i in range(3)]
    for _ in range(64):
        off = sum(a[i][j] ** 2 for i in range(3) for j in range(3) if i != j)
        if off < 1e-18:
            break
        p, q = 0, 1
        best = -1.0
        for i in range(3):
            for j in range(i + 1, 3):
                if abs(a[i][j]) > best:
                    best, p, q = abs(a[i][j]), i, j
        if best < 1e-18:
            break
        theta = (a[q][q] - a[p][p]) / (2.0 * a[p][q])
        t = (1.0 if theta >= 0 else -1.0) / (abs(theta) + math.sqrt(theta * theta + 1.0))
        c = 1.0 / math.sqrt(t * t + 1.0)
        s = t * c
        for k in range(3):
            akp, akq = a[k][p], a[k][q]
            a[k][p] = c * akp - s * akq
            a[k][q] = s * akp + c * akq
        for k in range(3):
            apk, aqk = a[p][k], a[q][k]
            a[p][k] = c * apk - s * aqk
            a[q][k] = s * apk + c * aqk
        for k in range(3):
            vkp, vkq = v[k][p], v[k][q]
            v[k][p] = c * vkp - s * vkq
            v[k][q] = s * vkp + c * vkq
    vals = [a[i][i] for i in range(3)]
    vecs = [[v[0][i], v[1][i], v[2][i]] for i in range(3)]
    order = sorted(range(3), key=lambda i: vals[i])
    return [vals[i] for i in order], [vecs[i] for i in order]


def mesh(shape, deflection=0.5, angular=0.5):
    """Tessellate in place and yield ``(p0, p1, p2)`` triangles in world space."""
    from OCC.Core.BRepMesh import BRepMesh_IncrementalMesh
    BRepMesh_IncrementalMesh(shape, deflection, False, angular, True)
    for face in iter_faces(shape):
        loc = TopLoc_Location()
        tri = BRep_Tool.Triangulation(face, loc)
        if tri is None:
            continue
        trsf = loc.Transformation()
        pts = [tri.Node(i) for i in range(1, tri.NbNodes() + 1)]
        if not loc.IsIdentity():
            pts = [p.Transformed(trsf) for p in pts]
        for i in range(1, tri.NbTriangles() + 1):
            a, b, c = tri.Triangle(i).Get()
            yield (
                (pts[a - 1].X(), pts[a - 1].Y(), pts[a - 1].Z()),
                (pts[b - 1].X(), pts[b - 1].Y(), pts[b - 1].Z()),
                (pts[c - 1].X(), pts[c - 1].Y(), pts[c - 1].Z()),
            )
