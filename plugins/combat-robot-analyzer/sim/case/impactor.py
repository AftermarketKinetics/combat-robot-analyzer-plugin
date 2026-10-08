"""Opponent impactor presets: the weapon's size, spin and tooth.

The weapon is sized from its mass (`weapon_size.py`: a quarter of the class
weight, a steel bar or disc), and spun at a preset RPM, so the tip speed and
the kinetic energy follow:

    v_tip = RPM x 2 pi / 60 x R        KE = 1/2 m_eff v_tip**2

v1 and an earlier v2 took the survey's energy as given instead; on the
quarter-weight weapons that needed up to 454 mph at the rim. Every number can
be overridden from SETUP.json's `impactor` block (OVERRIDE_KEYS); the spin is
given as at most one of `rpm`, `tip_speed_ms` or `energy_j`.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from typing import Any

from case.models import EnergyLevel, ImpactorSpec, OpponentArchetype, WeightClass
from case.weapon import OPPONENT_MASS_KG
from case.weapon_size import weapon_size

__all__ = [
    "OVERRIDE_KEYS",
    "RPM_PRESETS",
    "TIP_SPEED_WARN_MS",
    "TOOTH_MATERIAL",
    "impactor_spec",
    "spec_from_setup",
    "weapon_card",
    "tip_speed_ms",
]

# Hardened S7 tool steel, keyed into the openradioss-sim material library.
TOOTH_MATERIAL = "steel_s7_tool"

_H, _V = "bar", "disc"  # weapon shapes (weapon_size.SHAPES)
_T, _X = EnergyLevel.TYPICAL, EnergyLevel.HIGH

#: (class, weapon shape, level) -> RPM. Estimates (2026-10-08) chosen so every
#: preset stays under TIP_SPEED_WARN_MS: small discs spin faster than long
#: bars, high is about 1.35x typical. Keyed by shape, not archetype: a
#: horizontal spinner overridden to a disc got the bar's 6,000 RPM, which on a
#: 136 mm disc is 95 mph and 619 J.
RPM_PRESETS: dict[tuple[WeightClass, str, EnergyLevel], float] = {
    (WeightClass.ONE_LB, _H, _T): 12000.0, (WeightClass.ONE_LB, _H, _X): 16000.0,
    (WeightClass.ONE_LB, _V, _T): 20000.0, (WeightClass.ONE_LB, _V, _X): 25000.0,
    (WeightClass.THREE_LB, _H, _T): 10000.0, (WeightClass.THREE_LB, _H, _X): 14000.0,
    (WeightClass.THREE_LB, _V, _T): 18000.0, (WeightClass.THREE_LB, _V, _X): 24000.0,
    (WeightClass.TWELVE_LB, _H, _T): 6000.0, (WeightClass.TWELVE_LB, _H, _X): 8000.0,
    (WeightClass.TWELVE_LB, _V, _T): 10000.0, (WeightClass.TWELVE_LB, _V, _X): 14000.0,
    (WeightClass.THIRTY_LB, _H, _T): 4000.0, (WeightClass.THIRTY_LB, _H, _X): 5500.0,
    (WeightClass.THIRTY_LB, _V, _T): 7000.0, (WeightClass.THIRTY_LB, _V, _X): 9500.0,
}

#: 300 mph. Past it a weapon is not believable (Lily, 2026-10-08); a custom
#: weapon may still exceed it, with a warning the agent must confirm.
TIP_SPEED_WARN_MS = 300 * 0.44704

#: The SETUP.json `impactor` keys that override a preset; null or absent
#: keeps the preset.
SPIN_KEYS = ("rpm", "tip_speed_ms", "energy_j")
OVERRIDE_KEYS = ("weapon_shape", "weapon_mass_kg", "plate_thickness_mm", "weapon_od_mm",
                 "bar_width_mm", *SPIN_KEYS, "tooth_width_mm", "tooth_depth_mm",
                 "tooth_length_mm", "opponent_mass_kg")

#: A disc's tooth runs this fraction of its diameter along the travel.
DISC_TOOTH_LENGTH_FRACTION = 0.3

_SIZE_CAVEAT = (
    "Unless overridden, the opponent's weapon is estimated, not measured: a "
    "quarter of the class weight in steel, a 6:1 bar for a horizontal spinner "
    "and a solid disc for a vertical or drum, at a nominal plate thickness and "
    "RPM per class."
)
_NOMINAL_CAVEAT = (
    "The impactor is keyed to the nominal weight class, not to any bonused "
    "allowance: a walker's opponent is still a normal bot of that class."
)


def _key(
    cls: WeightClass | str, level: EnergyLevel | str, archetype: OpponentArchetype | str
) -> tuple[WeightClass, EnergyLevel, OpponentArchetype]:
    try:
        weight_class = WeightClass(cls)
    except ValueError:
        allowed = ", ".join(c.value for c in WeightClass)
        raise ValueError(f"unknown weight class {cls!r}; expected one of {allowed}") from None
    try:
        energy = EnergyLevel(level)
    except ValueError:
        allowed = ", ".join(level_.value for level_ in EnergyLevel)
        raise ValueError(f"unknown energy level {level!r}; expected one of {allowed}") from None
    try:
        arch = OpponentArchetype(archetype)
    except ValueError:
        allowed = ", ".join(a.value for a in OpponentArchetype)
        raise ValueError(
            f"unknown opponent archetype {archetype!r}; expected one of {allowed}"
        ) from None
    return weight_class, energy, arch


def tip_speed_ms(rpm: float, radius_mm: float) -> float:
    return rpm * 2.0 * math.pi / 60.0 * radius_mm / 1000.0


def _positive(o: Mapping[str, Any], key: str) -> float | None:
    v = o.get(key)
    if v is None:
        return None
    if isinstance(v, bool) or not isinstance(v, (int, float)) or not v > 0:
        raise ValueError(f"{key} must be a positive number, got {v!r}")
    return float(v)


def impactor_spec(
    cls: WeightClass | str,
    level: EnergyLevel | str,
    archetype: OpponentArchetype | str,
    overrides: Mapping[str, Any] | None = None,
) -> ImpactorSpec:
    """Resolve the dropdowns and any overrides into a full weapon specification.

    Takes no bonus argument by design -- see :data:`_NOMINAL_CAVEAT`. Raises
    ValueError naming the offending key on a bad or contradictory override.
    """
    weight_class, energy, arch = _key(cls, level, archetype)
    o = {k: v for k, v in (overrides or {}).items() if v is not None}
    unknown = sorted(set(o) - set(OVERRIDE_KEYS))
    if unknown:
        raise ValueError(f"unknown impactor setting {', '.join(unknown)}")
    size = weapon_size(weight_class, arch, o)
    radius = size.radius_mm

    spin = [k for k in SPIN_KEYS if k in o]
    if len(spin) > 1:
        raise ValueError(f"give one of rpm, tip_speed_ms or energy_j, not {' and '.join(spin)}: "
                         "the weapon's mass ties them together")
    if "tip_speed_ms" in o:
        v = _positive(o, "tip_speed_ms")
    elif "energy_j" in o:
        v = math.sqrt(2.0 * _positive(o, "energy_j") / size.m_eff_kg)
    else:
        rpm = _positive(o, "rpm") or RPM_PRESETS[(weight_class, size.shape, energy)]
        v = tip_speed_ms(rpm, radius)
    rpm = v * 1000.0 / radius * 60.0 / (2.0 * math.pi)
    ke = 0.5 * size.m_eff_kg * v * v

    if size.bar_width_mm is not None:
        default_length = size.bar_width_mm
    else:
        default_length = DISC_TOOTH_LENGTH_FRACTION * size.od_mm
    tooth_width = _positive(o, "tooth_width_mm") or size.thickness_mm
    tooth_depth = _positive(o, "tooth_depth_mm") or size.thickness_mm
    tooth_length = _positive(o, "tooth_length_mm") or default_length
    opponent = _positive(o, "opponent_mass_kg") or OPPONENT_MASS_KG[weight_class.value]
    if size.mass_kg >= opponent:
        raise ValueError(f"the weapon ({size.mass_kg:.3f} kg) cannot outweigh the robot "
                         f"carrying it ({opponent:.3f} kg)")

    warnings = []
    if v > TIP_SPEED_WARN_MS:
        warnings.append(f"tip speed {v:.0f} m/s ({v / 0.44704:.0f} mph) is above 300 mph, "
                        "faster than real weapons run")

    return ImpactorSpec(
        weight_class=weight_class,
        energy_level=energy,
        archetype=arch,
        ke_j=ke,
        v_tip_ms=v,
        m_eff_kg=size.m_eff_kg,
        r_arc_mm=radius,
        material=TOOTH_MATERIAL,
        assumptions=(_NOMINAL_CAVEAT, _SIZE_CAVEAT),
        rpm=rpm,
        weapon_shape=size.shape,
        weapon_mass_kg=size.mass_kg,
        plate_thickness_mm=size.thickness_mm,
        weapon_od_mm=size.od_mm,
        bar_width_mm=size.bar_width_mm,
        tooth_width_mm=tooth_width,
        tooth_depth_mm=tooth_depth,
        tooth_length_mm=tooth_length,
        opponent_mass_kg=opponent,
        overrides=tuple(sorted(o)),
        warnings=tuple(warnings),
    )


def spec_from_setup(block: Mapping[str, Any]) -> ImpactorSpec:
    """SETUP.json's `impactor` block: the three dropdowns plus any overrides."""
    return impactor_spec(block.get("weight_class"), block.get("energy_level", "typical"),
                         block.get("archetype", "vertical_drum"),
                         {k: block.get(k) for k in OVERRIDE_KEYS})


def weapon_card(spec: ImpactorSpec) -> dict[str, Any]:
    """Every resolved weapon number, rounded for people: what the agent reports
    and the UI's weapon card shows. `custom` names the overridden keys."""
    def r(x: float | None, n: int = 1) -> float | None:
        return None if x is None else round(float(x), n)
    return {
        "weight_class": str(spec.weight_class), "energy_level": str(spec.energy_level),
        "archetype": str(spec.archetype), "shape": spec.weapon_shape,
        "weapon_mass_kg": r(spec.weapon_mass_kg, 3), "plate_thickness_mm": r(spec.plate_thickness_mm),
        "weapon_od_mm": r(spec.weapon_od_mm), "bar_width_mm": r(spec.bar_width_mm),
        "rpm": round(spec.rpm), "tip_speed_ms": r(spec.v_tip_ms),
        "tip_speed_mph": r(spec.v_tip_ms / 0.44704, 0), "energy_j": round(spec.ke_j),
        "effective_mass_kg": r(spec.m_eff_kg, 3),
        "tooth_mm": [r(spec.tooth_width_mm), r(spec.tooth_depth_mm), r(spec.tooth_length_mm)],
        "opponent_mass_kg": r(spec.opponent_mass_kg, 3),
        "custom": list(spec.overrides), "warnings": list(spec.warnings),
    }
