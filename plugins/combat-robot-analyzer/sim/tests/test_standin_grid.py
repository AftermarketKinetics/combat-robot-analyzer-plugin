"""Unit tests for the transfinite stand-in planner.

No gmsh: the planner is pure, and the reader/applier take the module as an
argument, so a fake entity graph pins the behaviour that matters -- what
qualifies as a box after the fragment, how counts stay consistent across
shared faces, and that a disqualified volume never leaves half a grid behind.
"""

from __future__ import annotations

import types

import pytest

from case.standin_grid import (
    ASPECT_CAP,
    BoxTopology,
    plan_grid,
    read_topology,
    structure_standins,
)


class _Builder:
    """A fake gmsh whose model is an explicit box entity graph."""

    def __init__(self) -> None:
        self._next = 1
        self.points: dict[int, tuple[float, float, float]] = {}
        self.curves: dict[int, tuple[int, int]] = {}
        self.faces: dict[int, list[int]] = {}
        self.volumes: dict[int, list[int]] = {}
        self.transfinite_curves: dict[int, int] = {}
        self.transfinite_faces: list[int] = []
        self.transfinite_volumes: list[int] = []
        mesh = types.SimpleNamespace(
            setTransfiniteCurve=lambda tag, n: self.transfinite_curves.__setitem__(tag, n),
            setTransfiniteSurface=lambda tag: self.transfinite_faces.append(tag),
            setTransfiniteVolume=lambda tag: self.transfinite_volumes.append(tag),
        )
        self.model = types.SimpleNamespace(
            getBoundary=self._boundary,
            getBoundingBox=self._bbox,
            mesh=mesh,
        )

    def _tag(self) -> int:
        self._next += 1
        return self._next - 1

    def point(self, xyz: tuple[float, float, float]) -> int:
        for tag, existing in self.points.items():
            if existing == xyz:
                return tag
        tag = self._tag()
        self.points[tag] = xyz
        return tag

    def curve(self, a: int, b: int) -> int:
        for tag, ends in self.curves.items():
            if set(ends) == {a, b}:
                return tag
        tag = self._tag()
        self.curves[tag] = (a, b)
        return tag

    def face(self, corners: list[tuple[float, float, float]]) -> int:
        pts = [self.point(c) for c in corners]
        edges = sorted(self.curve(pts[i], pts[(i + 1) % 4]) for i in range(4))
        for tag, existing in self.faces.items():
            if sorted(existing) == edges:
                return tag
        tag = self._tag()
        self.faces[tag] = [self.curve(pts[i], pts[(i + 1) % 4]) for i in range(4)]
        return tag

    def box(self, lo: tuple[float, float, float], hi: tuple[float, float, float]) -> int:
        """A hexahedron; coincident faces/curves/points are shared, like a
        snapped fragment leaves them."""
        (x0, y0, z0), (x1, y1, z1) = lo, hi
        quads = [
            [(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)],
            [(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)],
            [(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)],
            [(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)],
            [(x0, y0, z0), (x1, y0, z0), (x1, y1, z0), (x0, y1, z0)],
            [(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)],
        ]
        tag = self._tag()
        self.volumes[tag] = [self.face(q) for q in quads]
        return tag

    def _boundary(
        self, dim_tags: list[tuple[int, int]], combined: bool = False, oriented: bool = False
    ) -> list[tuple[int, int]]:
        (dim, tag) = dim_tags[0]
        if dim == 3:
            return [(2, f) for f in self.volumes[tag]]
        if dim == 2:
            return [(1, c) for c in self.faces[tag]]
        return [(0, p) for p in self.curves[tag]]

    def _points_of(self, dim: int, tag: int) -> list[tuple[float, float, float]]:
        if dim == 1:
            return [self.points[p] for p in self.curves[tag]]
        if dim == 2:
            return [q for c in self.faces[tag] for q in self._points_of(1, c)]
        return [q for f in self.volumes[tag] for q in self._points_of(2, f)]

    def _bbox(self, dim: int, tag: int) -> tuple[float, ...]:
        pts = self._points_of(dim, tag)
        los = [min(p[i] for p in pts) for i in range(3)]
        his = [max(p[i] for p in pts) for i in range(3)]
        return (*los, *his)


def _topo(g: _Builder, volume: int) -> BoxTopology:
    """``read_topology`` narrowed: these tests build boxes that must qualify."""
    topo = read_topology(g, volume)
    assert topo is not None, "the fixture box should have box topology"
    return topo


class TestReadTopology:
    def test_a_clean_box_qualifies(self):
        g = _Builder()
        v = g.box((0, 0, 0), (10, 20, 40))
        topo = read_topology(g, v)
        assert topo is not None
        assert len(topo.faces) == 6
        assert len(topo.curve_lengths) == 12
        assert topo.thin_mm == 10
        assert sorted(topo.curve_lengths.values()).count(10) == 4

    def test_a_split_face_disqualifies(self):
        # The fragment splitting one face is the expected failure: the volume
        # gains a seventh face and stops being transfinite-able.
        g = _Builder()
        v = g.box((0, 0, 0), (10, 10, 10))
        extra = g.face([(0, 0, 0), (5, 0, 0), (5, 10, 0), (0, 10, 0)])
        g.volumes[v].append(extra)
        assert read_topology(g, v) is None

    def test_a_closed_edge_disqualifies(self):
        # A curve with one endpoint is a seam (cylinder against the box),
        # not a box edge.
        g = _Builder()
        v = g.box((0, 0, 0), (10, 10, 10))
        first_curve = g.faces[g.volumes[v][0]][0]
        a, _ = g.curves[first_curve]
        g.curves[first_curve] = (a, a)
        assert read_topology(g, v) is None


class TestPlanGrid:
    def test_counts_follow_the_cell_size(self):
        g = _Builder()
        topo = _topo(g, g.box((0, 0, 0), (10, 20, 40)))
        plan = plan_grid([topo], 6.0)
        by_length = {round(topo.curve_lengths[c]): n for c, n in plan.counts.items()}
        # intervals = round(length / cell): 10 -> 2, 20 -> 3, 40 -> 7.
        assert by_length == {10: 3, 20: 4, 40: 8}

    def test_the_aspect_cap_keeps_slab_cells_near_the_slab_scale(self):
        # A 1.5 mm slab gridded at 6 mm would get 1.5 x 6 x 6 cells; the cap
        # forces the in-plane pitch down to aspect_cap * thickness.
        g = _Builder()
        topo = _topo(g, g.box((0, 0, 0), (1.5, 24, 24)))
        plan = plan_grid([topo], 6.0)
        for c, n in plan.counts.items():
            length = topo.curve_lengths[c]
            if length > 1.5:
                pitch = length / (n - 1)
                assert pitch <= ASPECT_CAP * 1.5 + 1e-9

    def test_shared_curves_propagate_through_opposite_pairs(self):
        # A thin slab shares a face with a cube. The slab's aspect cap
        # refines the shared curves; a transfinite face needs opposite
        # curves equal, so the refinement must propagate through the cube
        # to its far face -- the coupling plan_grid exists to resolve.
        g = _Builder()
        slab = _topo(g, g.box((0, 0, 0), (1.0, 24, 24)))
        cube = _topo(g, g.box((1.0, 0, 0), (25, 24, 24)))
        plan = plan_grid([slab, cube], 6.0)
        for c in cube.curve_lengths:
            p0, p1 = (g.points[p] for p in cube.curve_points[c])
            direction = [abs(a - b) > 1e-9 for a, b in zip(p0, p1, strict=True)]
            n = plan.counts[c]
            if direction[0]:  # cube's own x edges: unaffected, 24/6 = 4
                assert n == 5
            else:  # y and z edges: forced by the slab through opposite pairs
                assert n == 7

    def test_a_zero_cell_is_refused(self):
        with pytest.raises(ValueError):
            plan_grid([], 0.0)


class TestStructureStandins:
    def test_qualifying_boxes_are_declared_and_split_ones_fall_back(self):
        g = _Builder()
        clean = g.box((0, 0, 0), (10, 20, 40))
        split = g.box((20, 0, 0), (30, 10, 3))
        g.volumes[split].append(g.face([(20, 0, 0), (25, 0, 0), (25, 10, 0), (20, 10, 0)]))

        result = structure_standins(g, [clean, split], 6.0)

        assert result.structured == (clean,)
        assert result.fallback == {split: 3.0}
        assert set(g.transfinite_volumes) == {clean}
        assert len(g.transfinite_faces) == 6
        assert len(g.transfinite_curves) == 12

    def test_a_disqualified_volume_leaves_no_declarations_at_all(self):
        # The plan is computed before the first gmsh call; a half-declared
        # grid would fail the whole mesh, not one box.
        g = _Builder()
        split = g.box((0, 0, 0), (10, 10, 10))
        g.volumes[split].append(g.face([(0, 0, 0), (5, 0, 0), (5, 10, 0), (0, 10, 0)]))
        result = structure_standins(g, [split], 6.0)
        assert result.structured == ()
        assert not g.transfinite_curves
        assert not g.transfinite_faces
        assert not g.transfinite_volumes
