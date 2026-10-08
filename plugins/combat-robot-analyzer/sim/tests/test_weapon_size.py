"""Weapon sizing from a quarter of the class weight, and its overrides."""

from __future__ import annotations

import math

import pytest

from case.models import OpponentArchetype, WeightClass
from case.weapon_size import BAR_ASPECT, PLATE_THICKNESS_MM, STEEL_DENSITY_G_MM3, weapon_size

H, V = OpponentArchetype.HORIZONTAL, OpponentArchetype.VERTICAL_DRUM


def plate_grams(s):
    area = s.od_mm * s.bar_width_mm if s.shape == "bar" else math.pi * s.od_mm**2 / 4.0
    return area * s.thickness_mm * STEEL_DENSITY_G_MM3


@pytest.mark.parametrize("wc", list(WeightClass))
@pytest.mark.parametrize("ar", [H, V])
def test_the_plate_weighs_a_quarter_of_the_class(wc, ar):
    s = weapon_size(wc, ar)
    assert plate_grams(s) / 1000.0 == pytest.approx(s.mass_kg)
    assert s.thickness_mm == PLATE_THICKNESS_MM[wc]


def test_horizontal_is_a_bar_and_vertical_a_disc():
    bar, disc = weapon_size("12lb", H), weapon_size("12lb", V)
    assert bar.shape == "bar" and bar.od_mm / bar.bar_width_mm == pytest.approx(BAR_ASPECT)
    assert disc.shape == "disc" and disc.bar_width_mm is None
    assert disc.m_eff_kg == pytest.approx(disc.mass_kg / 2.0)


def test_the_12lb_bar_is_about_300mm():
    # The rule reproduces Lily's hand-picked 300 mm 12 lb horizontal.
    assert weapon_size("12lb", H).od_mm == pytest.approx(295.0, rel=0.01)


class TestOverrides:
    @pytest.mark.parametrize("o", [
        {"weapon_od_mm": 250.0},
        {"bar_width_mm": 40.0},
        {"weapon_od_mm": 250.0, "bar_width_mm": 40.0},
        {"weapon_mass_kg": 1.0, "weapon_od_mm": 250.0},
        {"plate_thickness_mm": 8.0},
        {"weapon_mass_kg": 2.0},
        {"plate_thickness_mm": 8.0, "weapon_od_mm": 300.0, "bar_width_mm": 40.0},
    ])
    def test_a_bar_always_adds_up_and_keeps_what_was_given(self, o):
        s = weapon_size("12lb", H, o)
        assert plate_grams(s) / 1000.0 == pytest.approx(s.mass_kg)
        given = {"weapon_od_mm": s.od_mm, "bar_width_mm": s.bar_width_mm,
                 "weapon_mass_kg": s.mass_kg, "plate_thickness_mm": s.thickness_mm}
        for k, v in o.items():
            assert given[k] == pytest.approx(v)

    def test_an_outline_alone_keeps_the_default_mass_and_derives_the_rest(self):
        s = weapon_size("12lb", H, {"weapon_od_mm": 250.0})
        assert s.mass_kg == pytest.approx(0.25 * 5.443)
        assert s.thickness_mm == 12.0

    @pytest.mark.parametrize("o", [{"weapon_od_mm": 120.0}, {"weapon_mass_kg": 0.5},
                                   {"weapon_od_mm": 120.0, "plate_thickness_mm": 6.0}])
    def test_a_disc_always_adds_up(self, o):
        s = weapon_size("3lb", V, o)
        assert plate_grams(s) / 1000.0 == pytest.approx(s.mass_kg)

    def test_shape_can_be_swapped(self):
        assert weapon_size("12lb", H, {"weapon_shape": "disc"}).shape == "disc"
        assert weapon_size("12lb", V, {"weapon_shape": "bar"}).shape == "bar"

    @pytest.mark.parametrize(("arch", "o", "match"), [
        (H, {"weapon_mass_kg": 1.0, "plate_thickness_mm": 10.0, "weapon_od_mm": 300.0,
             "bar_width_mm": 40.0}, "leave one"),
        (V, {"weapon_mass_kg": 1.0, "plate_thickness_mm": 10.0, "weapon_od_mm": 200.0},
         "leave one"),
        (V, {"bar_width_mm": 30.0}, "disc"),
        (H, {"weapon_shape": "ring"}, "weapon_shape"),
        (H, {"weapon_od_mm": -3}, "positive"),
        (H, {"weapon_od_mm": 40.0}, "not a bar"),
    ])
    def test_bad_overrides_are_refused(self, arch, o, match):
        with pytest.raises(ValueError, match=match):
            weapon_size("12lb", arch, o)
