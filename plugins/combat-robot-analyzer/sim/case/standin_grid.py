"""Structured grids for stand-in boxes, so a stand-in can never sliver.

`docs/history/mesh-sliver-anatomy.md` attributes 11 of 35 case minima to
stand-in boxes -- geometry with no feature below millimetres producing
0.01 mm elements. Two mechanisms, both artefacts of unstructured meshing:
crossing-edge slivers spanning a thin slab whose two big faces were meshed
independently, and 1D nodes duplicated microns from a box corner. A
transfinite (structured) grid removes both by construction: every node sits
on a lattice, so no element can be smaller than the lattice allows.

The fragment in `loadcase._add_standins` complicates this: a box whose face
is partially covered by the target or another body comes out of the boolean
with that face *split*, and a volume without box topology (6 four-sided
faces, 8 corners) cannot be meshed transfinite. Such volumes are reported
back as fallbacks; `loadcase` caps their mesh size below the slab-spanning
threshold instead. A face *fully* shared with the target is fine -- the
structured grid dictates that face's mesh and the target's unstructured
volume conforms to it.

Split pure-from-gmsh the same way `simplify.py` split policy from kernel:
`plan_grid` is pure and unit-tested; `read_topology` and
`structure_standins` are the only functions that touch a gmsh session.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "ASPECT_CAP",
    "BoxTopology",
    "GridPlan",
    "GridResult",
    "plan_grid",
    "read_topology",
    "structure_standins",
]

#: A grid cell may be at most this many times wider than the box is thin.
#: Uncapped, a 1.5 mm slab gridded at the 6 mm stand-in size gets cells of
#: 1.5 x 6 x 6 whose tets fall below the 0.3 mm floor the grid exists to
#: protect. The value is a starting point for the mesh-algo harness to
#: judge, not a measured optimum.
ASPECT_CAP = 4.0


@dataclass(frozen=True)
class BoxTopology:
    """One stand-in volume that still has box topology after the fragment."""

    volume: int
    #: Smallest bounding-box extent -- the slab thickness for a thin box.
    thin_mm: float
    faces: tuple[int, ...]
    #: Face tag -> its four boundary curves.
    face_curves: Mapping[int, tuple[int, int, int, int]]
    #: Curve tag -> its two endpoint tags.
    curve_points: Mapping[int, tuple[int, int]]
    #: Curve tag -> straight-line length in mm.
    curve_lengths: Mapping[int, float]


@dataclass(frozen=True)
class GridPlan:
    """Transfinite declarations for every qualifying box, ready to apply."""

    #: Curve tag -> transfinite node count (endpoints included).
    counts: Mapping[int, int]
    faces: tuple[int, ...]
    volumes: tuple[int, ...]


@dataclass(frozen=True)
class GridResult:
    """What `structure_standins` did, for sizing and for the record."""

    structured: tuple[int, ...]
    #: Volume tag -> thinnest extent, for the caller's size-cap fallback.
    fallback: Mapping[int, float]


def _bbox_extents(gmsh: Any, dim: int, tag: int) -> tuple[float, float, float]:
    x0, y0, z0, x1, y1, z1 = gmsh.model.getBoundingBox(dim, tag)
    return (x1 - x0, y1 - y0, z1 - z0)


def read_topology(gmsh: Any, volume: int) -> BoxTopology | None:
    """The volume's box topology, or ``None`` if the fragment broke it.

    Disqualification is the *expected* path for a box the target overlaps:
    the boolean splits the touched face and the volume stops being a
    hexahedron. Curve lengths are bounding-box diagonals, exact for the
    straight edges a box has; a curved edge would measure slightly short,
    which only shifts a node count by one.
    """
    faces = _distinct(gmsh.model.getBoundary([(3, volume)], combined=False, oriented=False), 2)
    if faces is None or len(faces) != 6:
        return None

    face_curves: dict[int, tuple[int, int, int, int]] = {}
    curve_points: dict[int, tuple[int, int]] = {}
    curve_lengths: dict[int, float] = {}
    corners: set[int] = set()
    for f in faces:
        curves = _distinct(gmsh.model.getBoundary([(2, f)], combined=False, oriented=False), 1)
        if curves is None or len(curves) != 4:
            return None
        face_points: set[int] = set()
        for c in curves:
            if c not in curve_points:
                points = _distinct(
                    gmsh.model.getBoundary([(1, c)], combined=False, oriented=False), 0
                )
                if points is None or len(points) != 2:
                    return None  # a seam or closed edge; not a box edge
                curve_points[c] = (points[0], points[1])
                dx, dy, dz = _bbox_extents(gmsh, 1, c)
                curve_lengths[c] = (dx * dx + dy * dy + dz * dz) ** 0.5
            face_points.update(curve_points[c])
        if len(face_points) != 4:
            return None
        face_curves[f] = (curves[0], curves[1], curves[2], curves[3])
        corners.update(face_points)
    if len(corners) != 8:
        return None

    return BoxTopology(
        volume=volume,
        thin_mm=min(_bbox_extents(gmsh, 3, volume)),
        faces=tuple(faces),
        face_curves=face_curves,
        curve_points=curve_points,
        curve_lengths=curve_lengths,
    )


def _distinct(entries: Sequence[tuple[int, int]], dim: int) -> list[int] | None:
    """Unsigned, deduplicated tags of one dimension; ``None`` on a stray."""
    tags: list[int] = []
    for d, tag in entries:
        if d != dim:
            return None
        t = abs(int(tag))
        if t not in tags:
            tags.append(t)
    return tags


def plan_grid(
    boxes: Sequence[BoxTopology],
    cell_mm: float,
    *,
    aspect_cap: float = ASPECT_CAP,
) -> GridPlan:
    """Node counts that are consistent across every shared curve and face.

    Two constraints meet here. A curve wants ``length / cell_mm`` intervals,
    but no fewer than ``length / (aspect_cap * thin)`` so a thin slab's
    in-plane cells stay near the slab's own scale. And a transfinite face
    needs *opposite* curves to agree exactly, which couples counts across a
    face and -- through shared curves -- across neighbouring boxes. The
    coupling is resolved by raising each opposite pair to its maximum until
    nothing changes; counts only ever increase and are bounded by the
    largest initial count, so the loop terminates.
    """
    if cell_mm <= 0:
        raise ValueError("cell_mm must be positive")

    counts: dict[int, int] = {}
    for box in boxes:
        for c, length in box.curve_lengths.items():
            floor_intervals = (
                math.ceil(length / (aspect_cap * box.thin_mm)) if box.thin_mm > 0 else 1
            )
            intervals = max(1, round(length / cell_mm), floor_intervals)
            counts[c] = max(counts.get(c, 0), intervals + 1)

    changed = True
    while changed:
        changed = False
        for box in boxes:
            for curves in box.face_curves.values():
                for a, b in _opposite_pairs(curves, box.curve_points):
                    n = max(counts[a], counts[b])
                    if counts[a] != n or counts[b] != n:
                        counts[a] = counts[b] = n
                        changed = True

    faces = tuple(dict.fromkeys(f for box in boxes for f in box.faces))
    return GridPlan(counts=counts, faces=faces, volumes=tuple(b.volume for b in boxes))


def _opposite_pairs(
    curves: tuple[int, int, int, int],
    curve_points: Mapping[int, tuple[int, int]],
) -> list[tuple[int, int]]:
    """The two pairs of curves in a four-sided face that share no endpoint."""
    first = curves[0]
    rest = list(curves[1:])
    p0 = set(curve_points[first])
    opposite = next(c for c in rest if not p0 & set(curve_points[c]))
    rest.remove(opposite)
    return [(first, opposite), (rest[0], rest[1])]


def structure_standins(gmsh: Any, volumes: Sequence[int], cell_mm: float) -> GridResult:
    """Declare transfinite grids on every stand-in that still is a box.

    Declarations only -- meshing happens in ``generate_mesh`` as before.
    The plan is computed in full before the first gmsh call, so a volume
    that disqualifies can never leave half a grid behind.
    """
    boxes: list[BoxTopology] = []
    fallback: dict[int, float] = {}
    for v in volumes:
        topo = read_topology(gmsh, v)
        if topo is None:
            fallback[int(v)] = min(_bbox_extents(gmsh, 3, v))
        else:
            boxes.append(topo)

    plan = plan_grid(boxes, cell_mm)
    for curve, n in plan.counts.items():
        gmsh.model.mesh.setTransfiniteCurve(curve, n)
    for face in plan.faces:
        gmsh.model.mesh.setTransfiniteSurface(face)
    for volume in plan.volumes:
        gmsh.model.mesh.setTransfiniteVolume(volume)

    return GridResult(structured=plan.volumes, fallback=fallback)
