"""Scoping, clamping and deck assembly — the parts that need no gmsh.

The meshing path is exercised in the batch image, where gmsh exists; what is
pinned here is every decision made *around* it, because those are the ones
that silently produce a plausible-looking but wrong model.
"""

from __future__ import annotations

import math
import types
from typing import Any, ClassVar

import pytest

from case import loadcase
from case.weapon import Swing
from case.loadcase import (
    CLAMP_FRACTION,
    MAX_STANDINS,
    MAX_TARGET_VOLUME_DRIFT,
    STANDIN_FUZZ_MM,
    TOOTH_BACK,
    TOOTH_HEAD,
    LoadCaseError,
    _add_standins,
    _body_for_point,
    _clamp_box,
    _with_aim,
    deck_spec,
    order_standins,
    parts_in_scope,
    scope_radius,
)
from case.models import Aim, EnergyLevel, OpponentArchetype, WeightClass
from case.placement import Approach
from case.impactor import impactor_spec
from case.tooth import tooth_geometry

ALUMINIUM = {"density": 2.81e-9, "young": 71700.0, "poisson": 0.33}
STEEL = {"density": 7.83e-9, "young": 207000.0, "poisson": 0.3}
UHMW = {"density": 0.93e-9, "young": 900.0, "poisson": 0.46}

LIBRARY: dict[str, Any] = {
    "aluminium_7075_t6": ALUMINIUM,
    "steel_s7_tool": STEEL,
    "uhmw_pe": UHMW,
}


def _part(part_id: str, lo: tuple[float, ...], hi: tuple[float, ...]) -> dict[str, Any]:
    return {
        "id": part_id,
        "bbox": {"min": [float(v) for v in lo], "max": [float(v) for v in hi]},
    }


class TestScopeRadius:
    def test_the_fastest_material_sets_it(self):
        """Steel outruns aluminium, so steel decides where the boundary is."""
        both = scope_radius([ALUMINIUM, STEEL], 40e-6)
        just_aluminium = scope_radius([ALUMINIUM], 40e-6)
        assert both > just_aluminium
        assert both == pytest.approx(math.sqrt(207000.0 / 7.83e-9) * 40e-6)

    def test_it_scales_with_end_time(self):
        """The coupling that makes end_time and scope one decision."""
        short = scope_radius([ALUMINIUM], 40e-6)
        long = scope_radius([ALUMINIUM], 400e-6)
        assert long == pytest.approx(10.0 * short)
        assert short == pytest.approx(202.0, abs=1.0)  # mm, per PLAN.md §10
        assert long == pytest.approx(2021.0, abs=10.0)

    def test_a_slow_material_does_not_shrink_it(self):
        assert scope_radius([UHMW, STEEL], 40e-6) == scope_radius([STEEL], 40e-6)

    def test_no_usable_material_is_an_error_not_a_zero_radius(self):
        with pytest.raises(LoadCaseError):
            scope_radius([{"density": 0.0, "young": 0.0}], 40e-6)

    def test_a_non_positive_end_time_is_rejected(self):
        with pytest.raises(ValueError):
            scope_radius([STEEL], 0.0)


class TestPartsInScope:
    PARTS: ClassVar[list[dict[str, Any]]] = [
        _part("p000", (0, 0, 0), (10, 10, 10)),  # contains the strike point
        _part("p001", (50, 0, 0), (60, 10, 10)),  # 40 mm away
        _part("p002", (500, 0, 0), (510, 10, 10)),  # 490 mm away
    ]

    def test_distance_is_measured_to_the_box_not_its_centre(self):
        kept = parts_in_scope(self.PARTS, (5.0, 5.0, 5.0), 45.0)
        assert kept == ["p000", "p001"]  # p001's near face is 40 mm off

    def test_a_short_radius_keeps_only_what_it_reaches(self):
        assert parts_in_scope(self.PARTS, (5.0, 5.0, 5.0), 1.0) == ["p000"]

    def test_the_target_survives_a_radius_that_excludes_it(self):
        """Otherwise a slow material could scope away the thing being hit."""
        kept = parts_in_scope(self.PARTS, (5.0, 5.0, 5.0), 1.0, always=["p002"])
        assert kept[0] == "p002"
        assert "p000" in kept

    def test_the_target_is_not_duplicated(self):
        kept = parts_in_scope(self.PARTS, (5.0, 5.0, 5.0), 1000.0, always=["p001"])
        assert sorted(kept) == ["p000", "p001", "p002"]
        assert len(kept) == len(set(kept))

    def test_a_part_with_no_bbox_is_skipped_rather_than_crashing(self):
        parts = [*self.PARTS, {"id": "p003"}]
        assert "p003" not in parts_in_scope(parts, (5.0, 5.0, 5.0), 1000.0)


class TestClampBox:
    """The tooth travels along -direction, so the face to hold is ahead of it.

    ``rel_box`` reads ``xmax`` as "at or below this fraction of the range",
    which is why the two branches are not symmetric. Getting this backwards
    clamps the face the tooth is about to hit.
    """

    def test_approach_from_positive_y_holds_the_low_end(self):
        assert _clamp_box((0.0, 1.0, 0.0)) == {"ymax": CLAMP_FRACTION}

    def test_approach_from_negative_y_holds_the_high_end(self):
        assert _clamp_box((0.0, -1.0, 0.0)) == {"ymin": 1.0 - CLAMP_FRACTION}

    def test_the_dominant_axis_wins(self):
        assert _clamp_box((0.1, -0.2, 0.95)) == {"zmax": CLAMP_FRACTION}


class TestDeckSpec:
    APPROACH = Approach(
        direction=(0.0, 1.0, 0.0),
        strike_point=(5.0, 10.0, 5.0),
        standoff=0.2,
        reach=10.0,
        exposure=10.0,
        arbitrary=False,
    )

    _R = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL)
    SWING = Swing(hub=(5.0, 10.0 + _R.r_arc_mm, 5.0), axis=(0.0, 0.0, 1.0),
                  omega=_R.v_tip_ms * 1000.0 / _R.r_arc_mm, radius_mm=_R.r_arc_mm,
                  tip_velocity=(0.0, -1.0, 0.0), v_tip_ms=_R.v_tip_ms)
    REST = {"mass": 2.0e-3, "com": [0.0, -50.0, 0.0], "inertia": [1.0, 1.0, 1.0, 0.0, 0.0, 0.0]}

    def _spec(self, scoped: list[str], **kw: Any) -> dict[str, Any]:
        impactor = impactor_spec(
            WeightClass.TWELVE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL
        )
        return deck_spec(
            case="lc2",
            target_id="p000",
            standin_ids=[p for p in scoped if p != "p000"],
            materials=dict.fromkeys(scoped, "aluminium_7075_t6"),
            library=LIBRARY,
            tooth=tooth_geometry(impactor),
            approach=self.APPROACH,
            impactor=impactor,
            end_time=40e-6,
            swing=self.SWING,
            rest=self.REST,
            **kw,
        )

    def test_the_weapon_spins_about_its_hub_with_the_presets_energy(self):
        """v2: the tooth is on a rigid body spinning about an axis, not fired
        along the surface normal. Its spin energy must equal the preset's."""
        spec = self._spec(["p000"])
        iv = spec["initial_velocity"][0]
        assert iv["set"] == "weapon" and "vector" not in iv
        assert iv["axis"]["origin"] == list(self.SWING.hub)
        weapon = next(r for r in spec["rigid_bodies"] if r["name"] == "weapon")
        # the hub's inertia plus the meshed head's own, at the arc radius
        impactor = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL)
        head_mg = tooth_geometry(impactor).head_volume * 7.83e-9
        jzz = weapon["inertia"][2] + head_mg * self.SWING.radius_mm ** 2  # Mg mm^2
        ke_mj = 0.5 * jzz * iv["axis"]["omega"] ** 2  # N mm = mJ
        assert ke_mj / 1000.0 == pytest.approx(impactor.ke_j, rel=0.02)

    def test_the_weapon_hub_carries_the_opponents_mass(self):
        spec = self._spec(["p000"])
        weapon = next(r for r in spec["rigid_bodies"] if r["name"] == "weapon")
        assert weapon["main"] == "hub" and weapon["mass"] == pytest.approx(5.443e-3)
        assert spec["sets"]["weapon"] == {"part": "tooth*", "with_nodes": ["hub"]}

    def test_the_tooth_is_rigid_and_has_no_backing_block(self):
        spec = self._spec(["p000"])
        assert {p["match"] for p in spec["parts"]} == {"p000", TOOTH_HEAD}
        assert spec["materials"]["tooth_rigid"]["law"] == "elastic"

    def test_the_robot_is_free_not_clamped(self):
        """v1 fixed the far end; v2 makes it a rigid body with the mass of
        everything not meshed, so the robot can be knocked away."""
        spec = self._spec(["p000"])
        assert "boundary_conditions" not in spec
        rest = next(r for r in spec["rigid_bodies"] if r["name"] == "rest_of_robot")
        assert rest["set"] == "mount" and rest["mass"] == pytest.approx(2.0e-3)
        assert spec["sets"]["mount"]["with_nodes"] == ["chassis"]

    def test_parts_erode_at_their_failure_strain(self):
        assert self._spec(["p000"])["erosion_from_eps_max"] is True

    def test_the_only_contact_is_the_tooth(self):
        """Stand-ins are fragmented into the target, so they are tied by
        shared nodes. A structural interface here cost 7,331 starter errors:
        bolted parts sit flush and TYPE7 reads coincidence as penetration."""
        for scoped in (["p000"], ["p000", "p001", "p002"]):
            names = [c["name"] for c in self._spec(scoped)["contact"]]
            assert names == ["tooth_into_target", "target_into_tooth"]

    def test_stand_ins_are_named_apart_from_real_parts(self):
        """Nothing downstream may mistake a filled-in box for user geometry."""
        spec = self._spec(["p000", "p001"])
        matches = {p["match"] for p in spec["parts"]}
        assert matches == {"p000", "fill_p001", TOOTH_HEAD}
        for entry in spec["parts"]:
            assert entry["material"] in spec["materials"]

    def test_a_stand_in_keeps_its_own_material(self):
        """Impedance at the joint is what governs a 40 us event, so the box
        has to be the neighbour's material and not the panel's."""
        impactor = impactor_spec(
            WeightClass.TWELVE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL
        )
        spec = deck_spec(
            case="lc2",
            target_id="p000",
            standin_ids=["p001"],
            materials={"p000": "aluminium_7075_t6", "p001": "steel_s7_tool"},
            library=LIBRARY,
            tooth=tooth_geometry(impactor),
            approach=self.APPROACH,
            impactor=impactor,
            end_time=40e-6,
            swing=self.SWING,
            rest=self.REST,
        )
        by_match = {p["match"]: p["material"] for p in spec["parts"]}
        assert by_match["p000"] == "aluminium_7075_t6"
        assert by_match["fill_p001"] == "steel_s7_tool"

    def test_the_rigid_remainder_is_over_the_whole_model_not_the_target(self):
        """The far end that carries the rest of the robot's mass (v1: the
        clamp) is chosen over the whole model, not sawn through the panel."""
        spec = self._spec(["p000", "p001"])
        assert "part" not in spec["sets"]["mount"]
        assert spec["sets"]["mount"]["rel_box"] == {"ymax": CLAMP_FRACTION}

    def test_output_intervals_follow_end_time(self):
        spec = self._spec(["p000"], timestep_scale=0.8)
        assert spec["control"]["animation_dt"] == pytest.approx(40e-6 / 20.0)
        assert spec["control"]["th_dt"] == pytest.approx(40e-6 / 200.0)
        assert spec["control"]["timestep_scale"] == 0.8


class TestOrderStandins:
    """Which neighbours get filled in, and in what order.

    A stand-in that touches nothing is a free rigid body the solver will
    happily accelerate off to infinity, so placement grows outward from the
    target by adjacency rather than straight off the scope list.
    """

    # A chain: target - p001 - p002, with p003 detached and p004 far away.
    CHAIN: ClassVar[list[dict[str, Any]]] = [
        _part("p000", (0, 0, 0), (10, 10, 10)),
        _part("p001", (10, 0, 0), (20, 10, 10)),
        _part("p002", (20, 0, 0), (30, 10, 10)),
        _part("p003", (60, 0, 0), (70, 10, 10)),
        _part("p004", (200, 0, 0), (210, 10, 10)),
    ]
    SCOPED: ClassVar[list[str]] = ["p000", "p001", "p002", "p003", "p004"]

    def test_it_grows_outward_from_the_target(self):
        assert order_standins(self.CHAIN, "p000", self.SCOPED) == ["p001", "p002"]

    def test_a_detached_neighbour_is_not_placed(self):
        """p003 is 30 mm off the chain's end — a free body if it were built."""
        assert "p003" not in order_standins(self.CHAIN, "p000", self.SCOPED)

    def test_the_target_never_stands_in_for_itself(self):
        assert "p000" not in order_standins(self.CHAIN, "p000", self.SCOPED)

    def test_a_bridging_part_reconnects_the_rest(self):
        parts = [*self.CHAIN, _part("p005", (30, 0, 0), (60, 10, 10))]
        order = order_standins(parts, "p000", [*self.SCOPED, "p005"])
        assert order == ["p001", "p002", "p005", "p003"]

    def test_the_cap_keeps_the_nearest(self):
        order = order_standins(self.CHAIN, "p000", self.SCOPED, limit=1)
        assert order == ["p001"]
        assert MAX_STANDINS >= 12  # boxes are cheap; the cap is a backstop

    def test_only_scoped_parts_are_considered(self):
        """A part outside the wave radius cannot influence the answer."""
        assert order_standins(self.CHAIN, "p000", ["p000", "p002"]) == []

    def test_an_unknown_target_yields_nothing_rather_than_raising(self):
        assert order_standins(self.CHAIN, "p999", self.SCOPED) == []


class TestAimOverride:
    """A user's strike replaces the prefill's, and nothing else."""

    def _approach(self):
        from case.placement import Approach

        return Approach(
            direction=(0.0, 0.0, 1.0),
            strike_point=(1.0, 2.0, 3.0),
            standoff=0.5,
            reach=73.2,
            exposure=0.84,
            arbitrary=True,
            part_id="p000",
            solid_index=2,
        )

    def test_the_users_point_and_direction_win(self):
        from case.loadcase import _with_aim
        from case.models import Aim

        aim = Aim(point=(10.0, 20.0, 30.0), direction=(0.0, 1.0, 0.0), standoff=9.0)
        got = _with_aim(self._approach(), aim, standoff=0.4)
        assert got.strike_point == (10.0, 20.0, 30.0)
        assert got.direction == (0.0, 1.0, 0.0)

    def test_what_the_user_cannot_know_is_kept(self):
        """exposure, reach, arbitrary and solid_index are not the user's to give.

        solid_index especially: a boundary condition placed on a different
        body of the same multi-solid part holds nothing at all.
        """
        from case.loadcase import _with_aim
        from case.models import Aim

        aim = Aim(point=(10.0, 20.0, 30.0), direction=(0.0, 1.0, 0.0), standoff=9.0)
        got = _with_aim(self._approach(), aim, standoff=0.4)
        assert got.reach == 73.2
        assert got.exposure == 0.84
        assert got.arbitrary is True
        assert got.part_id == "p000"
        assert got.solid_index == 2

    def test_the_standoff_is_derived_not_taken(self):
        """The browser's standoff is a viewing choice; the real one is physics.

        It comes from end_time so a faster preset does not start the tooth
        already embedded, and that arithmetic is not the user's to override.
        """
        from case.loadcase import _with_aim
        from case.models import Aim

        aim = Aim(point=(0.0, 0.0, 0.0), direction=(1.0, 0.0, 0.0), standoff=9.0)
        got = _with_aim(self._approach(), aim, standoff=0.4)
        assert got.standoff == 0.4


class TestStruckBody:
    """A part built from several bodies is meshed as the one that gets hit.

    Mirrored left/right armour exported as one part, or a weapon hub with its
    teeth, is ordinary CAD. Keeping the whole part meshes the siblings too,
    and a sibling beyond the scope radius is tied to nothing — measured at up
    to 339 mm outside a 202 mm radius on Loft Feather Mk 1.2 v59.
    """

    def _part(self):
        return {
            "id": "p152",
            # The part box spans both halves: the width of the machine, and
            # nobody's actual geometry.
            "bbox": {"min": [0.0, 0.0, 0.0], "max": [40.0, 300.0, 12.0]},
            "solids": 2,
            "solid_bboxes": [
                {"min": [0.0, 0.0, 0.0], "max": [40.0, 120.0, 12.0]},
                {"min": [0.0, 180.0, 0.0], "max": [40.0, 300.0, 12.0]},
            ],
        }

    def test_the_struck_bodys_box_is_used_not_the_parts(self):
        from case.loadcase import body_bbox

        assert body_bbox(self._part(), 1)["min"] == [0.0, 180.0, 0.0]
        assert body_bbox(self._part(), 0)["max"] == [40.0, 120.0, 12.0]

    def test_a_single_solid_part_is_unaffected(self):
        from case.loadcase import body_bbox

        part = {"id": "p000", "bbox": {"min": [0, 0, 0], "max": [1, 1, 1]}, "solids": 1}
        assert body_bbox(part, None) == part["bbox"]

    def test_a_report_without_per_solid_boxes_falls_back(self):
        """Back-compatible: a report cached before solid_bboxes existed."""
        from case.loadcase import body_bbox

        part = {**self._part(), "solid_bboxes": None}
        assert body_bbox(part, 1) == part["bbox"]

    def test_an_out_of_range_index_falls_back_rather_than_crashing(self):
        from case.loadcase import body_bbox

        assert body_bbox(self._part(), 9) == self._part()["bbox"]

    def test_standins_chain_from_the_struck_body_not_the_whole_part(self):
        """The adjacency chain decides what holds the target on.

        Measured against the part box, a neighbour beside the *other* half
        reads as touching and gets a stand-in that holds nothing.
        """
        from case.loadcase import order_standins

        target = self._part()
        # Sits against the far half, 180 mm from the struck one.
        far = {
            "id": "p200",
            "bbox": {"min": [0.0, 300.0, 0.0], "max": [40.0, 340.0, 12.0]},
            "solids": 1,
        }
        parts = [target, far]
        whole = order_standins(parts, "p152", ["p152", "p200"])
        body = order_standins(
            parts, "p152", ["p152", "p200"], target_bbox=target["solid_bboxes"][0]
        )
        assert whole == ["p200"], "the part box reaches it"
        assert body == [], "the struck body does not"


def test_the_mesh_payload_carries_the_struck_body_across_the_process_boundary():
    """`part_id` and `solid_index` have to survive the child process.

    They were added to `Approach` for multi-solid targeting and never added to
    the worker's reconstruction, so every multi-solid target was meshed whole
    — in the sweep as well as production, and invisibly, because the whole
    part meshes perfectly well.
    """
    import json
    from dataclasses import asdict

    from case.loadcase import _rebuild_approach
    from case.placement import Approach

    before = Approach(
        direction=(0.0, 0.0, 1.0),
        strike_point=(1.0, 2.0, 3.0),
        standoff=0.5,
        reach=73.2,
        exposure=0.84,
        arbitrary=False,
        part_id="p152",
        solid_index=1,
    )
    after = _rebuild_approach(json.loads(json.dumps(asdict(before))))
    assert after == before


class TestStandinSnapping:
    """Bounding-box noise must not become the element that sets the timestep.

    Tier 0 boxes come from the CAD kernel with sub-micron error: one corpus
    model reports a part spanning x = ±146.75001083 beside a neighbour at
    exactly ±146.75. `fragment` turns that 10.7 nm overhang into a 10.7 nm
    sliver, and the stable timestep goes with the shortest edge in the whole
    model -- measured at 1.073e-05 mm against 1.468 mm for coincident faces,
    which priced one real job at 3,699x its budget.
    """

    def _boxes(self, target, others, min_volume=0.0):
        parts = {"t": {"bbox": target}, **{k: {"bbox": v} for k, v in others.items()}}
        return dict(
            (pid, (lo, hi))
            for pid, lo, hi in loadcase._standin_boxes(
                parts, list(others), target, min_volume=min_volume
            )
        )

    def test_a_nanometre_overhang_is_snapped_away(self):
        target = {"min": [-146.75, -100.0, -5.0], "max": [146.75, 100.0, 5.0]}
        # Exactly the corpus geometry: wider than the target by 1.073e-05 mm,
        # and sitting clear of it in z so no clip trims the x extents.
        noisy = {"min": [-146.75001083, -100.0, 20.0], "max": [146.75001083, 100.0, 24.0]}
        lo, hi = self._boxes(target, {"n": noisy})["n"]
        assert lo[0] == pytest.approx(target["min"][0], abs=0)
        assert hi[0] == pytest.approx(target["max"][0], abs=0)

    def test_every_corner_lands_on_the_grid(self):
        target = {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}
        noisy = {"min": [0.0000004, 0.0, 20.0], "max": [9.9999996, 10.0, 24.0]}
        lo, hi = self._boxes(target, {"n": noisy})["n"]
        for v in (*lo, *hi):
            assert v == pytest.approx(
                round(v / loadcase.STANDIN_SNAP_MM) * loadcase.STANDIN_SNAP_MM
            )

    def test_a_box_thinner_than_the_grid_is_dropped_not_meshed(self):
        """It would otherwise mesh to an edge shorter than the grid, which is
        the failure this whole guard exists to prevent."""
        target = {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}
        sliver = {"min": [0.0, 0.0, 20.0], "max": [10.0, 10.0, 20.0000004]}
        assert "n" not in self._boxes(target, {"n": sliver})


class _FakeOcc:
    """Just enough of `gmsh.model.occ` for `_add_standins`."""

    def __init__(self, fragment, masses: dict[int, float] | None = None):
        self._fragment = fragment
        self.masses = masses or {}
        self.added: list[tuple[float, ...]] = []

    def addBox(self, x, y, z, dx, dy, dz):
        self.added.append((x, y, z, dx, dy, dz))
        return 100 + len(self.added)

    def synchronize(self):
        pass

    def fragment(self, objects, tools):
        return self._fragment(objects, tools)

    def getMass(self, dim, tag):
        return self.masses.get(tag, 0.0)


class _FakeGmsh:
    def __init__(self, occ: _FakeOcc):
        self.model = types.SimpleNamespace(occ=occ)
        self.fuzz_history: list[float] = []
        self._options = {"Geometry.ToleranceBoolean": 0.0}

    # `option` is a module-level namespace in the real API.
    @property
    def option(self):
        outer = self

        class _Option:
            @staticmethod
            def getNumber(name):
                return outer._options[name]

            @staticmethod
            def setNumber(name, value):
                outer._options[name] = value
                if name == "Geometry.ToleranceBoolean":
                    outer.fuzz_history.append(value)

        return _Option

    @property
    def tolerance_boolean(self):
        return self._options["Geometry.ToleranceBoolean"]


BOX = ("p001", [0.0, 0.0, 0.0], [1.0, 1.0, 1.0])


def _clean_fragment(objects, tools):
    """One fragment per input, nothing shared — the healthy case."""
    return [], [[(3, 1)], [(3, 101)]]


class TestStandinFuzz:
    """The fragment is fuzzy, because a snapped box meets CAD that is not
    on the grid. Measured: at an exact boolean the target comes back as two
    bodies — itself plus the lens between them — and that lens is the
    shortest edge in the model."""

    def test_the_fragment_runs_with_the_fuzz_set(self):
        seen: list[float] = []

        def fragment(objects, tools):
            seen.append(gmsh.tolerance_boolean)
            return _clean_fragment(objects, tools)

        gmsh = _FakeGmsh(_FakeOcc(fragment))
        _add_standins(gmsh, [BOX], [1])
        assert seen == [STANDIN_FUZZ_MM]

    def test_the_tolerance_is_put_back_afterwards(self):
        """It is a global option and the session outlives this boolean."""
        gmsh = _FakeGmsh(_FakeOcc(_clean_fragment))
        _add_standins(gmsh, [BOX], [1])
        assert gmsh.tolerance_boolean == 0.0

    def test_the_tolerance_is_put_back_when_the_fragment_raises(self):
        def fragment(objects, tools):
            raise RuntimeError("OCC said no")

        gmsh = _FakeGmsh(_FakeOcc(fragment))
        with pytest.raises(RuntimeError):
            _add_standins(gmsh, [BOX], [1])
        assert gmsh.tolerance_boolean == 0.0

    def test_no_boxes_means_no_boolean_at_all(self):
        gmsh = _FakeGmsh(_FakeOcc(_clean_fragment))
        assert _add_standins(gmsh, [], [1]) == ([1], {})
        assert gmsh.fuzz_history == []


class TestTargetVolumeSurvives:
    """A fuzzy boolean is OCC being told to stop believing small differences.
    The one thing it must never round away is the customer's own part."""

    def test_an_unchanged_target_passes(self):
        occ = _FakeOcc(_clean_fragment, masses={1: 100.0})
        target, standins = _add_standins(_FakeGmsh(occ), [BOX], [1], target_volume=100.0)
        assert target == [1]
        assert standins == {"p001": [101]}

    def test_a_target_that_lost_material_is_refused(self):
        occ = _FakeOcc(_clean_fragment, masses={1: 90.0})
        with pytest.raises(LoadCaseError) as exc:
            _add_standins(_FakeGmsh(occ), [BOX], [1], target_volume=100.0)
        assert "10.000%" in str(exc.value)
        assert "100.000 to 90.000" in str(exc.value)

    def test_drift_inside_the_tolerance_is_allowed(self):
        inside = 100.0 * (1.0 + MAX_TARGET_VOLUME_DRIFT / 2.0)
        occ = _FakeOcc(_clean_fragment, masses={1: inside})
        assert _add_standins(_FakeGmsh(occ), [BOX], [1], target_volume=100.0)[0] == [1]

    def test_no_measured_volume_means_no_check(self):
        """Back-compatible: the check is on what the caller measured, and a
        caller that measured nothing has nothing to compare against."""
        occ = _FakeOcc(_clean_fragment, masses={1: 0.0})
        assert _add_standins(_FakeGmsh(occ), [BOX], [1])[0] == [1]


class TestTheAimPicksTheBody:
    """The builder's aim decides which body is struck — not the exposure score.

    From job f1ca7b48 (inertial-v6), whose numbers these are. `choose_approach`
    ranks bodies by how exposed they are and never sees the aim, so it picked
    p035's 400x422x73 mm shell while the builder had aimed at the 54x58x58 mm
    boss at the far end of the machine. Only the shell was meshed and the
    tooth was fired 1.46 mm outside it, into the space where the boss was.
    B3.5 grades an aim against the part's whole surface, so nothing caught it
    until B4 refused with "no usable density or wave speed for p035" — about a
    part whose material the builder had chosen by hand.
    """

    SHELL: ClassVar[dict[str, list[float]]] = {
        "min": [-200.0, -75.0, -21.0],
        "max": [200.0, 347.0, 52.0],
    }
    BOSS: ClassVar[dict[str, list[float]]] = {
        "min": [-27.05, -97.0, -21.0],
        "max": [27.05, -39.36, 37.0],
    }
    PART: ClassVar[dict[str, Any]] = {
        "id": "p035",
        "solids": 2,
        "solid_bboxes": [SHELL, BOSS],
    }
    AT_THE_BOSS: ClassVar[tuple[float, float, float]] = (-0.6423, -76.4616, -20.9997)
    DIRECTION: ClassVar[tuple[float, float, float]] = (0.0, 0.017452, -0.999848)

    @staticmethod
    def _approach(solid_index: int | None) -> Approach:
        """What `choose_approach` returned for p035: the shell, at y = +347."""
        return Approach(
            direction=(0.0, 1.0, 0.0),
            strike_point=(0.0, 347.0, 16.09),
            standoff=0.5,
            reach=200.0,
            exposure=180.0,
            arbitrary=False,
            part_id="p035",
            solid_index=solid_index,
        )

    def _aim(self) -> Aim:
        return Aim(
            point=self.AT_THE_BOSS, direction=self.DIRECTION, standoff=0.5, human_placed=True
        )

    def test_the_point_resolves_to_the_body_it_lands_in(self):
        assert _body_for_point(self.PART, self.AT_THE_BOSS, 0) == 1

    def test_the_aim_overrides_the_exposure_scorer(self):
        fixed = _with_aim(self._approach(0), self._aim(), 0.5, self.PART)
        assert fixed.solid_index == 1, "the builder aimed at the boss, not the shell"

    def test_the_prefills_own_point_still_resolves_to_the_shell(self):
        """The bug is the mismatch, not the scorer: unmoved, it agrees."""
        assert _body_for_point(self.PART, (0.0, 347.0, 16.09), 1) == 0

    def test_a_part_with_no_per_solid_boxes_keeps_what_the_scorer_said(self):
        """Back-compatible: a report cached before `solid_bboxes` existed."""
        part = {"id": "p035", "solids": 2, "solid_bboxes": None}
        assert _body_for_point(part, self.AT_THE_BOSS, 0) == 0
        assert _with_aim(self._approach(0), self._aim(), 0.5, part).solid_index == 0

    def test_no_part_at_all_keeps_what_the_scorer_said(self):
        assert _with_aim(self._approach(1), self._aim(), 0.5, None).solid_index == 1

    def test_a_point_outside_every_body_takes_the_nearest(self):
        """A strike is placed a standoff clear of the surface, so its point can
        sit just outside its own body's box."""
        just_outside = (0.0, -97.5, 0.0)
        assert _body_for_point(self.PART, just_outside, 0) == 1

    def test_the_rest_of_the_approach_is_untouched(self):
        fixed = _with_aim(self._approach(0), self._aim(), 0.5, self.PART)
        assert fixed.strike_point == self.AT_THE_BOSS
        assert fixed.direction == self.DIRECTION
        assert (fixed.reach, fixed.exposure, fixed.arbitrary) == (200.0, 180.0, False)


class TestUnboxableBodiesKeepTheirIndex:
    """`solid_bboxes` is one entry per solid, `None` where Tier 0 could not box
    one. The position is the body's name — it indexes `solid_volumes` and the
    STEP import's own ordering — so a gap must be carried, never closed up.
    """

    A: ClassVar[dict[str, list[float]]] = {"min": [0.0, 0.0, 0.0], "max": [10.0, 10.0, 10.0]}
    C: ClassVar[dict[str, list[float]]] = {"min": [50.0, 0.0, 0.0], "max": [60.0, 10.0, 10.0]}
    #: Body 1 could not be boxed; bodies 0 and 2 could.
    PART: ClassVar[dict[str, Any]] = {
        "id": "p900",
        "solids": 3,
        "bbox": {"min": [0.0, 0.0, 0.0], "max": [60.0, 10.0, 10.0]},
        "solid_bboxes": [A, None, C],
    }

    def test_a_point_in_the_third_body_is_body_two_not_body_one(self):
        """Closing the gap would rename body 2 to body 1, and the mesh would
        then be taken from whichever solid the STEP import calls 1."""
        assert _body_for_point(self.PART, (55.0, 5.0, 5.0), None) == 2

    def test_a_point_in_the_first_body_is_still_body_zero(self):
        assert _body_for_point(self.PART, (5.0, 5.0, 5.0), None) == 0

    def test_an_unboxable_body_falls_back_to_the_part_box(self):
        """There is nothing to scope or clamp against, and the part box is the
        same fallback a report cached before `solid_bboxes` existed gets."""
        assert loadcase.body_bbox(self.PART, 1) == self.PART["bbox"]

    def test_a_boxed_body_still_uses_its_own_box(self):
        assert loadcase.body_bbox(self.PART, 2) == self.C

    def test_every_body_unboxable_falls_back_rather_than_guessing(self):
        part = {**self.PART, "solid_bboxes": [None, None, None]}
        assert _body_for_point(part, (5.0, 5.0, 5.0), 1) == 1
        assert loadcase.body_bbox(part, 0) == part["bbox"]


def test_suggest_scope_previews_only_connected_standins():
    """The preview must carry what the build carries, not the whole radius."""
    import yaml

    from case.impactor import impactor_spec
    from case.loadcase import suggest_scope
    from tests.conftest import SIM_ROOT, make_tier0

    parts = make_tier0()["parts"]
    # p002 (chassis) rises to meet the armour p001; p000 (weapon bar) stays clear
    parts[2] = {**parts[2], "bbox": {"min": [50.0, 50.0, 40.0], "max": [250.0, 250.0, 100.0]}}
    library = yaml.safe_load((SIM_ROOT / "materials.yaml").read_text())
    out = suggest_scope(
        parts, {p["id"]: "aluminium_6061_t6" for p in parts}, library,
        target_id="p001", impactor=impactor_spec("12lb", "typical", "horizontal"),
        end_time=4e-5, exclude_span=True,
    )
    assert out["standin_ids"] == ["p002"]
    assert set(out["standin_ids"]) <= set(out["in_radius_ids"])
    assert out["scoped_ids"] == ["p001", *out["standin_ids"]]
    excluded = suggest_scope(
        parts, {p["id"]: "aluminium_6061_t6" for p in parts}, library,
        target_id="p001", impactor=impactor_spec("12lb", "typical", "horizontal"),
        end_time=4e-5, exclude_span=True, exclude=out["standin_ids"],
    )
    assert excluded["standin_ids"] == []


class TestSlivers:
    """`_slivers` is the check that sends a mesh back for refinement."""

    @staticmethod
    def _one_tet(gmsh, z4):
        gmsh.model.add("t")
        vol = gmsh.model.addDiscreteEntity(3)
        gmsh.model.mesh.addNodes(3, vol, [1, 2, 3, 4], [0, 0, 0, 10, 0, 0, 0, 10, 0, 3, 3, z4])
        gmsh.model.mesh.addElementsByType(vol, 4, [1], [1, 2, 3, 4])
        g = gmsh.model.addPhysicalGroup(3, [vol])
        gmsh.model.setPhysicalName(3, g, "p000")

    def test_a_flat_tet_is_a_sliver_and_a_plump_one_is_not(self):
        import gmsh

        from case.loadcase import _slivers
        gmsh.initialize()
        try:
            gmsh.option.setNumber("General.Terminal", 0)
            self._one_tet(gmsh, 0.01)
            assert _slivers(gmsh) == {"p000": 1}
            gmsh.clear()
            self._one_tet(gmsh, 8.0)
            assert _slivers(gmsh) == {}
        finally:
            gmsh.finalize()
