"""The opponent's weapon sized from its mass.

A combat robot's weapon is roughly a quarter of its weight, and it is a steel
plate: a bar for a horizontal spinner, a disc for a vertical or drum. Given the
class, that fixes the weapon's diameter and its rotational inertia. This
replaced fixed diameter tables (first v1's survey "OD high" row, then
hand-picked values), which left the drawn weapon and its inertia resting on
unrelated assumptions.

Every number can be overridden. A plate's mass, thickness and outline are tied
by steel's density, so of mass, thickness, diameter and (for a bar) width a
user gives at most all but one; the rest are filled from the defaults in the
order mass, then thickness, and a bar with neither length nor width given
takes BAR_ASPECT.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from case.models import OpponentArchetype, WeightClass
from case.weapon import OPPONENT_MASS_KG

__all__ = ["BAR_ASPECT", "PLATE_THICKNESS_MM", "SHAPES", "WEAPON_MASS_FRACTION", "WeaponSize",
           "default_shape", "weapon_size"]

#: Weapon mass as a fraction of the nominal class weight (Lily, 2026-10-08).
WEAPON_MASS_FRACTION = 0.25

#: Plate thickness per class, mm (Lily, 2026-10-08). The tooth head defaults
#: to the same thickness (`impactor.impactor_spec`).
PLATE_THICKNESS_MM: dict[WeightClass, float] = {
    WeightClass.ONE_LB: 4.0,
    WeightClass.THREE_LB: 10.0,
    WeightClass.TWELVE_LB: 12.0,
    WeightClass.THIRTY_LB: 20.0,
}

#: A horizontal bar's length over its width.
BAR_ASPECT = 6.0

#: Steel, g/mm³ (S7's 7.83 g/cm³, as the tooth uses).
STEEL_DENSITY_G_MM3 = 7.83e-3

SHAPES = ("bar", "disc")


@dataclass(frozen=True)
class WeaponSize:
    shape: str  #: "bar" or "disc"
    mass_kg: float
    thickness_mm: float
    od_mm: float  #: tip-to-tip for a bar
    bar_width_mm: float | None  #: None for a disc
    m_eff_kg: float  #: rim-effective mass, I / R²

    @property
    def radius_mm(self) -> float:
        return self.od_mm / 2.0


def default_shape(archetype: OpponentArchetype | str) -> str:
    return "bar" if OpponentArchetype(archetype) is OpponentArchetype.HORIZONTAL else "disc"


def _positive(overrides: Mapping[str, Any], key: str) -> float | None:
    value = overrides.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not value > 0:
        raise ValueError(f"{key} must be a positive number, got {value!r}")
    return float(value)


def weapon_size(
    cls: WeightClass | str,
    archetype: OpponentArchetype | str,
    overrides: Mapping[str, Any] | None = None,
) -> WeaponSize:
    """The weapon of a `cls` robot of the given archetype.

    `overrides` may set `weapon_shape` ("bar"/"disc"), `weapon_mass_kg`,
    `plate_thickness_mm`, `weapon_od_mm` and (bar only) `bar_width_mm`.

    Plate area = mass / (density x thickness). A bar has
    I = M (L² + w²) / 12 about its centre, so m_eff = I / (L/2)²; a solid disc
    has m_eff = M / 2.
    """
    weight_class = WeightClass(cls)
    o = overrides or {}
    shape = o.get("weapon_shape") or default_shape(archetype)
    if shape not in SHAPES:
        raise ValueError(f"weapon_shape must be one of {', '.join(SHAPES)}, got {shape!r}")
    mass_g = _positive(o, "weapon_mass_kg")
    mass_g = mass_g * 1000.0 if mass_g is not None else None
    t = _positive(o, "plate_thickness_mm")
    length = _positive(o, "weapon_od_mm")
    width = _positive(o, "bar_width_mm")
    if shape == "disc" and width is not None:
        raise ValueError("bar_width_mm applies to a bar; this weapon is a disc")

    outline = [length] if shape == "disc" else [length, width]
    given = sum(v is not None for v in (mass_g, t, *outline))
    if given == 2 + len(outline):
        raise ValueError(
            "weapon mass, plate thickness and outline are tied by steel's density; "
            "leave one of them unset so it can be derived")
    # Fill from the defaults, mass first, until one unknown remains.
    if mass_g is None and given < 1 + len(outline):
        mass_g = WEAPON_MASS_FRACTION * OPPONENT_MASS_KG[weight_class.value] * 1000.0
        given += 1
    if t is None and given < 1 + len(outline):
        t = PLATE_THICKNESS_MM[weight_class]
        given += 1

    if shape == "disc":
        if mass_g is None:
            mass_g = STEEL_DENSITY_G_MM3 * t * math.pi * length**2 / 4.0
        elif t is None:
            t = mass_g / (STEEL_DENSITY_G_MM3 * math.pi * length**2 / 4.0)
        else:
            length = math.sqrt(4.0 * mass_g / (STEEL_DENSITY_G_MM3 * t) / math.pi)
        mass_kg = mass_g / 1000.0
        return WeaponSize("disc", mass_kg, t, length, None, mass_kg / 2.0)

    if mass_g is None:
        mass_g = STEEL_DENSITY_G_MM3 * t * length * width
    elif t is None:
        t = mass_g / (STEEL_DENSITY_G_MM3 * length * width)
    else:
        area = mass_g / (STEEL_DENSITY_G_MM3 * t)
        if length is None and width is None:
            length = math.sqrt(BAR_ASPECT * area)
            width = length / BAR_ASPECT
        elif length is None:
            length = area / width
        else:
            width = area / length
    if width >= length:
        raise ValueError(f"a bar {length:.0f} mm long and {width:.0f} mm wide is not a bar; "
                         "check weapon_od_mm and bar_width_mm")
    mass_kg = mass_g / 1000.0
    m_eff = mass_kg * (length**2 + width**2) / (3.0 * length**2)
    return WeaponSize("bar", mass_kg, t, length, width, m_eff)
