"""Spin presets and overrides in impactor_spec."""

from __future__ import annotations

import math

import pytest

from case.impactor import RPM_PRESETS, TIP_SPEED_WARN_MS, impactor_spec
from case.models import EnergyLevel, OpponentArchetype, WeightClass


@pytest.mark.parametrize("key", list(RPM_PRESETS))
def test_every_preset_is_under_300_mph_and_self_consistent(key):
    arch = "horizontal" if key[1] == "bar" else "vertical_drum"
    spec = impactor_spec(key[0], key[2], arch)
    assert spec.v_tip_ms < TIP_SPEED_WARN_MS and not spec.warnings
    assert spec.rpm == pytest.approx(RPM_PRESETS[key])
    assert spec.v_tip_ms == pytest.approx(spec.rpm * 2 * math.pi / 60 * spec.r_arc_mm / 1000)
    assert spec.ke_j == pytest.approx(0.5 * spec.m_eff_kg * spec.v_tip_ms**2)


def test_high_spins_faster_than_typical():
    for wc in WeightClass:
        for ar in OpponentArchetype:
            assert (impactor_spec(wc, "high", ar).ke_j > impactor_spec(wc, "typical", ar).ke_j)


@pytest.mark.parametrize(("o", "field", "value"), [
    ({"rpm": 9000}, "rpm", 9000.0),
    ({"tip_speed_ms": 100}, "v_tip_ms", 100.0),
    ({"energy_j": 3000}, "ke_j", 3000.0),
])
def test_one_spin_input_sets_the_rest(o, field, value):
    spec = impactor_spec("12lb", "typical", "horizontal", o)
    assert getattr(spec, field) == pytest.approx(value)
    assert spec.ke_j == pytest.approx(0.5 * spec.m_eff_kg * spec.v_tip_ms**2)
    assert spec.overrides == tuple(o)


def test_two_spin_inputs_are_refused():
    with pytest.raises(ValueError, match="one of rpm"):
        impactor_spec("12lb", "typical", "horizontal", {"rpm": 9000, "energy_j": 3000})


def test_over_300_mph_warns_but_is_allowed():
    spec = impactor_spec("12lb", "typical", "horizontal", {"tip_speed_ms": 150})
    assert spec.v_tip_ms == 150.0 and "300 mph" in spec.warnings[0]


def test_tooth_defaults_to_the_end_of_the_weapon_and_can_be_overridden():
    spec = impactor_spec("12lb", "typical", "horizontal")
    assert (spec.tooth_width_mm, spec.tooth_depth_mm) == (12.0, 12.0)
    assert spec.tooth_length_mm == pytest.approx(spec.bar_width_mm)
    disc = impactor_spec("12lb", "typical", "vertical_drum")
    assert disc.tooth_length_mm == pytest.approx(0.3 * disc.weapon_od_mm)
    custom = impactor_spec("12lb", "typical", "horizontal",
                           {"tooth_width_mm": 20, "tooth_depth_mm": 8, "tooth_length_mm": 30})
    assert (custom.tooth_width_mm, custom.tooth_depth_mm, custom.tooth_length_mm) == (20, 8, 30)


def test_opponent_mass_defaults_to_the_class_and_must_exceed_the_weapon():
    assert impactor_spec("12lb", "typical", "horizontal").opponent_mass_kg == pytest.approx(5.443)
    with pytest.raises(ValueError, match="outweigh"):
        impactor_spec("12lb", "typical", "horizontal", {"weapon_mass_kg": 6.0})


def test_null_overrides_are_ignored_and_unknown_keys_refused():
    assert impactor_spec("1lb", "typical", "horizontal", {"rpm": None}).overrides == ()
    with pytest.raises(ValueError, match="unknown impactor setting"):
        impactor_spec("1lb", "typical", "horizontal", {"rmp": 5})


def test_energy_level_enum_still_validates():
    with pytest.raises(ValueError, match="energy level"):
        impactor_spec("1lb", "extreme", "horizontal")
    assert impactor_spec(WeightClass.ONE_LB, EnergyLevel.HIGH, OpponentArchetype.HORIZONTAL)


def test_case_cli_weapon_prints_the_card_or_an_error(tmp_path):
    # A subprocess: case_cli.main points fd 1 at stderr for good.
    import json
    import subprocess
    import sys
    from pathlib import Path

    cli = Path(__file__).resolve().parents[1] / "case_cli.py"
    setup = tmp_path / "setup.json"

    def run(block):
        setup.write_text(json.dumps({"impactor": block}))
        p = subprocess.run([sys.executable, str(cli), "weapon", str(setup)],
                           capture_output=True, text=True, check=False)
        return p.returncode, json.loads(p.stdout)

    code, card = run({"weight_class": "12lb", "archetype": "horizontal", "rpm": 7000})
    assert code == 0 and card["rpm"] == 7000 and card["custom"] == ["rpm"] and card["shape"] == "bar"
    code, out = run({"weight_class": "12lb", "rpm": -1})
    assert code == 1 and "positive" in out["error"]


def test_the_rpm_preset_follows_the_shape_not_the_archetype():
    disc = impactor_spec("12lb", "typical", "horizontal", {"weapon_shape": "disc"})
    assert disc.rpm == pytest.approx(RPM_PRESETS[(WeightClass.TWELVE_LB, "disc", EnergyLevel.TYPICAL)])
    bar = impactor_spec("12lb", "typical", "vertical_drum", {"weapon_shape": "bar"})
    assert bar.rpm == pytest.approx(RPM_PRESETS[(WeightClass.TWELVE_LB, "bar", EnergyLevel.TYPICAL)])
