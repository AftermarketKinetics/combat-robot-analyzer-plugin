"""Unit tests for the solid-to-part mapping.

No gmsh, no STEP files — ``map_solids`` is pure, and it is the function that
decides which material lands on which part. The numbers in
``TestAgainstCorpus`` are measured from the real corpus probe.
"""

from __future__ import annotations

import math
import resource
import sys
import types
from typing import Any

import pytest

from case.models import EnergyLevel, OpponentArchetype, WeightClass
from case.placement import Approach
from case.impactor import impactor_spec
from case.simgeom import (
    GeometryError,
    _mesh_failure_message,
    _volume_element_count,
    build_tooth,
    gmsh_session,
    map_solids,
)
from case.tooth import tooth_geometry

# Raising the soft limit needs the hard limit to allow it; skip if it does not.
_, hard = resource.getrlimit(resource.RLIMIT_STACK)
pytestmark_stack = pytest.mark.skipif(
    hard != resource.RLIM_INFINITY, reason="hard RLIMIT_STACK forbids raising the soft limit"
)


def _part(part_id: str, solids: int, volume: float) -> dict[str, Any]:
    return {"id": part_id, "solids": solids, "volume_mm3": volume}


class TestOneToOne:
    def test_single_part_single_solid(self):
        m = map_solids([(1, 100.0)], [_part("p000", 1, 100.0)])
        assert m.tags_by_part == {"p000": [1]}
        assert m.part_by_tag == {1: "p000"}
        assert m.worst_residual == pytest.approx(0.0)

    def test_three_parts_in_order(self):
        solids = [(1, 10.0), (2, 20.0), (3, 30.0)]
        parts = [_part("p000", 1, 10.0), _part("p001", 1, 20.0), _part("p002", 1, 30.0)]
        m = map_solids(solids, parts)
        assert m.part_by_tag == {1: "p000", 2: "p001", 3: "p002"}
        assert m.part_count == 3
        assert m.solid_count == 3


class TestMultiSolidParts:
    def test_part_consuming_several_solids(self):
        # p001 owns three solids; the count comes from the Tier 0 document.
        solids = [(1, 10.0), (2, 5.0), (3, 5.0), (4, 5.0), (5, 40.0)]
        parts = [_part("p000", 1, 10.0), _part("p001", 3, 15.0), _part("p002", 1, 40.0)]
        m = map_solids(solids, parts)
        assert m.tags_by_part["p001"] == [2, 3, 4]
        assert m.part_by_tag[3] == "p001"
        assert m.part_count == 3
        assert m.solid_count == 5

    def test_a_part_may_own_zero_solids(self):
        # A construction-geometry occurrence with no solid body.
        solids = [(1, 10.0)]
        parts = [_part("p000", 0, 0.0), _part("p001", 1, 10.0)]
        m = map_solids(solids, parts)
        assert m.tags_by_part["p000"] == []
        assert m.tags_by_part["p001"] == [1]


class TestBodilessOccurrences:
    """meowtybrain carries four occurrences with no solid and a noise volume."""

    @pytest.mark.parametrize("noise", [1.32e-11, -1.07e-09, -1.48e-11, 0.0])
    def test_noise_volume_with_no_solids_is_accepted(self, noise):
        # Naively dividing by this volume invents a residual out of nothing.
        m = map_solids([(1, 10.0)], [_part("p000", 0, noise), _part("p001", 1, 10.0)])
        assert m.residuals["p000"] == 0.0
        assert m.tags_by_part["p000"] == []

    def test_a_real_volume_with_no_solids_is_still_fatal(self):
        # Claiming a body but receiving no solid is a genuine desync.
        with pytest.raises(GeometryError, match="p000"):
            map_solids([(1, 10.0)], [_part("p000", 0, 500.0), _part("p001", 1, 10.0)])


class TestRejections:
    def test_more_solids_than_accounted_for(self):
        with pytest.raises(GeometryError, match="3 solids but the Tier 0"):
            map_solids([(1, 1.0), (2, 1.0), (3, 1.0)], [_part("p000", 2, 2.0)])

    def test_fewer_solids_than_accounted_for(self):
        with pytest.raises(GeometryError, match="1 solids but the Tier 0"):
            map_solids([(1, 1.0)], [_part("p000", 2, 2.0)])

    def test_volume_disagreement_is_fatal(self):
        # Right count, wrong bodies — exactly the silent mis-map we refuse.
        with pytest.raises(GeometryError, match="does not match the Tier 0 enumeration"):
            map_solids([(1, 10.0), (2, 20.0)], [_part("p000", 1, 20.0), _part("p001", 1, 10.0)])

    def test_missing_part_id(self):
        with pytest.raises(GeometryError, match="no id"):
            map_solids([(1, 1.0)], [{"solids": 1, "volume_mm3": 1.0}])


class TestTolerance:
    def test_float_noise_is_accepted(self):
        # 6e-6 relative is the worst seen across the corpus.
        parts = [_part("p000", 1, 29372.954)]
        m = map_solids([(1, 29372.778)], parts)
        assert m.residuals["p000"] == pytest.approx(5.99e-6, rel=0.1)

    def test_just_inside_tolerance(self):
        map_solids([(1, 1000.05)], [_part("p000", 1, 1000.0)], tolerance=1e-4)

    def test_just_outside_tolerance(self):
        with pytest.raises(GeometryError):
            map_solids([(1, 1000.5)], [_part("p000", 1, 1000.0)], tolerance=1e-4)

    def test_default_accepts_the_worst_corpus_residual(self):
        # Two Seraphs V1 parts sit at 6.0e-5. The default has to clear them
        # with room to spare or a tessellation change breaks every job.
        declared = 1000.0
        m = map_solids([(1, declared * (1 + 6.0e-5))], [_part("p000", 1, declared)])
        assert m.residuals["p000"] == pytest.approx(6.0e-5, rel=0.01)

    def test_default_still_rejects_a_real_mismap(self):
        # A mis-map pairs a part with different bodies entirely.
        with pytest.raises(GeometryError):
            map_solids([(1, 1200.0)], [_part("p000", 1, 1000.0)])

    def test_unmeasured_part_is_accepted_but_flagged(self):
        # cad-step could not measure it, so there is nothing to check against.
        # The ordinal structure still held, so the mapping stands.
        m = map_solids([(1, 5.0)], [{"id": "p000", "solids": 1, "volume_mm3": None}])
        assert m.tags_by_part["p000"] == [1]
        assert m.unverified == ("p000",)
        assert "p000" not in m.residuals

    def test_measured_parts_are_not_flagged(self):
        m = map_solids([(1, 5.0)], [_part("p000", 1, 5.0)])
        assert m.unverified == ()


class TestAgainstCorpus:
    """Measured from inertial-v6.step: 87 solids across 57 parts."""

    def test_opaque_ids_are_unique(self):
        # The whole point: 57 distinct group names, no collisions, where the
        # product names collapse 415 -> 306 on meowtybrain.
        parts = [_part(f"p{i:03d}", 1, float(i + 1)) for i in range(57)]
        solids = [(i + 1, float(i + 1)) for i in range(57)]
        m = map_solids(solids, parts)
        assert len(set(m.tags_by_part)) == 57

    def test_every_solid_is_claimed_exactly_once(self):
        parts = [_part("p000", 1, 10.0), _part("p001", 5, 50.0), _part("p002", 2, 20.0)]
        solids = [(1, 10.0), *[(t, 10.0) for t in range(2, 7)], (7, 10.0), (8, 10.0)]
        m = map_solids(solids, parts)
        claimed = [t for tags in m.tags_by_part.values() for t in tags]
        assert sorted(claimed) == list(range(1, 9))
        assert len(claimed) == len(set(claimed))


@pytestmark_stack
class TestStackLimitRestored:
    """gmsh.initialize() raises RLIMIT_STACK process-wide and never lowers it.

    That is not cosmetic. glibc sizes the default pthread stack from
    RLIMIT_STACK and reads "unlimited" as 2 MB, where an 8 MB limit gives
    8 MB — so leaving it raised *shrinks* the stacks of every thread in every
    child spawned afterwards. The OpenRadioss engine's OpenMP workers segfault
    during startup on 2 MB, which is how a solver crash came to be blamed on
    thread count. The session has to put the limit back.
    """

    @staticmethod
    def _fake_gmsh(monkeypatch):
        """A stand-in that reproduces gmsh's one relevant side effect."""
        gmsh: Any = types.ModuleType("gmsh")
        gmsh.option = types.SimpleNamespace(setNumber=lambda *a: None)

        def initialize():
            gmsh.initialized = True
            resource.setrlimit(resource.RLIMIT_STACK, (resource.RLIM_INFINITY, hard))

        def finalize():
            gmsh.initialized = False

        gmsh.initialize, gmsh.finalize = initialize, finalize
        monkeypatch.setitem(sys.modules, "gmsh", gmsh)
        return gmsh

    @pytest.fixture(autouse=True)
    def _pin_limit(self):
        """Pin a finite limit going in, and undo any leak going out.

        Without this the second test passes vacuously off the first one's
        leaked "unlimited" — which is the exact failure being guarded against.
        """
        original = resource.getrlimit(resource.RLIMIT_STACK)
        resource.setrlimit(resource.RLIMIT_STACK, (8 * 1024 * 1024, hard))
        try:
            yield
        finally:
            resource.setrlimit(resource.RLIMIT_STACK, original)

    def test_limit_restored_after_a_clean_session(self, monkeypatch):
        before = resource.getrlimit(resource.RLIMIT_STACK)
        assert before[0] != resource.RLIM_INFINITY  # the fixture's premise
        self._fake_gmsh(monkeypatch)
        with gmsh_session() as g:
            assert resource.getrlimit(resource.RLIMIT_STACK)[0] == resource.RLIM_INFINITY
            assert g.initialized
        assert resource.getrlimit(resource.RLIMIT_STACK) == before

    def test_limit_restored_when_the_body_raises(self, monkeypatch):
        before = resource.getrlimit(resource.RLIMIT_STACK)
        assert before[0] != resource.RLIM_INFINITY
        self._fake_gmsh(monkeypatch)
        with pytest.raises(ValueError), gmsh_session():
            raise ValueError("meshing blew up")
        assert resource.getrlimit(resource.RLIMIT_STACK) == before


class TestToothPlacement:
    """Where the tooth ends up relative to the part it strikes.

    ``build_tooth`` needs gmsh, but the placement is pure rigid-body maths, so
    a fake ``occ`` that tracks two probe points through the same rotations and
    translations pins it without one. The probes are the chisel edge (built at
    the origin) and the far end of the head (built at ``z = +length``).

    This is a regression guard with a real failure behind it: aiming -Z at the
    approach instead of +Z put 45.7% of the head volume *inside* the armour
    panel on inertial-v6, with the tip 0.08 mm below a surface it was meant to
    clear by 0.08 mm.
    """

    class FakeOcc:
        #: The probes ride on the head, which is the first box built. Cutters
        #: get rotated and shifted too, and applying those to the probes is
        #: exactly what a fake that ignored its dim_tags argument would do.
        HEAD_TAG = 1

        def __init__(self, length: float) -> None:
            self.next_tag = 0
            # (chisel edge, far end of the head) as built, before placement.
            self.probes = [[0.0, 0.0, 0.0], [0.0, 0.0, length]]

        def _owns(self, dim_tags) -> bool:
            return any(tag == self.HEAD_TAG for _dim, tag in dim_tags)

        def addBox(self, *a: float) -> int:
            self.next_tag += 1
            return self.next_tag

        def rotate(self, _dt, cx, cy, cz, ax, ay, az, angle):
            if not self._owns(_dt):
                return
            c, s = math.cos(angle), math.sin(angle)
            for p in self.probes:
                v = [p[0] - cx, p[1] - cy, p[2] - cz]
                dot = ax * v[0] + ay * v[1] + az * v[2]
                cross = [ay * v[2] - az * v[1], az * v[0] - ax * v[2], ax * v[1] - ay * v[0]]
                for i, k in enumerate((ax, ay, az)):
                    p[i] = v[i] * c + cross[i] * s + k * dot * (1.0 - c) + (cx, cy, cz)[i]

        def translate(self, _dt, dx, dy, dz):
            if not self._owns(_dt):
                return
            for p in self.probes:
                p[0] += dx
                p[1] += dy
                p[2] += dz

        def cut(self, objects, _tools):
            return list(objects), None

        def synchronize(self) -> None:
            pass

    @staticmethod
    def _place(direction, standoff=0.5, strike=(0.0, 0.0, 0.0)):
        spec = impactor_spec(
            WeightClass.TWELVE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL
        )
        geom = tooth_geometry(spec)
        occ = TestToothPlacement.FakeOcc(geom.head_length)
        approach = Approach(
            direction=direction,
            strike_point=strike,
            standoff=standoff,
            reach=10.0,
            exposure=10.0,
            arbitrary=False,
        )
        build_tooth(types.SimpleNamespace(model=types.SimpleNamespace(occ=occ)), geom, approach)
        edge, back = occ.probes
        return edge, back, geom, approach

    @pytest.mark.parametrize(
        "direction",
        [(0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)],
    )
    def test_the_edge_stands_one_standoff_outside_the_strike_point(self, direction):
        edge, _, _, ap = self._place(direction)
        along = sum(e * d for e, d in zip(edge, direction, strict=True))
        assert along == pytest.approx(ap.standoff, abs=1e-9)

    @pytest.mark.parametrize(
        "direction",
        [(0.0, 1.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 1.0), (0.0, 0.0, -1.0)],
    )
    def test_the_body_sits_behind_the_edge_not_through_the_target(self, direction):
        """The whole head must be on the outside; only the edge is near."""
        edge, back, geom, _ = self._place(direction)
        edge_along = sum(e * d for e, d in zip(edge, direction, strict=True))
        back_along = sum(b * d for b, d in zip(back, direction, strict=True))
        assert back_along > edge_along
        assert back_along == pytest.approx(edge_along + geom.head_length, abs=1e-9)

    def test_it_travels_towards_the_target(self):
        """Velocity is -direction, so the edge must lead into the surface."""
        edge, back, _, _ = self._place((0.0, 1.0, 0.0), strike=(0.0, 100.0, 0.0))
        assert edge[1] > 100.0  # outside the struck surface
        assert back[1] > edge[1]  # and the body further out still

    def test_an_oblique_approach_places_it_on_the_outside(self):
        d = (0.6, 0.8, 0.0)
        edge, back, geom, ap = self._place(d, strike=(10.0, 20.0, 30.0))
        along_edge = sum((e - s) * k for e, s, k in zip(edge, (10.0, 20.0, 30.0), d, strict=True))
        along_back = sum((b - s) * k for b, s, k in zip(back, (10.0, 20.0, 30.0), d, strict=True))
        assert along_edge == pytest.approx(ap.standoff, abs=1e-9)
        assert along_back == pytest.approx(ap.standoff + geom.head_length, abs=1e-9)


class TestMeshFailureMessages:
    """A mesher failure has to name the part, not a gmsh entity tag.

    gmsh says "Impossible to mesh periodic surface 6056". A submitter cannot
    look 6056 up in their CAD, so the message resolves it to the part that
    owns the face. Measured on a real bearing that stopped a 276-solid
    assembly meshing.
    """

    def _gmsh(self, *, adjacency=(261,), bbox=(0.0, 0.0, 0.0, 9.8, 14.0, 14.0)):
        model = types.SimpleNamespace(
            getAdjacencies=lambda dim, tag: ([*adjacency], []),
            getBoundingBox=lambda dim, tag: bbox,
        )
        return types.SimpleNamespace(model=model)

    def test_surface_tag_resolves_to_the_owning_part(self):
        smap = map_solids([(261, 655.6)], [_part("p-42", 1, 655.6)])
        msg = _mesh_failure_message(
            self._gmsh(), Exception("Impossible to mesh periodic surface 6056"), smap
        )
        assert "p-42" in msg
        assert "6056" in msg
        assert "9.8 x 14.0 x 14.0 mm" in msg

    def test_unresolvable_tag_still_reports_the_tag(self):
        smap = map_solids([(1, 1.0)], [_part("p-1", 1, 1.0)])
        msg = _mesh_failure_message(
            self._gmsh(adjacency=(999,)),
            Exception("Impossible to mesh periodic surface 6056"),
            smap,
        )
        assert "6056" in msg
        assert "p-1" not in msg

    def test_message_without_a_surface_tag_is_passed_through(self):
        msg = _mesh_failure_message(self._gmsh(), Exception("HXT 3D mesh failed"), None)
        assert "HXT 3D mesh failed" in msg

    def test_no_solid_map_does_not_crash(self):
        msg = _mesh_failure_message(
            self._gmsh(), Exception("Impossible to mesh periodic surface 12"), None
        )
        assert "12" in msg

    def test_empty_exception_text_still_produces_a_message(self):
        assert _mesh_failure_message(self._gmsh(), Exception(""), None)


class TestVolumeElementCount:
    """Zero tetrahedra is a failure that does not raise.

    A 617 mm^3 bearing meshed to zero volume elements: gmsh returned, the file
    was written, and nothing flagged it. Counting is the only way to see it.
    """

    def test_counts_tetrahedra(self):
        g = types.SimpleNamespace(
            model=types.SimpleNamespace(
                mesh=types.SimpleNamespace(getElementsByType=lambda t: ([1, 2, 3], []))
            )
        )
        assert _volume_element_count(g) == 3

    def test_zero_when_no_tetrahedra(self):
        g = types.SimpleNamespace(
            model=types.SimpleNamespace(
                mesh=types.SimpleNamespace(getElementsByType=lambda t: ([], []))
            )
        )
        assert _volume_element_count(g) == 0

    def test_zero_when_gmsh_raises(self):
        def boom(_):
            raise RuntimeError("no mesh")

        g = types.SimpleNamespace(
            model=types.SimpleNamespace(mesh=types.SimpleNamespace(getElementsByType=boom))
        )
        assert _volume_element_count(g) == 0


class TestPerSolidVolumes:
    """A part's volume is the union of its bodies, not their sum.

    Measured on a real SOT-23-5 package in the corpus: the part measures
    5.582057 mm^3 as a union, its six solids sum to 5.600437, and the five
    pins overlapping the body account for the 0.33% difference. Checking the
    sum against the union called that an ordering fault and refused two corpus
    models outright, while both kernels agreed on every individual solid to
    4e-10 mm^3.
    """

    BODY = 5.159549
    PIN = 0.088178
    UNION = 5.582057  # what OCC measures for the whole part

    def _solids(self):
        return [(1, self.BODY)] + [(i, self.PIN) for i in range(2, 7)]

    def _part(self, **over):
        part = {
            "id": "p072",
            "solids": 6,
            "volume_mm3": self.UNION,
            "solid_volumes": [self.BODY] + [self.PIN] * 5,
        }
        part.update(over)
        return part

    def test_overlapping_bodies_are_not_an_ordering_fault(self):
        from case.simgeom import map_solids

        got = map_solids(self._solids(), [self._part()])
        assert got.tags_by_part["p072"] == [1, 2, 3, 4, 5, 6]
        assert got.residuals["p072"] < 1e-6

    def test_the_old_sum_against_union_check_would_have_refused_this(self):
        """Guards the regression: without solid_volumes it still fails."""
        import pytest

        from case.simgeom import GeometryError, map_solids

        with pytest.raises(GeometryError, match="solid ordering"):
            map_solids(self._solids(), [self._part(solid_volumes=None)])

    def test_a_genuinely_reordered_solid_is_still_caught(self):
        """The point of the check survives: swap a pin for the body."""
        import pytest

        from case.simgeom import GeometryError, map_solids

        swapped = [(1, self.PIN), (2, self.BODY)] + [(i, self.PIN) for i in range(3, 7)]
        with pytest.raises(GeometryError, match="do not match the Tier 0 enumeration"):
            map_solids(swapped, [self._part()])

    def test_a_per_solid_check_catches_what_a_sum_would_hide(self):
        """Two errors that cancel pass a sum and fail per solid.

        Strictly stronger than what it replaces, which is the reason to prefer
        it beyond fixing the union/sum bug.
        """
        import pytest

        from case.simgeom import GeometryError, map_solids

        cancelling = [(1, self.BODY + 0.5)] + [(i, self.PIN - 0.1) for i in range(2, 7)]
        assert abs(sum(v for _, v in cancelling) - (self.BODY + 5 * self.PIN)) < 1e-9
        with pytest.raises(GeometryError, match="do not match the Tier 0 enumeration"):
            map_solids(cancelling, [self._part()])

    def test_a_count_mismatch_falls_back_rather_than_zipping_wrongly(self):
        """solid_volumes shorter than the solid count is not usable."""
        from case.simgeom import map_solids

        # Falls through to the sum check, which this part passes because the
        # declared volume is the sum rather than the union.
        part = self._part(solid_volumes=[self.BODY], volume_mm3=self.BODY + 5 * self.PIN)
        got = map_solids(self._solids(), [part])
        assert got.tags_by_part["p072"] == [1, 2, 3, 4, 5, 6]


class TestBodilessPartWithVolume:
    """Volume but no solid is not an ordering fault, and should not say so.

    Two corpus models carry one: Subdivide P Mk 1.3 v25 (p139, 140.241 mm^3)
    and Yeetus V3 Final Assm (p008, 49.5221 mm^3). Both were refused with "the
    solid ordering does not match the Tier 0 enumeration", which sends the
    reader looking at an ordering that is fine.
    """

    def test_it_names_the_real_problem(self):
        import pytest

        from case.simgeom import GeometryError, map_solids

        parts = [
            {"id": "p139", "solids": 0, "volume_mm3": 140.241},
            {"id": "p140", "solids": 1, "volume_mm3": 100.0},
        ]
        with pytest.raises(GeometryError) as exc:
            map_solids([(1, 100.0)], parts)
        assert "contributes no solid body" in str(exc.value)
        assert "ordering" not in str(exc.value)

    def test_a_genuinely_empty_occurrence_is_still_fine(self):
        """Construction occurrences own no solid and no meaningful volume."""
        from case.simgeom import map_solids

        parts = [
            {"id": "p000", "solids": 0, "volume_mm3": 1e-12},
            {"id": "p001", "solids": 1, "volume_mm3": 100.0},
        ]
        got = map_solids([(1, 100.0)], parts)
        assert got.tags_by_part["p000"] == []
        assert got.residuals["p000"] == 0.0
