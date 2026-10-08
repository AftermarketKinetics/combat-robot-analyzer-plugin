---
name: weapon_size
---

# The opponent's weapon sized from its mass

A combat robot's weapon is about a quarter of its weight, in steel plate: a
bar for a horizontal spinner, a solid disc for a vertical or drum. Given the
class, that fixes the weapon's diameter and rim-effective mass, and
`impactor.impactor_spec` spins it at a preset RPM to get the tip speed and
energy. This replaced fixed diameter tables (v1's survey "OD high" row, then
hand-picked values), which left the drawn weapon and its inertia resting on
unrelated assumptions. Rule and per-class thicknesses are Lily's, 2026-10-08.

Every number can be overridden (Lily, 2026-10-08): `impactor_spec` passes
SETUP.json's `impactor` overrides straight through. The shape can be swapped
whatever the archetype; mass, thickness and outline are tied by steel's
density, so the user gives at most all but one of them and the rest are
derived.

```py-sig
def weapon_size(cls: WeightClass | str, archetype: OpponentArchetype | str,
                overrides: Mapping[str, Any] | None = None) -> WeaponSize
def default_shape(archetype: OpponentArchetype | str) -> str   # "bar" | "disc"

@dataclass(frozen=True)
class WeaponSize:
    shape: str                  # "bar" | "disc"
    mass_kg: float
    thickness_mm: float
    od_mm: float                # tip-to-tip for a bar
    bar_width_mm: float | None  # None for a disc
    m_eff_kg: float             # I / R**2
    radius_mm: float            # property, od_mm / 2

SHAPES = ("bar", "disc")
WEAPON_MASS_FRACTION = 0.25
PLATE_THICKNESS_MM: dict[WeightClass, float]   # 4 / 10 / 12 / 20
BAR_ASPECT = 6.0
STEEL_DENSITY_G_MM3 = 7.83e-3
```

```requires
cls and archetype are enum members or their string values
overrides keys used: weapon_shape (in SHAPES), weapon_mass_kg,
    plate_thickness_mm, weapon_od_mm, bar_width_mm; others are ignored
    (impactor_spec rejects unknown keys before calling); None means unset
each numeric override is a positive number (not bool), else ValueError
bar_width_mm only on a bar, else ValueError
not every one of mass, thickness and outline (disc: OD; bar: OD and width)
    given at once, else ValueError "... leave one of them unset ..."
```

```ensures
shape = overrides["weapon_shape"] or default_shape(archetype)
    (horizontal -> "bar", vertical_drum -> "disc")
plate area x thickness x density = mass_kg, always
every given override is kept exactly; missing values are filled from the
    defaults in order -- mass (WEAPON_MASS_FRACTION x weapon.OPPONENT_MASS_KG
    of the class), then thickness (PLATE_THICKNESS_MM) -- until one unknown
    remains, which is derived; a bar with mass and thickness but neither
    length nor width takes od / width = BAR_ASPECT
no overrides: mass_kg = ¼ class mass, thickness = PLATE_THICKNESS_MM[cls],
    bar 6:1
bar: width < od, else ValueError "... is not a bar ...";
    m_eff = M (L² + w²) / (3 L²)  (a little over M / 3)
disc: solid, m_eff = M / 2
```

```test
the_plate_weighs_a_quarter_of_the_class: (every class, both archetypes) => plate mass == mass_kg, thickness == PLATE_THICKNESS_MM
horizontal_is_a_bar_and_vertical_a_disc: ("12lb") => bar 6:1, disc m_eff = M/2
the_12lb_bar_is_about_300mm: ("12lb", horizontal) => od_mm ~ 295
a_bar_always_adds_up_and_keeps_what_was_given: ("12lb", horizontal, assorted overrides) => plate mass == mass_kg, given values kept
an_outline_alone_keeps_the_default_mass_and_derives_the_rest: ("12lb", horizontal, {weapon_od_mm: 250}) => mass ¼ x 5.443, thickness 12
a_disc_always_adds_up: ("3lb", vertical, assorted overrides) => plate mass == mass_kg
shape_can_be_swapped: (weapon_shape disc on horizontal / bar on vertical) => that shape
bad_overrides_are_refused: (all four given / bar_width on a disc / shape "ring" / negative / 40 mm bar) => ValueError
```

## Implementation

**A solid disc is compact**, so verticals come out much smaller than
horizontals of the same mass (12 lb: 136 mm disc, 295 mm bar), and at a given
RPM their tips move slower. Real verticals are often lightened discs, bars
or eggbeaters; the solid disc is the chosen simplification, and a user who
knows better overrides it.

**Fill order is mass, then thickness.** A user who gives only an outline
(say "a 250 mm bar") keeps the quarter-weight mass and the class thickness
and gets the remaining dimension derived; one who gives mass and OD gets the
class thickness and a derived width. Giving everything is refused rather than
silently ignoring one value, because steel density makes it over-determined.

**The plate thickness is also the tooth's default width and depth**
(`impactor.impactor_spec` reads `thickness_mm`), so one per-class number sets
both unless the tooth is overridden.

**The weapon's mass is not the hub's mass.** The hub rigid body carries the
whole opponent (`ImpactorSpec.opponent_mass_kg`, default
`weapon.OPPONENT_MASS_KG`), because the axle is in a robot; only the spin
inertia comes from here, via `m_eff × R²`.

Tests: `tests/test_weapon_size.py`.
