"""Unit tests for B4's admission arithmetic.

Hand-built tets and hand-checked numbers. This is the calculation that decides
whether a user is charged, so the mass-scaling model is pinned against closed
form rather than against a golden file.
"""

from __future__ import annotations

import math
from typing import Any

import pytest

from case.mesh_metrics import (
    CHAR_QUANTILES,
    DEFAULT_POISSON,
    K_TET,
    PartMetrics,
    analyse,
    dilatational_wave_speed,
    poisson_ratio,
    tet_geometry,
    wave_speed,
)

# steel_s7_tool, verbatim from the plugin's configs/materials.yaml.
S7: dict[str, Any] = {
    "law": "johnson_cook",
    "density": 7.83e-9,
    "young": 207000.0,
    "poisson": 0.30,
}
# aluminium_7075_t6, likewise.
AL: dict[str, Any] = {
    "law": "johnson_cook",
    "density": 2.81e-9,
    "young": 71700.0,
    "poisson": 0.33,
}


def unit_tet(scale: float = 1.0, origin: tuple[float, float, float] = (0.0, 0.0, 0.0)):
    """Corner tet with three orthogonal edges of length ``scale``."""
    ox, oy, oz = origin
    return [
        (ox, oy, oz),
        (ox + scale, oy, oz),
        (ox, oy + scale, oz),
        (ox, oy, oz + scale),
    ]


# --- geometry -------------------------------------------------------------


class TestTetGeometry:
    def test_unit_tet_volume(self):
        # Corner tet of a unit cube: V = 1/6.
        _, volume, _ = tet_geometry(unit_tet(1.0))
        assert volume == pytest.approx(1.0 / 6.0)

    def test_shortest_edge_is_the_leg_not_the_face_diagonal(self):
        # Legs are 1.0; face diagonals are sqrt(2).
        edge, _, _ = tet_geometry(unit_tet(1.0))
        assert edge == pytest.approx(1.0)

    def test_volume_scales_cubically(self):
        _, v1, _ = tet_geometry(unit_tet(1.0))
        _, v2, _ = tet_geometry(unit_tet(2.0))
        assert v2 / v1 == pytest.approx(8.0)

    def test_translation_invariant(self):
        a = tet_geometry(unit_tet(1.5))
        b = tet_geometry(unit_tet(1.5, origin=(100.0, -50.0, 7.0)))
        assert a == pytest.approx(b)

    def test_degenerate_tet_has_zero_volume(self):
        flat = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]
        _, volume, _ = tet_geometry(flat)
        assert volume == pytest.approx(0.0)

    def test_characteristic_length_is_k_times_volume_over_largest_face(self):
        # Corner tet, legs 1: V = 1/6, largest face is the sqrt(3)/2 hypotenuse
        # face rather than any of the three right-triangle legs at 1/2.
        _, volume, char = tet_geometry(unit_tet(1.0))
        assert char == pytest.approx(K_TET * volume / (math.sqrt(3.0) / 2.0))

    def test_characteristic_length_scales_linearly(self):
        _, _, c1 = tet_geometry(unit_tet(1.0))
        _, _, c2 = tet_geometry(unit_tet(3.0))
        assert c2 / c1 == pytest.approx(3.0)

    def test_a_sliver_keeps_its_edges_and_loses_its_height(self):
        """The defect the shortest edge could not see.

        Squashing one node towards the opposite face leaves every edge close to
        its original length -- the shortest is unchanged at 1.0 -- while the
        height the element can be compressed through, and so the timestep it
        can take, falls with the volume.
        """
        healthy = unit_tet(1.0)
        sliver = [(0, 0, 0), (1, 0, 0), (0, 1, 0), (0.5, 0.5, 0.001)]
        h_edge, _, h_char = tet_geometry(healthy)
        s_edge, _, s_char = tet_geometry(sliver)
        assert s_edge == pytest.approx(h_edge, rel=0.3)
        assert s_char < h_char / 100

    def test_degenerate_tet_has_no_characteristic_length(self):
        flat = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0)]
        assert tet_geometry(flat)[2] == 0.0


# --- wave speed -----------------------------------------------------------


class TestDilatationalWaveSpeed:
    """The speed a solid element's timestep goes with, not a rod's.

    Checked against handbook longitudinal (P-wave) speeds, which is an
    independent source: the constant in :data:`K_TET` was fitted against the
    solver, and if the wave speed were wrong the fit would have absorbed the
    error and still looked exact. It cannot absorb both, because the two
    materials below have different Poisson ratios.
    """

    def test_steel_matches_the_handbook_p_wave_speed(self):
        # ~5900 m/s for steel, against ~5150 for the bar speed.
        assert dilatational_wave_speed(S7) / 1000.0 == pytest.approx(5900.0, rel=0.02)

    def test_aluminium_matches_the_handbook_p_wave_speed(self):
        # ~6300 m/s; 7075 is stiffer per unit density than pure aluminium.
        assert dilatational_wave_speed(AL) / 1000.0 == pytest.approx(6300.0, rel=0.04)

    def test_it_is_always_faster_than_the_bar_speed(self):
        for material in (S7, AL):
            assert dilatational_wave_speed(material) > wave_speed(material)

    def test_the_ratio_is_the_lateral_constraint_factor(self):
        nu = S7["poisson"]
        expected = math.sqrt((1 - nu) / ((1 + nu) * (1 - 2 * nu)))
        assert dilatational_wave_speed(S7) / wave_speed(S7) == pytest.approx(expected)

    def test_no_density_means_no_speed(self):
        assert dilatational_wave_speed({"density": 0.0, "young": 1.0}) == 0.0


class TestPoissonRatio:
    def test_prefers_poisson(self):
        assert poisson_ratio({"poisson": 0.42, "nu12": 0.1}) == pytest.approx(0.42)

    def test_falls_back_to_nu12_for_composites(self):
        # The five composite cards in the library spell it this way.
        assert poisson_ratio({"law": "compsh", "nu12": 0.12}) == pytest.approx(0.12)

    def test_defaults_when_a_card_carries_neither(self):
        assert poisson_ratio({"density": 1.0}) == pytest.approx(DEFAULT_POISSON)

    def test_clamped_below_one_half(self):
        """An incompressible card must refuse, not cost infinity."""
        assert poisson_ratio({"poisson": 0.5}) < 0.5
        incompressible = {"density": 1e-9, "young": 1.0, "poisson": 0.5}
        assert math.isfinite(dilatational_wave_speed(incompressible))


class TestWaveSpeed:
    def test_matches_closed_form_for_steel(self):
        # sqrt(207000 MPa / 7.83e-9 Mg/mm^3) in mm/s.
        assert wave_speed(S7) == pytest.approx(math.sqrt(207000.0 / 7.83e-9))

    def test_steel_is_about_5140_m_per_s(self):
        # Sanity against the textbook value, converted from mm/s.
        assert wave_speed(S7) / 1000.0 == pytest.approx(5140.0, rel=0.01)

    def test_aluminium_is_about_5050_m_per_s(self):
        assert wave_speed(AL) / 1000.0 == pytest.approx(5051.0, rel=0.01)

    def test_zero_density_is_not_a_division_error(self):
        assert wave_speed({"density": 0.0, "young": 1.0}) == 0.0

    def test_missing_modulus_yields_zero(self):
        assert wave_speed({"density": 7.8e-9}) == 0.0

    def test_orthotropic_uses_the_larger_modulus(self):
        m = {"law": "compsh", "density": 1.6e-9, "e11": 130000.0, "e22": 9000.0}
        assert wave_speed(m) == pytest.approx(math.sqrt(130000.0 / 1.6e-9))

    def test_ogden_uses_bulk_modulus(self):
        m = {"law": "ogden", "density": 1.2e-9, "bulk_modulus": 2000.0}
        assert wave_speed(m) == pytest.approx(math.sqrt(2000.0 / 1.2e-9))


# --- the estimate ---------------------------------------------------------


class TestTimestepAndCycles:
    def test_physical_timestep_is_edge_over_wave_speed(self):
        m = analyse(
            {"p000": [unit_tet(1.0)]},
            {"p000": AL},
            end_time=1e-4,
            timestep_min=0.0,
        )
        char = tet_geometry(unit_tet(1.0))[2]
        assert m.dt_physical == pytest.approx(char / dilatational_wave_speed(AL))
        assert m.dt_effective == pytest.approx(0.9 * m.dt_physical)
        assert not m.mass_scaling_active

    def test_the_floor_wins_when_it_is_higher(self):
        # 1 mm of aluminium gives dt ~ 1.98e-7; a 1e-6 floor dominates.
        m = analyse({"p000": [unit_tet(1.0)]}, {"p000": AL}, end_time=1e-4, timestep_min=1e-6)
        assert m.dt_effective == pytest.approx(1e-6)
        assert m.mass_scaling_active

    def test_cycles_follow_the_effective_timestep(self):
        m = analyse({"p000": [unit_tet(1.0)]}, {"p000": AL}, end_time=1e-4, timestep_min=1e-8)
        assert m.cycles == math.ceil(1e-4 / m.dt_effective)

    def test_element_cycles_is_elements_times_cycles(self):
        tets = [unit_tet(1.0, origin=(3.0 * i, 0, 0)) for i in range(5)]
        m = analyse({"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=1e-8)
        assert m.elements == 5
        assert m.element_cycles == pytest.approx(5 * m.cycles)

    def test_the_smallest_element_anywhere_sets_the_timestep(self):
        parts = {"p000": [unit_tet(10.0)], "p001": [unit_tet(0.1, origin=(50, 0, 0))]}
        m = analyse(parts, {"p000": AL, "p001": AL}, end_time=1e-4, timestep_min=0.0)
        small = tet_geometry(unit_tet(0.1))[2]
        assert m.dt_physical == pytest.approx(small / dilatational_wave_speed(AL))

    def test_zero_end_time_is_rejected(self):
        with pytest.raises(ValueError, match="end_time"):
            analyse({"p000": [unit_tet()]}, {"p000": AL}, end_time=0.0, timestep_min=0.0)

    def test_empty_mesh_is_rejected(self):
        with pytest.raises(ValueError, match="no usable tetrahedra"):
            analyse({"p000": []}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)


class TestAddedMass:
    def test_no_added_mass_without_a_floor(self):
        m = analyse({"p000": [unit_tet(1.0)]}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)
        assert m.mass_added == pytest.approx(0.0)
        assert m.added_mass_fraction == pytest.approx(0.0)

    def test_added_mass_matches_the_closed_form(self):
        # One element, dt_elem = 0.9 * L/c. Floor is 4x that, so the density
        # must rise 16x -> added mass is 15x the original.
        char = tet_geometry(unit_tet(1.0))[2]
        dt_elem = 0.9 * char / dilatational_wave_speed(AL)
        edge = 1.0
        m = analyse(
            {"p000": [unit_tet(edge)]},
            {"p000": AL},
            end_time=1e-4,
            timestep_min=4 * dt_elem,
        )
        assert m.mass_added / m.mass_total == pytest.approx(15.0)

    def test_only_elements_below_the_floor_are_scaled(self):
        # The big tet already clears the floor; only the small one pays.
        big, small = unit_tet(10.0), unit_tet(0.1, origin=(50, 0, 0))
        dt_big = 0.9 * tet_geometry(big)[2] / dilatational_wave_speed(AL)
        m = analyse(
            {"p000": [big], "p001": [small]},
            {"p000": AL, "p001": AL},
            end_time=1e-4,
            timestep_min=dt_big,
        )
        by_id = {p.part_id: p for p in m.per_part}
        assert by_id["p000"].added_mass == pytest.approx(0.0)
        assert by_id["p001"].added_mass > 0

    def test_worst_added_mass_names_the_culprit(self):
        big, small = unit_tet(10.0), unit_tet(0.1, origin=(50, 0, 0))
        m = analyse(
            {"p000": [big], "p001": [small]},
            {"p000": AL, "p001": AL},
            end_time=1e-4,
            timestep_min=0.9 * tet_geometry(big)[2] / dilatational_wave_speed(AL),
        )
        worst = m.worst_added_mass()
        assert worst[0].part_id == "p001"
        # Parts contributing nothing are not named in a refusal message.
        assert all(p.added_mass > 0 for p in worst)

    def test_one_sliver_barely_moves_the_fraction(self):
        # Counter-intuitive and worth pinning: added mass for a scaled element
        # is rho * (L^3/6) * (dt*c/(scale*L))^2, which is *linear* in L. A
        # single tiny element therefore contributes almost nothing to the
        # fraction, however badly it wrecks the timestep. The fraction is a
        # bulk property; dt_physical is the one the sliver ruins.
        bulk = [unit_tet(3.0, origin=(10.0 * i, 0, 0)) for i in range(50)]
        sliver = unit_tet(0.001, origin=(500.0, 0, 0))
        m = analyse(
            {"p000": bulk, "p001": [sliver]},
            {"p000": AL, "p001": AL},
            end_time=1e-4,
            timestep_min=1e-8,
        )
        assert m.added_mass_fraction < 1e-6
        assert m.dt_physical < 1e-9  # the timestep is wrecked even so
        assert m.worst_added_mass()[0].part_id == "p001"

    def test_added_mass_is_linear_in_element_size(self):
        # added = rho/6 * (K*L - L^3). Well below the floor the cubic term
        # vanishes and halving the element halves its added mass.
        def added_for(edge: float) -> float:
            m = analyse(
                {"p000": [unit_tet(edge)]},
                {"p000": AL},
                end_time=1e-4,
                timestep_min=1e-8,
            )
            return m.mass_added

        assert added_for(0.001) / added_for(0.0005) == pytest.approx(2.0, rel=1e-3)

    def test_the_cubic_term_makes_it_slightly_sublinear_near_the_floor(self):
        # Just under the floor the "-1" in (ratio - 1) still bites, so the
        # relationship is 1.95x rather than 2x. Pinned so the gate's
        # sensitivity near its threshold is not accidentally changed.
        def added_for(edge: float) -> float:
            m = analyse({"p000": [unit_tet(edge)]}, {"p000": AL}, end_time=1e-4, timestep_min=1e-8)
            return m.mass_added

        assert added_for(0.01) / added_for(0.005) == pytest.approx(1.95, rel=0.02)

    def test_a_bulk_of_undersized_elements_does_move_the_fraction(self):
        # The real driver: most of the model's *mass* sitting below the floor,
        # not one sliver. 0.5 mm aluminium runs at 8.9e-8, just under a 1e-7
        # floor, and here it carries the majority of the mass.
        fine = [unit_tet(0.5, origin=(1.0 * i, 0, 0)) for i in range(2000)]
        coarse = [unit_tet(3.0, origin=(5000.0 + 10.0 * i, 0, 0)) for i in range(5)]
        m = analyse(
            {"p000": coarse, "p001": fine},
            {"p000": AL, "p001": AL},
            end_time=1e-4,
            timestep_min=1e-7,
        )
        assert m.added_mass_fraction > 0.05
        assert m.worst_added_mass()[0].part_id == "p001"


class TestMixedMaterials:
    def test_aluminium_not_steel_sets_the_shorter_timestep(self):
        """Reversed by the switch to the dilatational speed, and correctly so.

        On the bar speed steel came out faster, so this test used to assert
        steel had the shorter timestep. Real P-wave speeds go the other way --
        roughly 5900 m/s in steel against 6300 in aluminium -- because
        aluminium's lower Poisson ratio buys less lateral constraint per unit
        stiffness. The old assertion was pinning the wrong physics, and it
        passed for as long as the wrong wave speed was used.
        """
        steel = analyse({"p000": [unit_tet(1.0)]}, {"p000": S7}, end_time=1e-4, timestep_min=0.0)
        alu = analyse({"p000": [unit_tet(1.0)]}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)
        assert alu.dt_physical < steel.dt_physical

    def test_mass_uses_each_part_own_density(self):
        m = analyse(
            {"p000": [unit_tet(1.0)], "p001": [unit_tet(1.0, origin=(9, 0, 0))]},
            {"p000": S7, "p001": AL},
            end_time=1e-4,
            timestep_min=0.0,
        )
        by_id = {p.part_id: p for p in m.per_part}
        assert by_id["p000"].mass / by_id["p001"].mass == pytest.approx(
            S7["density"] / AL["density"]
        )

    def test_no_material_anywhere_is_rejected(self):
        # Nothing can be estimated, so the job cannot be admitted.
        with pytest.raises(ValueError, match="usable timestep"):
            analyse({"p000": [unit_tet(1.0)]}, {}, end_time=1e-4, timestep_min=1e-8)

    def test_one_unpriced_part_is_reported_not_swallowed(self):
        # It contributes no mass, so the added-mass fraction would be computed
        # over only part of the model. B4 has to see that.
        m = analyse(
            {"p000": [unit_tet(1.0)], "p001": [unit_tet(1.0, origin=(9, 0, 0))]},
            {"p000": AL},
            end_time=1e-4,
            timestep_min=1e-8,
        )
        assert m.unpriced_parts == ("p001",)

    def test_fully_priced_mesh_reports_nothing_unpriced(self):
        m = analyse({"p000": [unit_tet(1.0)]}, {"p000": AL}, end_time=1e-4, timestep_min=1e-8)
        assert m.unpriced_parts == ()


# --- the distribution, not just its minimum -------------------------------


class TestCharQuantiles:
    """What separates a lone artefact from a part that resolves that fine.

    The corpus motivating these: over 54 retained scoped meshes the median
    case meets its 1.5 mm request at p50 while carrying a minimum 10x below
    it, and six cases reach 6e-7 mm. ``dt_physical`` goes with the minimum, so
    the minimum alone cannot say whether a refusal on cost is about the user's
    geometry or about ours.
    """

    def test_quantiles_are_the_documented_three(self):
        assert CHAR_QUANTILES == (0.001, 0.01, 0.5)

    def test_one_tiny_element_moves_the_minimum_and_not_the_median(self):
        # 999 elements at 1.0 and one at 0.01. The timestep collapses onto the
        # outlier; p50 stays where the mesh actually is.
        tets = [unit_tet(1.0, origin=(3.0 * i, 0, 0)) for i in range(999)]
        tets.append(unit_tet(0.01, origin=(-10.0, 0, 0)))
        m = analyse({"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)

        big = tet_geometry(unit_tet(1.0))[2]
        small = tet_geometry(unit_tet(0.01))[2]
        assert m.dt_physical == pytest.approx(small / dilatational_wave_speed(AL))
        assert m.char_p50_mm == pytest.approx(big)
        assert m.char_p01_mm == pytest.approx(big)
        assert m.char_p001_mm == pytest.approx(small)

    def test_a_uniformly_fine_mesh_reports_fine_at_every_quantile(self):
        # The other branch: nothing to repair, the part really is that size.
        tets = [unit_tet(0.01, origin=(0.1 * i, 0, 0)) for i in range(1000)]
        m = analyse({"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)
        small = tet_geometry(unit_tet(0.01))[2]
        for q in (m.char_p001_mm, m.char_p01_mm, m.char_p50_mm):
            assert q == pytest.approx(small)

    def test_below_a_thousand_elements_p001_is_the_minimum(self):
        # Documented on CHAR_QUANTILES: nearest-rank on a short list cannot
        # distinguish the 0.1st percentile from the smallest element.
        tets = [unit_tet(1.0, origin=(3.0 * i, 0, 0)) for i in range(10)]
        tets.append(unit_tet(0.01, origin=(-10.0, 0, 0)))
        m = analyse({"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)
        assert m.char_p001_mm == pytest.approx(min(p.min_char_mm for p in m.per_part))

    def test_quantiles_span_every_part_together(self):
        # A coarse stand-in must not dilute the target's distribution by
        # being counted separately, nor be left out of the whole-mesh view.
        parts = {
            "p000": [unit_tet(0.1, origin=(0.3 * i, 0, 0)) for i in range(100)],
            "p001": [unit_tet(10.0, origin=(0, 300.0 * i, 0)) for i in range(100)],
        }
        m = analyse(parts, {"p000": AL, "p001": AL}, end_time=1e-4, timestep_min=0.0)
        by_id = {p.part_id: p for p in m.per_part}
        assert by_id["p000"].char_p50_mm == pytest.approx(tet_geometry(unit_tet(0.1))[2])
        assert by_id["p001"].char_p50_mm == pytest.approx(tet_geometry(unit_tet(10.0))[2])
        # Half the elements are fine and half coarse, so the whole-mesh median
        # lands on the boundary -- at the fine side, nearest-rank rounding down.
        assert m.char_p50_mm == pytest.approx(tet_geometry(unit_tet(0.1))[2])

    def test_unmeasured_quantiles_are_infinite_not_zero(self):
        # The gate fixtures build PartMetrics by hand. A zero would read as
        # "infinitely fine" and silently fail any threshold put on it.
        assert (
            PartMetrics(
                part_id="p000",
                elements=0,
                min_edge_mm=math.inf,
                min_char_mm=math.inf,
                wave_speed=0.0,
                mass=0.0,
                added_mass=0.0,
            ).char_p50_mm
            == math.inf
        )


class TestBelowMeshMin:
    def test_counts_elements_under_the_floor_gmsh_was_given(self):
        tets = [unit_tet(1.0, origin=(3.0 * i, 0, 0)) for i in range(20)]
        tets += [unit_tet(0.01, origin=(-5.0 * i - 10, 0, 0)) for i in range(3)]
        # char = 0.86 * edge, so a 0.5 mm floor is above the 0.01 mm tets and
        # below the 1.0 mm ones.
        m = analyse(
            {"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=0.0, mesh_size_min=0.5
        )
        assert m.below_mesh_min == 3
        assert m.per_part[0].below_mesh_min == 3

    def test_no_floor_given_means_no_count(self):
        # Zero is "not asked", not "none found". Nothing may read it as a
        # clean bill of health.
        tets = [unit_tet(0.001, origin=(0.01 * i, 0, 0)) for i in range(5)]
        m = analyse({"p000": tets}, {"p000": AL}, end_time=1e-4, timestep_min=0.0)
        assert m.below_mesh_min == 0

    def test_the_floor_does_not_touch_the_price(self):
        # Documented on analyse: mesh_size_min is a diagnostic. The timestep
        # goes with the element that exists, not the one we asked for.
        tets = [unit_tet(0.01, origin=(0.1 * i, 0, 0)) for i in range(5)]
        kw = dict(end_time=1e-4, timestep_min=0.0)
        loose = analyse({"p000": tets}, {"p000": AL}, **kw)
        strict = analyse({"p000": tets}, {"p000": AL}, mesh_size_min=1.0, **kw)
        assert loose.element_cycles == strict.element_cycles
        assert loose.dt_physical == strict.dt_physical
        assert strict.below_mesh_min == 5
