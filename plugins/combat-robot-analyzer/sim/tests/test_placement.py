"""Unit tests for strike-direction selection.

Pure geometry over Tier 0 bounding boxes, so every case here is a small
hand-built assembly with a known right answer.
"""

from __future__ import annotations

import math
from typing import Any, ClassVar

import pytest

from case.placement import (
    BLOCK_COVERAGE,
    SPAN_ASPECT_RATIO,
    SPAN_EXCLUSION_DEG,
    Approach,
    PlacementError,
    _span_axis,
    approach_candidates,
    bbox_corners,
    choose_approach,
)


def part(
    part_id: str,
    lo: tuple[float, float, float],
    hi: tuple[float, float, float],
    volume: float = 1000.0,
) -> dict[str, Any]:
    return {"id": part_id, "bbox": {"min": list(lo), "max": list(hi)}, "volume_mm3": volume}


def choose(parts, targets, **kw) -> Approach:
    kw.setdefault("tooth_width", 10.0)
    kw.setdefault("standoff", 0.5)
    return choose_approach(parts, targets, **kw)


class TestBboxCorners:
    def test_min_max_form(self):
        c = bbox_corners({"min": [0, 0, 0], "max": [1, 2, 3]})
        assert len(c) == 8
        assert (0, 0, 0) in c and (1, 2, 3) in c

    def test_named_key_form(self):
        c = bbox_corners({"xmin": 0, "ymin": 0, "zmin": 0, "xmax": 1, "ymax": 1, "zmax": 1})
        assert len(c) == 8

    def test_flat_sequence_form(self):
        assert len(bbox_corners([0, 0, 0, 1, 1, 1])) == 8

    def test_unusable_box_is_rejected(self):
        with pytest.raises(PlacementError, match="bounding box"):
            bbox_corners({"min": [0, None, 0], "max": [1, 1, 1]})


class TestCandidates:
    def test_sweeps_around_the_shortest_axis(self):
        # Flat bot: z is shortest, so the sweep is in the xy plane.
        dirs = approach_candidates([300.0, 500.0, 100.0])
        in_plane = [d for d in dirs if abs(d[2]) < 1e-9]
        assert len(in_plane) == 32
        # Plus the vertical pair, so a vertical spinner can still win.
        assert (0.0, 0.0, 1.0) in dirs and (0.0, 0.0, -1.0) in dirs

    def test_all_candidates_are_unit_length(self):
        for d in approach_candidates([300.0, 500.0, 100.0]):
            assert math.sqrt(sum(c * c for c in d)) == pytest.approx(1.0)

    def test_is_deterministic(self):
        assert approach_candidates([1.0, 2.0, 3.0]) == approach_candidates([1.0, 2.0, 3.0])

    def test_bad_extent_is_rejected(self):
        with pytest.raises(PlacementError, match="extent"):
            approach_candidates([1.0, 2.0])


class TestReach:
    def test_an_exposed_panel_is_struck_from_outside(self):
        # Armour on the +x face of a chassis behind it.
        parts = [
            part("p000", (-50, -50, -10), (0, 50, 10)),  # chassis
            part("p001", (0, -50, -10), (10, 50, 10)),  # armour, outermost
        ]
        got = choose(parts, ["p001"])
        # It must come from the armoured side, not through the chassis. The
        # exact azimuth is not pinned: a corner strike on a full-width panel
        # is unobstructed too, so over-specifying it would encode nothing real.
        assert got.direction[0] > 0.0
        assert got.exposure > 0.0

    def test_a_bar_is_struck_at_its_tip(self):
        # Bar along x; the tip at +x is the furthest point.
        parts = [
            part("p000", (-10, -10, -10), (10, 10, 10)),
            part("p001", (10, -5, -5), (200, 5, 5)),
        ]
        got = choose(parts, ["p001"])
        assert got.direction[0] == pytest.approx(1.0)

    def test_strike_point_lands_on_an_off_axis_target(self):
        """The tooth has to hit the part, not the plane the part touches.

        Regression: inertial-v6's side panel is 9.5 mm thick and centred at
        z = -11.2. Aiming at ``direction * reach`` put the tooth at z = 0, so
        it flew past and the solve recorded a clean miss with zero internal
        energy -- a confidently empty report.
        """
        lo, hi = (-30.0, -139.5, -16.0), (30.0, 389.5, -6.5)
        got = choose([part("p000", lo, hi)], ["p000"])
        assert got.direction == pytest.approx((0.0, 1.0, 0.0))
        # Inside the panel's own z range, not at the origin.
        assert lo[2] <= got.strike_point[2] <= hi[2]
        assert got.strike_point[1] == pytest.approx(got.reach)

    def test_strike_point_is_on_the_target_surface(self):
        # Its projection along the strike direction equals the reach.
        parts = [part("p000", (10.0, 20.0, 30.0), (40.0, 60.0, 90.0))]
        got = choose(parts, ["p000"])
        proj = sum(got.strike_point[i] * got.direction[i] for i in range(3))
        assert proj == pytest.approx(got.reach)

    def test_reach_is_the_projection_of_the_extreme_corner(self):
        parts = [part("p000", (0, -5, -5), (100, 5, 5))]
        got = choose(parts, ["p000"])
        assert got.reach == pytest.approx(100.0)
        assert got.strike_point[0] == pytest.approx(100.0)


class TestObstruction:
    def test_a_buried_part_cannot_be_struck(self):
        # Target at the centre, shell all around it.
        parts = [
            part("p000", (-1, -1, -1), (1, 1, 1)),  # buried target
            part("p001", (-50, -50, -50), (-40, 50, 50)),
            part("p002", (40, -50, -50), (50, 50, 50)),
            part("p003", (-50, -50, -50), (50, -40, 50)),
            part("p004", (-50, 40, -50), (50, 50, 50)),
            part("p005", (-50, -50, 40), (50, 50, 50)),
            part("p006", (-50, -50, -50), (50, 50, -40)),
        ]
        with pytest.raises(PlacementError, match="enclosed"):
            choose(parts, ["p000"])

    def test_a_part_further_out_sideways_does_not_hide_the_face(self):
        # The chassis reaches as far in y as the panel does, but not in x, so
        # the outward face wins on exposure even though y has more reach.
        parts = [
            part("p000", (-50, -50, -10), (0, 50, 10)),
            part("p001", (0, -50, -10), (10, 50, 10)),
        ]
        got = choose(parts, ["p001"])
        assert got.direction[0] > 0.0
        assert got.exposure > 0.0


class TestSymmetry:
    def test_a_symmetric_target_reports_an_arbitrary_azimuth(self):
        # A disc centred on the origin reaches equally in every direction.
        parts = [part("p000", (-100, -100, -5), (100, 100, 5))]
        got = choose(parts, ["p000"])
        assert got.arbitrary

    def test_an_asymmetric_target_does_not(self):
        parts = [part("p000", (0, -5, -5), (100, 5, 5))]
        assert not choose(parts, ["p000"]).arbitrary

    def test_the_tie_break_is_reproducible(self):
        parts = [part("p000", (-100, -100, -5), (100, 100, 5))]
        assert choose(parts, ["p000"]).direction == choose(parts, ["p000"]).direction


class TestSpanExclusion:
    def test_a_bar_is_not_struck_on_its_end_cap(self):
        # Bar along x. Without exclusion the tip wins; with it, the strike
        # comes across the span instead.
        parts = [part("p000", (-200, -5, -5), (200, 5, 5))]
        along = choose(parts, ["p000"])
        across = choose(parts, ["p000"], exclude_span=True)
        assert abs(along.direction[0]) == pytest.approx(1.0)
        assert abs(across.direction[0]) <= math.cos(math.radians(SPAN_EXCLUSION_DEG))

    def test_exclusion_only_removes_the_span_directions(self):
        parts = [part("p000", (-200, -5, -5), (200, 5, 5))]
        got = choose(parts, ["p000"], exclude_span=True)
        # Whatever wins, it is not within 20 degrees of the bar's own axis.
        assert abs(got.direction[0]) <= math.cos(math.radians(SPAN_EXCLUSION_DEG))


class TestSpanExclusionAppliesOnlyToBars:
    """The rule protects a bar's length. Nothing else has a span to protect.

    Every dimension here is a real corpus part. A disc weapon whose longest
    axis lies in its own plane was made unstrikeable by the old rule, which
    excluded exactly the rim strike a drum tooth arrives on.
    """

    #: (name, half-extents, is_bar). Sorted extents e0<=e1<=e2; a bar needs
    #: e2/e1 >= 2 *and* a roughly square cross-section, e1/e0 < 2.
    SHAPES = (
        # Derive Mk 1 v41 p153 — 4.0 x 32.8 x 30.1 disc, e2/e1 = 1.09
        ("disc", (2.0, 16.4, 15.05), False),
        # 000-PBK-Mk2.1 p026 — 6.4 x 89.9 x 42.7 plate. Elongated 2.11:1, so
        # an elongation-only test calls it a bar; its 6.7:1 cross-section does
        # not. This is the case that decides the shape of the rule.
        ("plate", (3.2, 44.95, 21.35), False),
        # inertial-v6 p036 — 60.3 x 529.0 x 9.5 blade, very long but flat
        ("blade", (30.15, 264.5, 4.75), False),
        ("cube", (20.0, 20.0, 20.0), False),
        ("bar", (200.0, 5.0, 5.0), True),
    )

    @pytest.mark.parametrize(("name", "half", "is_bar"), SHAPES)
    def test_only_a_bar_loses_directions(self, name, half, is_bar):
        lo = tuple(-h for h in half)
        parts = [part("p000", lo, half)]
        free = choose(parts, ["p000"])
        held = choose(parts, ["p000"], exclude_span=True)
        if is_bar:
            assert held.direction != free.direction, f"{name}: exclusion did nothing"
        else:
            assert held.direction == free.direction, (
                f"{name}: exclusion changed the strike on a part with no span"
            )

    @pytest.mark.parametrize(
        ("name", "half", "expect"),
        [
            ("disc", (2.0, 16.4, 15.05), None),
            ("plate", (3.2, 44.95, 21.35), None),
            ("blade", (30.15, 264.5, 4.75), None),
            ("cube", (20.0, 20.0, 20.0), None),
            ("bar along x", (200.0, 5.0, 5.0), (1.0, 0.0, 0.0)),
            ("bar along y", (5.0, 200.0, 5.0), (0.0, 1.0, 0.0)),
            ("bar along z", (5.0, 5.0, 200.0), (0.0, 0.0, 1.0)),
        ],
    )
    def test_which_axis_is_protected(self, name, half, expect):
        # The classifier on its own. The end-to-end path runs through the
        # occlusion test, which is separately too crude to build a meaningful
        # fixture against -- see docs/history/target-selection-fixes.md phase B.
        assert _span_axis([((0.0, 0.0, 0.0), half)]) == expect

    def test_a_degenerate_box_protects_nothing(self):
        # A zero extent would make the ratio test divide by zero or read as
        # infinitely elongated; neither should exclude a direction.
        assert _span_axis([((0.0, 0.0, 0.0), (0.0, 10.0, 10.0))]) is None

    def test_the_boundary_is_inclusive_on_elongation(self):
        # e2/e1 exactly at the threshold counts as a bar; just under does not.
        assert _span_axis([((0.0, 0.0, 0.0), (10.0, 10.0, 20.0))]) == (0.0, 0.0, 1.0)
        assert _span_axis([((0.0, 0.0, 0.0), (10.0, 10.0, 19.9))]) is None

    def test_a_square_cross_section_is_what_makes_a_bar(self):
        # Same 4:1 elongation, different cross-sections. Only the square one
        # is a bar; this is the distinction 000-PBK-Mk2.1's plate turns on.
        assert _span_axis([((0.0, 0.0, 0.0), (5.0, 5.0, 20.0))]) == (0.0, 0.0, 1.0)
        assert _span_axis([((0.0, 0.0, 0.0), (1.0, 5.0, 20.0))]) is None

    def test_the_threshold_is_the_documented_one(self):
        assert SPAN_ASPECT_RATIO == 2.0


class TestOcclusion:
    """What counts as standing in the way.

    The old test asked whether two circles centred on the parts overlapped,
    with each circle sized by the part's *longest* perpendicular dimension. A
    long thin rail therefore blocked everything, and any overlap at all
    blocked outright. Both are fixed here: real rectangles, and a coverage
    threshold.
    """

    def test_a_thin_rail_beside_a_panel_does_not_block_it(self):
        # The regression this phase exists for. The rail is 20 mm wide and
        # 400 long, well clear of the panel in x, and stands further out in z.
        # Modelled as a circle of its length it swallowed the whole bot.
        panel = part("p000", (100, -50, 0), (200, 50, 10))
        rail = part("p001", (0, -200, 20), (20, 200, 40))
        got = choose([panel, rail], ["p000"])
        assert got.direction is not None
        assert choose([panel], ["p000"]).exposure == pytest.approx(got.exposure)

    def test_a_part_fully_behind_a_bigger_plate_is_still_refused(self):
        # Enclosure has to keep working; this is what the threshold protects.
        inner = part("p000", (50, 50, 50), (60, 60, 60))
        hull = part("p001", (0, 0, 0), (110, 110, 110))
        with pytest.raises(PlacementError, match="enclosed by other parts"):
            choose([inner, hull], ["p000"])

    def test_a_flush_neighbour_does_not_block(self):
        # BLOCK_MARGIN_MM exists for exactly this: a frame sitting level with
        # the panel it holds obstructs nothing a blunt tooth cares about.
        panel = part("p000", (0, 0, 0), (100, 100, 10))
        frame = part("p001", (0, 0, 0), (100, 100, 10.5))
        assert choose([panel, frame], ["p000"]).direction is not None

    # A tall narrow post: +z reaches 100 where any in-plane direction reaches
    # about 24, so +z wins outright and the shadow over it actually decides
    # something. On a wide flat panel the diagonal always wins and a test
    # phrased about +z passes without ever exercising it.
    POST = ((0.0, 0.0, 0.0), (20.0, 20.0, 100.0))

    def test_a_cover_pushes_the_strike_off_it(self):
        # Not by sliding the aim point across a fixed approach -- by tilting
        # the approach until the cover is out of the way, which scoring on
        # exposed reach makes the cheaper option. Heavier cover, more tilt.
        post = part("p000", *self.POST)
        clear = choose([post], ["p000"]).direction[2]
        half = choose([post, part("p001", (0, 0, 110), (10, 20, 120))], ["p000"]).direction[2]
        most = choose([post, part("p001", (0, 0, 110), (19, 20, 120))], ["p000"]).direction[2]
        assert clear > half > most, (
            f"tilt should grow with cover, got {clear:.3f} {half:.3f} {most:.3f}"
        )

    def test_whatever_is_chosen_is_mostly_exposed(self):
        from case.placement import BLOCK_COVERAGE, _ellipsoid, _shadow, _support

        post = part("p000", *self.POST)
        cover = part("p001", (0, 0, 110), (10, 20, 120))
        got = choose([post, cover], ["p000"])
        te = [_ellipsoid(post["bbox"])]
        oe = [_ellipsoid(cover["bbox"])]
        covered, _ = _shadow(te, oe, got.direction, _support(te[0], got.direction))
        assert covered < BLOCK_COVERAGE

    def test_coverage_past_the_threshold_rejects_the_direction(self):
        post = part("p000", *self.POST)
        cover = part("p001", (0, 0, 110), (19, 20, 120))  # 95% of the footprint
        got = choose([post, cover], ["p000"])
        assert got.direction[2] != pytest.approx(1.0), "should not strike through the cover"

    def test_shadow_measures_area_not_contact(self):
        from case.placement import _ellipsoid, _shadow, _support

        target = [_ellipsoid({"min": [0, 0, 0], "max": [20, 20, 100]})]
        along = (0.0, 0.0, 1.0)
        reach = _support(target[0], along)
        # An ellipsoid inscribed in the box, so compare against itself: a
        # blocker with the same footprint standing further out hides all of it.
        same = [_ellipsoid({"min": [0, 0, 110], "max": [20, 20, 120]})]
        covered, exposed = _shadow(target, same, along, reach)
        assert covered == pytest.approx(1.0)
        assert exposed is None
        # Half the footprint, half the shadow.
        half = [_ellipsoid({"min": [0, 0, 110], "max": [10, 20, 120]})]
        covered, exposed = _shadow(target, half, along, reach)
        assert covered == pytest.approx(0.5)
        assert exposed is not None
        # Nothing standing further out at all.
        assert _shadow(target, [], along, reach)[0] == 0.0

    def test_sliding_moves_across_the_strike_but_not_along_it(self):
        from case.placement import _slide_to_exposed

        along = (0.0, 0.0, 1.0)
        start = (10.0, 10.0, 100.0)
        # _shadow reports the exposed centre in (u, v) coordinates on the
        # plane perpendicular to the strike, so feed it the same way.
        from case.placement import _dot, _perpendicular_basis

        u, v = _perpendicular_basis(along)
        target = (15.0, 10.0, 100.0)
        moved = _slide_to_exposed(start, along, (_dot(target, u), _dot(target, v)))
        assert moved[2] == pytest.approx(start[2]), "depth along the strike is untouched"
        assert moved[0] == pytest.approx(15.0)
        assert moved[1] == pytest.approx(10.0)

    def test_sliding_a_target_with_nothing_exposed_leaves_it_alone(self):
        from case.placement import _slide_to_exposed

        start = (1.0, 2.0, 3.0)
        assert _slide_to_exposed(start, (0.0, 0.0, 1.0), None) == start

    def test_two_overlapping_blockers_are_not_double_counted(self):
        from case.placement import _ellipsoid, _shadow, _support

        target = [_ellipsoid({"min": [0, 0, 0], "max": [20, 20, 100]})]
        along = (0.0, 0.0, 1.0)
        reach = _support(target[0], along)
        blockers = [
            _ellipsoid({"min": [0, 0, 110], "max": [12, 20, 120]}),
            _ellipsoid({"min": [8, 0, 110], "max": [20, 20, 120]}),
        ]
        # 60% + 60% of the footprint, overlapping over 8..12 -- the union is
        # the whole of it, and a naive sum would report 120%.
        assert _shadow(target, blockers, along, reach)[0] == pytest.approx(1.0)

    def test_the_threshold_is_the_documented_one(self):
        assert BLOCK_COVERAGE == 0.8


class TestRejections:
    def test_unknown_target_id(self):
        with pytest.raises(PlacementError, match="no geometry"):
            choose([part("p000", (0, 0, 0), (1, 1, 1))], ["p999"])

    def test_multiple_targets_use_their_union(self):
        parts = [
            part("p000", (0, -5, -5), (10, 5, 5)),
            part("p001", (90, -5, -5), (100, 5, 5)),
        ]
        got = choose(parts, ["p000", "p001"])
        assert got.reach == pytest.approx(100.0)


class TestDescription:
    def test_describe_names_the_direction(self):
        parts = [part("p000", (0, -5, -5), (100, 5, 5))]
        text = choose(parts, ["p000"]).describe()
        assert "+1.00" in text and "0.50 mm out" in text

    def test_describe_discloses_an_arbitrary_azimuth(self):
        parts = [part("p000", (-100, -100, -5), (100, 100, 5))]
        assert "arbitrary" in choose(parts, ["p000"]).describe()


class TestSnapToSurface:
    """The repair for the defect that made 38 of 44 corpus runs vacuous.

    Automatic placement aims at the support point of the ellipsoid inscribed
    in a bounding box. That point is inside the box, and the box is already
    bigger than the part, so for an oblique approach the strike lands in the
    void between the two — measured at up to 170 mm from any material.
    """

    def _approach(self, point, direction=(0.0, 0.0, 1.0)):
        from case.placement import Approach

        return Approach(
            direction=direction,
            strike_point=point,
            standoff=0.5,
            reach=73.2,
            exposure=0.84,
            arbitrary=False,
            part_id="p000",
            solid_index=None,
        )

    def _box(self):
        from tests.conftest import box_triangles

        return box_triangles((0.0, 0.0, 0.0), (40.0, 300.0, 12.0))

    def test_a_point_floating_in_the_void_lands_on_the_material(self):
        from case.placement import snap_to_surface

        got = snap_to_surface(self._approach((20.0, 150.0, 60.0)), self._box())
        assert got.strike_point == pytest.approx((20.0, 150.0, 12.0))

    def test_a_point_already_on_the_surface_does_not_move(self):
        from case.placement import snap_to_surface

        got = snap_to_surface(self._approach((20.0, 150.0, 12.0)), self._box())
        assert got.strike_point == pytest.approx((20.0, 150.0, 12.0))

    def test_a_point_buried_inside_is_brought_out_to_the_face(self):
        from case.placement import snap_to_surface

        got = snap_to_surface(self._approach((20.0, 150.0, 6.0)), self._box())
        assert got.strike_point == pytest.approx((20.0, 150.0, 12.0))

    def test_the_direction_and_everything_else_survive(self):
        """Snapping fixes the distance along the approach, not the approach."""
        from case.placement import snap_to_surface

        before = self._approach((20.0, 150.0, 60.0))
        got = snap_to_surface(before, self._box())
        assert got.direction == before.direction
        assert got.reach == before.reach
        assert got.exposure == before.exposure
        assert got.solid_index == before.solid_index

    def test_an_approach_that_misses_is_left_for_the_gate_to_refuse(self):
        """A line that never meets the part is a direction failure.

        Snapping cannot repair it, and quietly moving the strike somewhere
        else would hide a case that needs a human to re-aim.
        """
        from case.placement import snap_to_surface

        before = self._approach((500.0, 150.0, 60.0))
        assert snap_to_surface(before, self._box()) == before

    def test_no_surface_is_not_a_crash(self):
        from case.placement import snap_to_surface

        before = self._approach((20.0, 150.0, 60.0))
        assert snap_to_surface(before, []) == before


class TestUnboxableBodiesKeepTheirIndex:
    """`solid_bboxes` carries a `None` for a body Tier 0 could not box, so the
    position of every other body keeps naming the same solid. `solid_index`
    is handed to `loadcase`, which uses it to pick which solid to mesh, so
    renumbering around a gap picks a different body than the one scored.
    """

    @staticmethod
    def _part(boxes):
        return {
            "id": "p900",
            "solids": len(boxes),
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [60.0, 10.0, 10.0]},
            "solid_bboxes": boxes,
            "volume_mm3": 1000.0,
        }

    A: ClassVar[dict[str, list[float]]] = {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}
    C: ClassVar[dict[str, list[float]]] = {"min": [50.0, 0.0, 0.0], "max": [60.0, 10.0, 10.0]}

    def test_the_index_survives_a_gap(self):
        """Only body 2 is boxed, so that is the only body it can pick — and it
        must say 2, not 0."""
        got = choose([self._part([None, None, self.C])], ["p900"])
        assert got.solid_index == 2

    def test_a_part_whose_bodies_are_all_unboxable_falls_back_to_the_part(self):
        """`solid_index` None is the documented 'strike the part as a whole',
        which `_struck_solid` then refuses for a multi-body target rather than
        meshing a guess."""
        got = choose([self._part([None, None])], ["p900"])
        assert got.solid_index is None

    def test_a_boxed_body_is_still_scored_normally(self):
        got = choose([self._part([self.A, None, self.C])], ["p900"])
        assert got.solid_index in (0, 2)
