"""Unit tests for impactor sizing.

The headline property is that every shipped preset reaches ``m_eff`` at *true*
S7 density. If that ever stops holding, the tooth starts absorbing the wrong
share of the impact energy and LC1's output is biased.
"""

from __future__ import annotations

import math

import pytest

from case.models import EnergyLevel, OpponentArchetype, WeightClass
from case.impactor import impactor_spec
from case.weapon_size import weapon_size
from case.weapon_size import PLATE_THICKNESS_MM as HEAD_THICKNESS_MM
from case.tooth import S7_DENSITY_MG_MM3, ToothGeometry, tooth_geometry

ALL_PRESETS = [(wc, lv, ar) for wc in WeightClass for lv in EnergyLevel for ar in OpponentArchetype]


def geometry_for(wc, lv, ar) -> ToothGeometry:
    return tooth_geometry(impactor_spec(wc, lv, ar))


class TestDensityIsHonest:
    """The reason the backing block exists at all."""

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_every_preset_lands_on_true_s7_density(self, wc, lv, ar):
        g = geometry_for(wc, lv, ar)
        assert g.density_scale == pytest.approx(1.0, abs=1e-9)
        assert not g.clamped

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_every_preset_reaches_its_effective_mass(self, wc, lv, ar):
        spec = impactor_spec(wc, lv, ar)
        g = tooth_geometry(spec)
        # achieved_mass is in Mg; the preset is in kg.
        assert g.achieved_mass_mg * 1000.0 == pytest.approx(spec.m_eff_kg, rel=1e-9)

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_impedance_is_unperturbed(self, wc, lv, ar):
        # Z = sqrt(rho*E); a scale of 1.0 means the head's energy split is
        # not biased in either direction.
        assert geometry_for(wc, lv, ar).impedance_ratio == pytest.approx(1.0, abs=1e-9)

    def test_the_narrowest_radius_preset_needs_no_density_scaling(self):
        # v1: a 121 mm 12 lb drum against a 1.042 kg m_eff needed 5.5x density
        # when the backing matched the head's width; a cube does not. The
        # narrowest preset is now the 1 lb disc.
        g = geometry_for(WeightClass.ONE_LB, EnergyLevel.HIGH, OpponentArchetype.VERTICAL_DRUM)
        assert g.density_scale == pytest.approx(1.0, abs=1e-9)


class TestHeadProportions:
    def test_head_scales_with_weight_class(self):
        small = geometry_for(WeightClass.ONE_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL)
        big = geometry_for(WeightClass.THIRTY_LB, EnergyLevel.TYPICAL, OpponentArchetype.HORIZONTAL)
        assert big.head_width > small.head_width
        assert big.head_thickness > small.head_thickness

    def test_head_dimensions_follow_the_documented_ratios(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        g = tooth_geometry(spec)
        size = weapon_size(spec.weight_class, spec.archetype)
        assert g.head_width == pytest.approx(size.thickness_mm)
        assert g.head_thickness == pytest.approx(HEAD_THICKNESS_MM[WeightClass.TWELVE_LB])
        assert g.head_length == pytest.approx(size.bar_width_mm)

    def test_a_disc_tooth_is_a_fraction_of_the_disc(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.VERTICAL_DRUM)
        size = weapon_size(spec.weight_class, spec.archetype)
        assert tooth_geometry(spec).head_length == pytest.approx(0.3 * size.od_mm)

    def test_head_volume_is_the_trapezoid_formula(self):
        """Land at the tip, full thickness at the root."""
        g = geometry_for(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        assert g.head_volume == pytest.approx(
            0.5 * (g.land_width + g.head_thickness) * g.head_length * g.head_width
        )

    def test_a_zero_land_collapses_to_the_old_wedge(self):
        """The trapezoid generalises the triangle rather than replacing it.

        If the two did not agree at the limit, one of them would be a
        different formula wearing the same name.
        """
        g = geometry_for(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        collapsed = 0.5 * (0.0 + g.head_thickness) * g.head_length * g.head_width
        assert collapsed == pytest.approx(0.5 * g.head_thickness * g.head_length * g.head_width)

    def test_the_12lb_head_is_a_plausible_tooth(self):
        # The end of a 295 x 49 x 12 mm bar: 12 mm across the strike, 12 mm
        # deep, 49 mm along its travel.
        g = geometry_for(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        assert g.head_width == pytest.approx(12.0)
        assert g.head_thickness == pytest.approx(12.0)
        assert g.head_length == pytest.approx(49.2, rel=0.01)

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_every_head_spans_at_least_two_production_elements(self, wc, lv, ar):
        # Regression: thickness as 0.035 x radius gave a 1 lb tooth 1.75 mm
        # thick once the weapon diameters came down, under one 1.5 mm element.
        assert geometry_for(wc, lv, ar).head_thickness >= 2 * 1.5


class TestStrikingLand:
    """The head strikes with a flat land, proportional to its own thickness.

    It replaced ``edge_radius``, which was computed from the mesh size, stored,
    and read by nothing -- so the geometry cut a mathematically sharp tip that
    meshed 126x finer than the mesh size and owned the timestep for every model
    in its class. Proportional rather than absolute because the old
    ``max(2.0 mm, 2 elements)`` wanted a 3 mm land on a 1 lb head 2.89 mm thick.
    """

    @pytest.mark.parametrize(
        "wc",
        [WeightClass.ONE_LB, WeightClass.THREE_LB, WeightClass.TWELVE_LB, WeightClass.THIRTY_LB],
    )
    def test_the_land_is_a_third_of_the_head(self, wc):
        g = geometry_for(wc, EnergyLevel.TYPICAL, OpponentArchetype.VERTICAL_DRUM)
        assert g.land_width == pytest.approx(g.head_thickness / 3.0)

    @pytest.mark.parametrize(
        "wc",
        [WeightClass.ONE_LB, WeightClass.THREE_LB, WeightClass.TWELVE_LB, WeightClass.THIRTY_LB],
    )
    def test_the_land_fits_inside_the_head(self, wc):
        """A land as wide as the head is a flat punch, and will not build."""
        g = geometry_for(wc, EnergyLevel.TYPICAL, OpponentArchetype.VERTICAL_DRUM)
        assert 0 < g.land_width < g.head_thickness

    def test_the_land_does_not_depend_on_the_mesh(self):
        """The previous rule did, which is why it did not scale."""
        spec = impactor_spec(WeightClass.ONE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        g = tooth_geometry(spec)
        assert g.land_width == pytest.approx(g.head_thickness / 3.0)


class TestBackingBlock:
    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_backing_is_cubic(self, wc, lv, ar):
        g = geometry_for(wc, lv, ar)
        assert g.back_volume == pytest.approx(g.back_section**3, rel=1e-9)

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_backing_dominates_the_head(self, wc, lv, ar):
        # m_eff is a third of a weapon, so the lumped block should carry the
        # overwhelming majority of the mass in every shipped preset.
        g = geometry_for(wc, lv, ar)
        assert g.back_volume > g.head_volume

    @pytest.mark.parametrize(("wc", "lv", "ar"), ALL_PRESETS)
    def test_backing_stays_a_meshable_size(self, wc, lv, ar):
        # Under ~60 mm at every preset, so at a 3 mm mesh the block costs a
        # few tens of thousands of elements, not hundreds of thousands.
        assert 10.0 < geometry_for(wc, lv, ar).back_section < 60.0


class TestDegenerateCases:
    def test_head_heavier_than_target_falls_back_to_density(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        # An absurdly light target for this radius: the head alone overshoots.
        light = type(spec)(**{**spec.__dict__, "m_eff_kg": 1e-6})
        g = tooth_geometry(light)
        assert g.back_section == 0.0
        assert g.clamped
        assert g.density_scale < 1.0
        assert g.achieved_mass_mg * 1000.0 == pytest.approx(1e-6, rel=1e-9)

    def test_zero_radius_is_rejected(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        broken = type(spec)(**{**spec.__dict__, "r_arc_mm": 0.0})
        with pytest.raises(ValueError, match="arc radius"):
            tooth_geometry(broken)

    def test_zero_density_is_rejected(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        with pytest.raises(ValueError, match="density"):
            tooth_geometry(spec, density=0.0)


class TestDescription:
    def test_describe_states_the_mass(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        text = tooth_geometry(spec).describe()
        assert f"{spec.m_eff_kg:.3f} kg" in text
        # No scaling to disclose when the block did its job.
        assert "density" not in text

    def test_describe_discloses_a_scaled_density(self):
        spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
        light = type(spec)(**{**spec.__dict__, "m_eff_kg": 1e-6})
        assert "density" in tooth_geometry(light).describe()


def test_s7_density_matches_the_material_library(materials_yaml):
    """The constant here has to track the plugin's steel_s7_tool entry."""
    text = materials_yaml.read_text()
    block = text.split("steel_s7_tool:", 1)[1]
    density = next(
        float(line.split(":", 1)[1].strip())
        for line in block.splitlines()
        if line.strip().startswith("density:")
    )
    assert density == pytest.approx(S7_DENSITY_MG_MM3)


def test_effective_mass_is_a_third_of_a_weapon():
    """Sanity on the physics the module docstring rests on.

    For a bar spun about its centre m_eff ~ M/3, and the weapon is a quarter
    of the class weight: 3 lb at 12 lb, so m_eff is about 0.45 kg.
    """
    spec = impactor_spec(WeightClass.TWELVE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)
    implied_weapon_kg = spec.m_eff_kg * 3.0
    assert implied_weapon_kg == pytest.approx(0.25 * 5.443, rel=0.03)
    # And m_eff is self-consistent with KE and tip speed by construction.
    assert spec.m_eff_kg == pytest.approx(2 * spec.ke_j / spec.v_tip_ms**2, rel=1e-6)
    assert math.isfinite(spec.r_arc_mm)


class TestHeadVolumeContract:
    """The mass model assumes a symmetric chisel, and says so.

    ``tooth_geometry`` sizes the backing as *whatever the head does not
    already account for*, from a head volume of ``w * t * L / 2`` -- a
    triangular prism, half its own bounding box. That is the contract
    ``simgeom.build_tooth`` has to satisfy, and today it does not: measured,
    it builds 0.505 of that, so the finished tooth is light.

    The geometric half of this cannot be tested here -- gmsh does not import
    in the development venv and pytest is not installed in the image that has
    it -- so ``cra.calibrate.tooth_shapes`` verifies that side on every run.
    These are the parts checkable with no wheels at all.
    """

    CLASSES = (
        WeightClass.ONE_LB,
        WeightClass.THREE_LB,
        WeightClass.TWELVE_LB,
        WeightClass.THIRTY_LB,
    )

    def _geom(self, wc):
        spec = impactor_spec(wc, EnergyLevel.TYPICAL, OpponentArchetype.VERTICAL_DRUM)
        return spec, tooth_geometry(spec)

    @pytest.mark.parametrize("wc", CLASSES)
    def test_the_head_is_modelled_as_the_trapezoid_it_is_built_as(self, wc):
        """The contract simgeom.build_tooth has to satisfy.

        It did not, for a long time: the geometry cut 0.505 of the modelled
        volume with its edge half a head-thickness off the axis it was aimed
        by. The geometric half of this check needs gmsh and lives in
        cra.calibrate.tooth_shapes, which verifies it on every run.
        """
        _spec, g = self._geom(wc)
        assert g.head_volume == pytest.approx(
            0.5 * (g.land_width + g.head_thickness) * g.head_length * g.head_width
        )

    @pytest.mark.parametrize("wc", CLASSES)
    def test_the_backing_is_whatever_the_head_does_not_account_for(self, wc):
        """So an undersized head is not compensated — it is simply missing."""
        spec, g = self._geom(wc)
        if g.back_section <= 0:
            pytest.skip("head alone outweighs this preset; the backing is clamped away")
        modelled = g.head_volume + g.back_section**3
        wanted = (spec.m_eff_kg * 1000.0) / (S7_DENSITY_MG_MM3 * 1e6)
        assert modelled == pytest.approx(wanted, rel=1e-6)

    @pytest.mark.parametrize("wc", CLASSES)
    def test_the_backing_still_has_room_for_a_fatter_head(self, wc):
        """A land makes the head heavier, and the backing absorbs it.

        The trapezoid is 1.333x the triangle at a land of t/3. tooth_geometry
        sizes the backing as whatever the head does not account for, so that
        has to stay positive -- if it clamped, the preset would start trimming
        with density instead, which is the thing the cubic backing exists to
        avoid.
        """
        _spec, g = self._geom(wc)
        assert g.back_section > 0, f"{wc.name}: the head alone now outweighs the preset"
        assert g.density_scale == pytest.approx(1.0, abs=1e-6)
