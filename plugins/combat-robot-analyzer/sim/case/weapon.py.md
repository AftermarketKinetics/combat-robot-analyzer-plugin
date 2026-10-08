---
name: weapon
---

# The opponent's weapon as a spinning rigid body

Where the opponent weapon's spin axis is, how fast it turns, what inertia it
carries, and how long the hit lasts. New in v2 (2026-10-08); it replaces v1's
model, which fired a deformable S7 tooth along the struck surface's normal at
a clamped target for a fixed 40 µs.

A real spinner's tooth travels along its arc, so it meets armour at an angle,
can glance off, and keeps driving until its energy is spent. Here the tooth is
rigid, belongs to a body spinning about an axis placed so the arc passes
through the strike point, and carries the spec's energy (`ImpactorSpec.ke_j`) as rotation.
`loadcase.build_case` calls `parse_up`, `swing` and (when `end_time` is None)
`estimate_end_time`/`flow_stress`; `loadcase.deck_spec` uses
`impactor.opponent_mass_kg` and `weapon_inertia` to fill the weapon's `/RBODY` and
`/INIVEL/AXIS`; `OPPONENT_MASS_KG` is read by `impactor.impactor_spec` (the
default `opponent_mass_kg`) and `weapon_size` (the quarter-weight weapon). `case_cli.py` imports `END_TIME_CAP_S` and reports
`Swing.to_dict()` as `weapon`.

All lengths mm; speeds as named (`v_tip_ms` m/s, `omega` rad/s); times s.

```py-sig
Vec = tuple[float, float, float]

OPPONENT_MASS_KG: dict[str, float]   # {"1lb": 0.454, "3lb": 1.361, "12lb": 5.443, "30lb": 13.608}
END_TIME_FLOOR_S = 4.0e-5
END_TIME_CAP_S = 5.0e-4
HIT_LENGTH_FACTOR = 2.0
ELASTOMER_FLOW_MPA = 30.0

def infer_up(lo: Sequence[float], hi: Sequence[float]) -> Vec
def parse_up(value: str | None, lo: Sequence[float], hi: Sequence[float]) -> Vec

@dataclass(frozen=True)
class Swing:
    hub: Vec              # point on the spin axis, at the weapon's centre
    axis: Vec             # unit spin axis; right-handed rotation at omega
    omega: float          # rad/s, positive
    radius_mm: float
    tip_velocity: Vec     # unit direction the tooth moves at first contact
    v_tip_ms: float
    def to_dict(self) -> dict[str, object]

def swing(strike, normal_out, up, archetype: str, radius_mm: float,
          v_tip_ms: float, robot_centre) -> Swing
def weapon_inertia(axis: Sequence[float], i_axis: float) -> list[float]
def estimate_end_time(ke_j: float, v_tip_ms: float, flow_stress_mpa: float,
                      contact_area_mm2: float) -> float
def flow_stress(card: Mapping[str, object]) -> float
```

```requires
infer_up: lo/hi are the assembly's bounding box corners
parse_up: value is None, "auto", or an axis name x|y|z with optional +/- sign
    ("-y"); anything else raises ValueError (str.index)
swing: normal_out points out of the struck surface (towards the opponent);
    up and normal_out are non-zero; archetype "horizontal" spins about up,
    any other value is treated as a vertical weapon (drum)
swing: a vertical weapon needs a face that is not straight up/down
    -> ValueError("a vertical weapon cannot reach a face that points straight up or down")
swing: the inward normal must not lie along the spin axis (e.g. the top of the
    robot for a horizontal spinner) -> ValueError("this face is parallel to the
    weapon's plane of rotation ...")
weapon_inertia: axis is non-zero
```

```ensures
infer_up: the unit vector along the box's thinnest dimension (+ sign)
swing: |strike - hub| == radius_mm, and omega * axis x (strike - hub)
    == v_tip_ms * 1000 * tip_velocity (mm/s): the arc passes through the
    strike point moving along tip_velocity at v_tip
swing: tip_velocity = inward normal projected onto the plane of rotation,
    normalised -- a facing wall is met head-on, an oblique face obliquely
swing: of the two axis signs (two hub positions) the hub farther from
    robot_centre is chosen -- the weapon belongs to the opponent
swing: omega = v_tip_ms * 1000 / radius_mm
weapon_inertia: [Jxx, Jyy, Jzz, Jxy, Jyz, Jxz] in global axes; i_axis about
    the spin axis, i_axis / 2 about any perpendicular axis (a bar)
estimate_end_time: in [END_TIME_FLOOR_S, END_TIME_CAP_S]; returns the cap when
    any of flow stress, area or v_tip is not positive
flow_stress: the card's `yield` (MPa) when truthy, else ELASTOMER_FLOW_MPA
```

```test up_is_the_thinnest_dimension
infer_up([0,0,0], [400,420,73]) => (0, 0, 1)
infer_up([0,0,0], [400,60,300]) => (0, 1, 0)
parse_up("-y", [0,0,0], [1,1,1]) => (0, -1, 0)
```

```test horizontal_spinner_hits_a_vertical_wall_head_on_and_the_arc_passes_through_it
with wall facing -x at x=0, robot centre (200, 0, 10), up z
swing((0,0,10), (-1,0,0), (0,0,1), "horizontal", 150, 100, (200,0,10))
    => tip_velocity (1,0,0); |axis| along z; |strike - hub| == 150;
       hub farther from the robot centre than the strike;
       omega * axis x (strike - hub) == (100_000, 0, 0) mm/s
```

```test a_sloped_wedge_is_struck_obliquely
swing((0,0,0), (-sqrt(.5), 0, sqrt(.5)), (0,0,1), "horizontal", 100, 100, (200,0,0))
    => tip_velocity (1,0,0); tip_velocity . normal == -sqrt(.5)  (45 degrees)
```

```test a_drum_spins_about_a_horizontal_axis_across_the_attack
swing((0,0,0), (-1,0,0), (0,0,1), "vertical_drum", 60, 100, (200,0,0))
    => axis has z == 0 and |y| == 1
```

```test a_face_in_the_plane_of_rotation_cannot_be_struck
swing((0,0,0), (0,0,1), (0,0,1), "horizontal", 100, 100, (0,0,-50)) => ValueError "parallel"
```

```test inertia_has_the_spin_value_about_the_axis
weapon_inertia((0,0,1), 10.0) => Jzz 10.0, Jxx 5.0, Jxy 0.0
```

```test end_time_is_clamped_and_grows_with_energy
estimate_end_time(10, 100, 1000, 100) => END_TIME_FLOOR_S
estimate_end_time(2500, 98.3, 435, 118) => END_TIME_CAP_S
END_TIME_FLOOR_S < estimate_end_time(60, 120, 435, 60) < END_TIME_CAP_S
```

All seven run in `sim/tests/test_weapon.py`.

## Implementation

**The hub position is solved, not searched.** A point P on a body spinning at
`omega` about axis `a` through H moves at `omega * a x (P - H)`. Setting
`P - H = R * (t x a)` gives exactly `omega * R * t`, so the hub is
`strike - R * (t x a)` for each sign of `a`; the farther one from the robot's
centre is kept. `build_case` passes the centre of the whole assembly's
bounding box as `robot_centre`.

**Axis per archetype.** A horizontal spinner spins about the robot's up axis.
A drum (any non-`"horizontal"` archetype) spins about `heading x up`, where
`heading` is the inward normal with its up component removed — horizontal and
across the line of attack. The tooth's velocity at contact is the inward
normal projected on the plane of rotation, so the tooth meets a facing wall
head-on and a sloped wedge obliquely (it can glance off in the solve).

**Up is guessed, then confirmed.** `infer_up` takes the thinnest overall
dimension because combat robots are flat; the service's prompt tells the agent
to state it and have the user confirm, since a wrong up axis aims a horizontal
spinner wrongly. `parse_up` accepts an explicit `x`/`-y`/... override.

**The hub carries the opponent's whole mass.** `ImpactorSpec.opponent_mass_kg`
defaults to `OPPONENT_MASS_KG`, the weight-class limit, and the user may
override it (2026-10-08): the axle is in a robot, so after the hit the weapon
recoils with the whole opponent rather than flying off alone.
`weapon_inertia` is given `i_axis` by `deck_spec` as
`(m_eff - head_mass) * R**2` — the weapon's spin inertia minus what the meshed
rigid head already contributes at the arc radius — so the total spin energy is
the spec's. The half-value about perpendicular axes is a bar's, an
approximation; only the spin component is set by the preset.

**Duration estimate.** The target is taken to resist with about flow stress x
tooth face area (`build_case` passes `head_width * head_thickness`), so the
tooth stops in `d = KE / F`, decelerating roughly uniformly, in `2 d / v`.
That is multiplied by `HIT_LENGTH_FACTOR` (2) because the estimate ignores the
free robot's recoil and eroding material's falling resistance: on inertial-v6
(12 lb horizontal into a 4130 shell) the target was still absorbing energy at
the estimated 286 µs (the code comment's measurement). Clamped to 40 µs (v1's validated floor) and
500 µs (what a graded mesh keeps inside the solve budget on mint). Materials
without a yield (hyperelastic, elastic) get `ELASTOMER_FLOW_MPA` (TPU-like),
which in practice sends soft targets to the cap. The service's `get_results`
`hit` block reports afterwards whether the run caught the end of the hit.

**What a caller gets wrong.** `parse_up` with a bad axis name raises a bare
`ValueError` from `str.index`, not a descriptive one (`update_setup`'s enum
keeps bad names out). `swing`'s two `ValueError`s are meant for the user;
`build_case` re-raises them as `LoadCaseError` prefixed with the case name.
