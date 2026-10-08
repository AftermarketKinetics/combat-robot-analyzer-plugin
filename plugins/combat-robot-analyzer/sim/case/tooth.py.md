# tooth.py

## Function

Builds the opponent's tooth from the resolved `ImpactorSpec`. Copied from v1
(`cra.tooth`); imports rewritten to `case.`. Changed from v1 in where the
head's size comes from: since 2026-10-08 `impactor.impactor_spec` sizes it
(`tooth_width_mm`, `tooth_depth_mm`, `tooth_length_mm`, user-overridable) and
this module reads it off the spec, instead of taking fractions of the radius.

In v1 (PLAN.md §10) this sized a *deformable* S7 tooth — a head plus a lumped
backing cube solved so the pair weighed `m_eff` — that was fired at the target.
**v2 uses only the head, and the head is rigid** (2026-10-08 impact-physics
rework): `loadcase.build_case` takes `tooth_geometry(impactor)` and zeroes
`back_section` / `back_volume`, the head becomes part of the weapon's `/RBODY`,
and the weapon's mass and spin inertia live on the hub node instead of in a
backing block (`weapon.py`, `loadcase.deck_spec`). In v2 this module therefore
supplies the head's shape (width, thickness, length, land) and its volume —
`deck_spec` subtracts the head's own inertia (`head_volume x S7 density x R²`)
from the hub's — and nothing it computes about the backing reaches the solve.

## Interface

- `tooth_geometry(impactor, *, density=S7_DENSITY_MG_MM3) -> ToothGeometry` —
  the sized tooth (head and v1 backing).
- `ToothGeometry` (frozen dataclass) — `head_width`, `head_thickness`,
  `head_length`, `land_width`, `back_section`, `head_volume`, `back_volume`,
  `target_mass_mg`, `density_scale`, `clamped`; properties `total_volume`,
  `achieved_mass_mg`, `density_mg_mm3`, `impedance_ratio`; `describe()`.
- `head_width(impactor) -> float` — `impactor.tooth_width_mm` (ValueError if
  not positive); split out because target selection needs it before a mesh
  size exists.
- `LAND_FRACTION` — sizes the land proportionally (not in `__all__`; v1's
  `cra.calibrate.tooth_shapes` imported it).
- `S7_DENSITY_MG_MM3`, `BACKING_IS_CUBIC` (the `__all__` constants);
  `KG_PER_MG` is module-level but not exported (`loadcase` imports it).
- `tooth_geometry` raises ValueError on a non-positive arc radius, density,
  tooth depth or tooth length.

## Implementation

**v1's reason for deformable no longer holds in v2's model.** v1 kept the tooth
deformable because `fe5-results.json` measured it taking 2.06 of damage against
the plate's 3.98, so a rigid tooth would misattribute about a third of the
energy. v2 chose a rigid head on a spinning rigid body anyway: the head is
meshed only for its contact shape and given a plain elastic S7 card
(`tooth_rigid`), which sets contact stiffness and the timestep, not
deformation. The module docstring still argues the v1 case.

**The tooth cannot carry `m_eff` at steel density, and that is not a mistake in
the presets.** `m_eff` is the *rim-effective* mass of the whole weapon: for a
bar spun about its centre `I = M·L²/12` and `R = L/2`, so `m_eff = I/R² = M/3`
(since 2026-10-08 `weapon_size.py` computes it from a quarter-weight bar or
disc; v1 derived it as `2·KE/v_tip²` from the survey). In v1, asking a
tooth-sized wedge to weigh the 12 lb high preset's 1042 g needed a density
around 204 g/cm³, ten times osmium, which would raise the acoustic impedance
`sqrt(rho·E)` by 5.1×. The module docstring and comments still quote v1's
numbers. v1 solved this
with the backing cube (`BACKING_IS_CUBIC`, `density_scale`); v2 solves it by
putting `m_eff·R²` (less the head's share) on the weapon's hub as rotational
inertia, so the backing fields are computed and then discarded.

**The head is the end of the opponent's weapon** (Lily, 2026-10-08), on the
axes `simgeom.build_tooth` lays out: width along the spin axis =
`spec.tooth_width_mm`; thickness, radial out of the rim =
`spec.tooth_depth_mm`; length along the travel = `spec.tooth_length_mm`.
`impactor_spec` defaults them to the plate thickness, the plate thickness
(4 / 10 / 12 / 20 mm), and the bar's width or 0.3 × a disc's OD
(`impactor.DISC_TOOTH_LENGTH_FRACTION`), and the user may override each. All
three were fractions of the arc radius before, which looked
right only against v1's oversized weapon diameters: at typical diameters the
thickness rule gave a 1 lb tooth 1.75 mm thick, under one mesh element. A 12 lb
horizontal head is now 12 × 12 × 49 mm (it was 41.9 × 9.8 × 24.4).

**The striking edge is a proportional flat land.** `LAND_FRACTION` sizes
`land_width`, and `simgeom.build_tooth` consumes it. The proportion matters:
an earlier edge-radius field was computed, stored, and read by nothing — the
edge shipped mathematically sharp, and that single defect had five symptoms
across the corpus. In v1 `cra.calibrate.tooth_shapes` (not copied to v2)
measured the *shipped* construction against what this module assumes — not a
frozen copy of it — and exited non-zero on disagreement. The land still
matters for a rigid head: it sets the contact patch, and
`weapon.estimate_end_time` uses `head_width * head_thickness` as the face area.

**`describe()` and `impedance_ratio` are v1-only.** Both describe the backing
block ("… backed by a … lumped inertia block", the backing's impedance
departure). v2's build path does not read them: `case_cli.py build` gives the
agent its own one-line `tooth.summary` built from the head's dimensions.

## Assertions

- **The striking edge has a land, not a point.** A sharp point drives the
  contact area to zero; a land spreads the same momentum over 25.6 mm².
  `tests/test_tooth.py` pins the land at a third of the head thickness and
  inside it; v1's `cra.calibrate.tooth_shapes` verified it against the real
  construction.
- **The head's shape and volume are what v2 reads.** `head_volume` feeds the
  hub-inertia subtraction in `loadcase.deck_spec`; changing the head formula
  changes the weapon's total spin energy unless both move together
  (`tests/test_loadcase.py::TestDeckSpec::test_the_weapon_spins_about_its_hub_with_the_presets_energy`).
- **Every value this module computes was meant to be read.** The one that was
  not (the edge radius) is the defect above. In v2 the backing fields are
  knowingly unread (zeroed by `build_case`), and so are `describe()` and
  `impedance_ratio`: they are v1-only. Changed deliberately on 2026-10-08 —
  before then `case_cli.py build` passed both to the agent, where they
  described a backing block v2 no longer has.
