---
name: impactor
---

# Opponent impactor presets: the weapon's size, spin and tooth

The weapon is sized from its mass by `weapon_size.py` (a quarter of the class
weight in steel: a bar for horizontal, a disc for vertical/drum), which fixes
its radius and rim-effective mass. It is spun at a preset RPM per
`(class, archetype, level)`, so tip speed and kinetic energy follow:
`v_tip = RPM · 2π/60 · R`, `KE = ½ m_eff v_tip²`. In v2, `case_cli.py`
resolves SETUP.json's `impactor` block through `spec_from_setup` for `scope`,
`weapon` and `build`.

Copied from v1 (`cra.rules.impactor`) and rewritten on 2026-10-08 (Lily): v1
and an earlier v2 took the community survey's energy (PLAN.md §10) as given,
which on quarter-weight weapons needed up to 454 mph at the rim. Energy is now
an output. The RPM presets are estimates chosen so every preset stays under
300 mph (fastest: 1 lb and 12 lb horizontal high, ~276 mph). Every number can
be overridden by the user through the agent (`OVERRIDE_KEYS`); a resolved
tip speed over `TIP_SPEED_WARN_MS` is allowed but carries a warning the agent
must confirm with the user.

```py-sig
def impactor_spec(cls: WeightClass | str, level: EnergyLevel | str,
                  archetype: OpponentArchetype | str,
                  overrides: Mapping[str, Any] | None = None) -> ImpactorSpec
def spec_from_setup(block: Mapping[str, Any]) -> ImpactorSpec
def weapon_card(spec: ImpactorSpec) -> dict[str, Any]
def tip_speed_ms(rpm: float, radius_mm: float) -> float

RPM_PRESETS: dict[tuple[WeightClass, str, EnergyLevel], float]   # (class, "bar"|"disc", level)
    # typical / high RPM, horizontal then vertical_drum:
    # 1lb 12k/16k, 20k/25k; 3lb 10k/14k, 18k/24k;
    # 12lb 6k/8k, 10k/14k; 30lb 4k/5.5k, 7k/9.5k
TIP_SPEED_WARN_MS = 300 mph (134.1 m/s)
OVERRIDE_KEYS = ("weapon_shape", "weapon_mass_kg", "plate_thickness_mm",
                 "weapon_od_mm", "bar_width_mm", "rpm", "tip_speed_ms",
                 "energy_j", "tooth_width_mm", "tooth_depth_mm",
                 "tooth_length_mm", "opponent_mass_kg")
DISC_TOOTH_LENGTH_FRACTION = 0.3   # not exported
TOOTH_MATERIAL: str                # "steel_s7_tool"
```

```requires
cls, level and archetype are enum members or their string values; a bad
    string raises ValueError naming the field and its allowed values
override values of None are ignored (= keep the preset); any other key not in
    OVERRIDE_KEYS raises ValueError "unknown impactor setting ..."
at most one of rpm, tip_speed_ms, energy_j, else ValueError "give one of rpm ..."
numeric overrides positive; weapon geometry overrides as weapon_size requires
spec_from_setup: block has weight_class; energy_level defaults to "typical",
    archetype to "vertical_drum"
```

```ensures
weapon geometry and m_eff come from weapon_size(cls, archetype, overrides);
    r_arc = OD / 2
spin: tip_speed_ms given -> v; energy_j given -> v = sqrt(2 E / m_eff);
    else rpm (given, or RPM_PRESETS[(class, weapon shape, level)] -- keyed by
    the resolved shape, so a horizontal spinner overridden to a disc gets disc
    RPMs) -> v = tip_speed_ms(rpm, R);
    then rpm and ke_j are recomputed from v, so all three agree
every RPM preset resolves under TIP_SPEED_WARN_MS with no warnings
v > TIP_SPEED_WARN_MS -> spec.warnings carries a "... above 300 mph, faster
    than real weapons run" line (the agent's instructions make it confirm
    with the user); the spec is still returned
tooth defaults: width = depth = plate thickness; length = bar width, or
    DISC_TOOTH_LENGTH_FRACTION x a disc's OD
opponent_mass_kg defaults to weapon.OPPONENT_MASS_KG[class]; a weapon
    weighing as much or more raises ValueError "... cannot outweigh ..."
spec.overrides = the sorted non-null override keys given
the spec is keyed to the NOMINAL weight class -- a walker's opponent is still
    a normal bot of that class (no bonus argument)
weapon_card: every resolved number rounded for people -- weight_class,
    energy_level, archetype, shape, weapon_mass_kg, plate_thickness_mm,
    weapon_od_mm, bar_width_mm, rpm, tip_speed_ms, tip_speed_mph, energy_j,
    effective_mass_kg, tooth_mm [w, d, l], opponent_mass_kg, custom (=
    spec.overrides), warnings
```

```test
every_preset_is_under_300_mph_and_self_consistent: (every RPM_PRESETS key) => v < warn, no warnings, rpm == preset, v == rpm·2π/60·R, KE == ½ m_eff v²
high_spins_faster_than_typical: (every class, archetype) => KE high > KE typical
one_spin_input_sets_the_rest: ("12lb", typical, horizontal, {rpm|tip_speed_ms|energy_j}) => that value kept, KE == ½ m_eff v², overrides == (key,)
two_spin_inputs_are_refused: ({rpm, energy_j}) => ValueError "one of rpm"
over_300_mph_warns_but_is_allowed: ({tip_speed_ms: 150}) => v 150, warning mentions 300 mph
tooth_defaults_to_the_end_of_the_weapon_and_can_be_overridden: ("12lb") => 12 x 12 x bar width; disc length 0.3 OD; overrides kept
opponent_mass_defaults_to_the_class_and_must_exceed_the_weapon: ("12lb") => 5.443; {weapon_mass_kg: 6} => ValueError "outweigh"
null_overrides_are_ignored_and_unknown_keys_refused: ({rpm: None}) => overrides (); ({rmp: 5}) => ValueError
energy_level_enum_still_validates: ("extreme") => ValueError "energy level"
the_rpm_preset_follows_the_shape_not_the_archetype: (12 lb horizontal + disc / vertical + bar) => the disc / bar preset RPM
case_cli_weapon_prints_the_card_or_an_error: (case_cli.py weapon, rpm 7000 / rpm -1) => card with custom ["rpm"] / exit 1 with error
```

## Implementation

**Energy, size and tip speed cannot all be inputs.** v1 took the survey's
energy and tip speed and derived `m_eff`, leaving the diameter a separate
table; the drawn weapon then had nothing to do with its inertia, and an
earlier v2 kept the energy and derived the speed, which went unbelievably
fast. Now mass and shape give `m_eff` and the diameter together, the level
picks an RPM, and the user can pin exactly one of RPM, tip speed or energy —
the other two follow, since the weapon's mass ties them together.

**`m_eff` is the rim-effective mass of the whole weapon, not the tooth's mass**
(`I / R²`: about M/3 for the bar, M/2 for the disc). In v2 the tooth is a rigid
head and `m_eff·R²` (less the head's own share) is the spin inertia on the
weapon's hub (`loadcase.deck_spec`), whose translational mass is
`opponent_mass_kg`.

**The tooth head is the end of the weapon** (Lily, 2026-10-08): it defaults
to the plate's thickness across and deep, and to the bar's width (or 0.3 of a
disc's OD) along the travel. `tooth.py` reads these three numbers off the
spec; it no longer sizes the head itself.

**Archetype sets the default shape, and the spin axis** (`weapon.swing`: a
horizontal spinner about the robot's up axis, a drum about a horizontal axis
across the attack). An overridden `weapon_shape` changes the shape only.

**300 mph is a warning, not a limit.** A user may genuinely want to model
something extreme; the warning travels on `spec.warnings` into
`case_cli.py build`'s warnings and the weapon card, and the system prompt
tells the agent to confirm before running.

**`weapon_card` is the one human-facing view.** The agent reports it and the
web viewer's Weapon card renders it; its keys are the contract with
`web/src/weapon_card.ts`.

## Tests

`tests/test_impactor.py` (presets, spin inputs, warnings, tooth and opponent
defaults, the `case_cli.py weapon` subcommand); `tests/test_weapon_size.py`
(geometry and its overrides); `tests/test_tooth.py::test_effective_mass_is_a_third_of_a_weapon`
checks m_eff against the quarter-weight bar; `test_tooth`, `test_loadcase` and
`test_simgeom` exercise `impactor_spec` as a fixture.
