"""Sizing the opponent impactor.

PLAN.md §10 strikes the user's bot with a *parametric deformable tooth* of
hardened S7, scaled per weight class. Deformable matters: ``fe5-results.json``
measured the tooth taking 2.06 of damage against the plate's 3.98, so a rigid
tooth would misattribute about a third of the energy.

**The tooth cannot carry m_eff at steel density, and that is not a mistake in
the presets.** ``m_eff = 2*KE/v_tip**2`` is the *rim-effective* mass of the
whole weapon: for a bar spun about its centre ``I = M*L**2/12`` and ``R = L/2``,
so ``m_eff = I/R**2 = M/3``. The survey agrees — 12 lb high lists a 4.5-6 lb
weapon, a third of which is 680-900 g against the preset's 1042 g. Asking a
tooth-sized wedge to weigh that much needs a density around 204 g/cm3, ten
times osmium, which would raise the acoustic impedance ``sqrt(rho*E)`` by 5.1x
and cut the tooth's share of the absorbed energy from the measured ~34% to
~9%. That biases LC1's headline output in the user's favour, which is the one
direction a paid engineering report must never be wrong in.

So the impactor is two regions:

* a **head** — a real S7 wedge at the true density, modulus, impedance and
  yield. This is what touches the target, so this is what has to be right.
* a **backing block** — a cube whose side is solved so the pair weighs exactly
  ``m_eff`` at true S7 density. It is a lumped stand-in for the rest of the
  weapon, not the shape of any real one.

All sixteen ``(class, level, archetype)`` presets land at a density scale of
1.000, so nothing in the shipped configuration is running on a fudged density.
Scaling survives only for the degenerate case where the head alone already
outweighs the preset, and the factor is reported either way.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from case.models import ImpactorSpec

__all__ = [
    "BACKING_IS_CUBIC",
    "S7_DENSITY_MG_MM3",
    "ToothGeometry",
    "head_width",
    "tooth_geometry",
]

# steel_s7_tool from the plugin's configs/materials.yaml, in model units
# (mm_Mg_s), so 7.83e-9 Mg/mm3 = 7.83 g/cm3.
S7_DENSITY_MG_MM3 = 7.83e-9

# The model's mass unit is the tonne; the presets are in kilograms.
KG_PER_MG = 1000.0

# The head is the end of the real weapon (Lily, 2026-10-08), sized by
# `impactor.impactor_spec` (tooth_width_mm / tooth_depth_mm / tooth_length_mm,
# defaulting to the plate thickness, the plate thickness, and the bar's width
# or 0.3 x a disc's OD). Its three axes, as `simgeom.build_tooth` lays them out:
#   width     along the spin axis
#   thickness radial, out of the rim (the spec's tooth_depth_mm)
#   length    along the direction of travel
# These replaced fractions of the arc radius (width 0.15 R, thickness 0.035 R,
# length 2.5 x thickness), which looked plausible only against v1's oversized
# weapon diameters: at typical diameters they gave a 1 lb tooth 1.75 mm
# thick, under one 1.5 mm mesh element.

# The backing is a **cube**, which is the shape decision that makes the whole
# scheme work. Tying its section to the head width instead leaves the narrow
# presets starved: 12 lb vertical-drum has a 121 mm arc radius, so an 18 mm
# head cannot back 1.042 kg without growing into a bar, and the density scale
# blows out to 5.5x. A cube has unit aspect ratio by construction, so it never
# develops the axial modes a bar would feed back into the contact, and every
# preset lands at scale 1.000.
BACKING_IS_CUBIC = True

# The head strikes with a flat **land**, not a point, as a fraction of its own
# thickness. Two reasons, and the numerical one is the larger.
#
# Physically it is what a tooth is: drum and bar teeth are hardened steel with
# a flat or heavily radiused land, because a knife edge chips on first contact
# with armour. They deliver momentum and gouge; they do not cut.
#
# Numerically, a mathematically sharp edge drives the contact area to zero, so
# the pressure is unbounded and the answer depends on how finely the tip
# happened to be meshed. It also owns the timestep: measured at the production
# 1.5 mm mesh, a sharp tip produced a 0.0119 mm element, 126x finer than the
# mesh size and 12-17x finer than anything in the CAD being judged -- and dt is
# proportional to it, so every model in the class paid for it.
#
# Proportional rather than absolute, because absolute does not scale. The
# previous rule here asked for max(2.0 mm, 2 elements), which is 3 mm at the
# production mesh and wider than a 1 lb head is thick. A fraction of the
# thickness gives one rule that holds across every class: measured, t/8
# returns exactly 30.3x the smallest element and exactly 1.125x the head
# volume on all four, because the shape is geometrically similar.
LAND_FRACTION = 1.0 / 3.0


@dataclass(frozen=True)
class ToothGeometry:
    """Dimensions and mass bookkeeping for one impactor."""

    head_width: float
    head_thickness: float
    head_length: float
    land_width: float  # the flat that does the striking; see LAND_FRACTION

    back_section: float  # cube side; zero when the head alone suffices

    head_volume: float
    back_volume: float

    target_mass_mg: float
    density_scale: float
    clamped: bool

    @property
    def total_volume(self) -> float:
        return self.head_volume + self.back_volume

    @property
    def achieved_mass_mg(self) -> float:
        return self.total_volume * S7_DENSITY_MG_MM3 * self.density_scale

    @property
    def density_mg_mm3(self) -> float:
        """What the backing block's material card should declare."""
        return S7_DENSITY_MG_MM3 * self.density_scale

    @property
    def impedance_ratio(self) -> float:
        """How far the backing's acoustic impedance departs from real S7.

        ``Z = sqrt(rho*E)``, so scaling density alone moves Z by its square
        root. Only ever applies to the backing — the head keeps true S7.
        """
        return math.sqrt(self.density_scale)

    def describe(self) -> str:
        """One line for the report's assumptions block."""
        mass_kg = self.achieved_mass_mg * KG_PER_MG
        text = (
            f"S7 tooth {self.head_width:.1f} x {self.head_thickness:.1f} mm "
            f"backed by a {self.back_section:.1f} mm lumped inertia block, "
            f"{mass_kg:.3f} kg total"
        )
        if self.clamped:
            text += (
                f"; the block hit its aspect limit, so its density is "
                f"{self.density_scale:.2f}x that of S7"
            )
        return text


def head_width(impactor: ImpactorSpec) -> float:
    """The tooth's contact width across the strike, along the spin axis.

    Split out of ``tooth_geometry`` because target selection needs it before a
    mesh size exists.
    """
    width = float(impactor.tooth_width_mm)
    if width <= 0:
        raise ValueError(f"tooth width must be positive, got {width}")
    return width


def tooth_geometry(
    impactor: ImpactorSpec,
    *,
    density: float = S7_DENSITY_MG_MM3,
) -> ToothGeometry:
    """Size the impactor for one load case.

    The head's three sizes come from the spec (see the comment above
    ToothGeometry for its axes). The backing is then solved so
    the pair weighs ``m_eff`` at true density, which is what keeps the density
    scale at 1.0 and the head's impedance honest.
    """
    radius = float(impactor.r_arc_mm)
    if radius <= 0:
        raise ValueError(f"impactor arc radius must be positive, got {radius}")
    if density <= 0:
        raise ValueError(f"tooth density must be positive, got {density}")

    width = head_width(impactor)
    thickness = float(impactor.tooth_depth_mm)
    length = float(impactor.tooth_length_mm)
    if thickness <= 0 or length <= 0:
        raise ValueError(f"tooth depth and length must be positive, got {thickness}, {length}")
    land = LAND_FRACTION * thickness

    # Trapezoidal prism: the cross-section runs from the land at the tip to the
    # full thickness at the root, extruded across the width. A land of zero
    # collapses this to the triangle it used to be, so the two agree at the
    # limit -- which is the check that this is the same formula generalised
    # rather than a different one.
    head_volume = 0.5 * (land + thickness) * length * width

    target_mass_mg = float(impactor.m_eff_kg) / KG_PER_MG
    wanted_volume = target_mass_mg / density

    # Whatever the head does not already account for goes into the backing,
    # as a cube — see BACKING_IS_CUBIC.
    back_volume = wanted_volume - head_volume
    clamped = False

    if back_volume <= 0:
        # The head alone already outweighs the preset. Trim with density
        # rather than shrinking the contact patch, which is the part that has
        # to stay geometrically honest.
        back_volume = 0.0
        section = 0.0
        clamped = True
    else:
        section = back_volume ** (1.0 / 3.0)

    achieved_volume = head_volume + back_volume
    density_scale = target_mass_mg / (achieved_volume * density) if achieved_volume > 0 else 1.0

    return ToothGeometry(
        head_width=width,
        head_thickness=thickness,
        head_length=length,
        land_width=land,
        back_section=section,
        head_volume=head_volume,
        back_volume=back_volume,
        target_mass_mg=target_mass_mg,
        density_scale=density_scale,
        clamped=clamped,
    )
