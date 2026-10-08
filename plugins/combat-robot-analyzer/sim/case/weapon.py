"""The opponent's weapon as a spinning rigid body: where its axis is, how fast
it turns, and how long the hit lasts.

v1 fired a deformable tooth along the struck surface's normal for a fixed
40 µs. A real spinner's tooth travels along its arc, so it meets armour at an
angle and can glance off, and it keeps driving until its energy is spent. Here
the tooth is rigid, belongs to a body spinning about an axis placed so the arc
passes through the strike point, and carries the weapon's energy as rotation.

All lengths mm, speeds as stated, everything else SI unless named otherwise.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

Vec = tuple[float, float, float]

#: Opponent robot mass by weight class (the class limit). The weapon's hub
#: carries it: the axle is in a robot, so after the hit the weapon recoils
#: with the whole opponent rather than flying off on its own.
OPPONENT_MASS_KG = {"1lb": 0.454, "3lb": 1.361, "12lb": 5.443, "30lb": 13.608}

#: Contact time bounds. 40 µs is v1's floor (the scope it was validated at);
#: 500 µs is what a graded mesh keeps inside the solve budget on mint.
END_TIME_FLOOR_S = 4.0e-5
END_TIME_CAP_S = 5.0e-4

#: The stopping estimate ignores that a free robot recoils and that eroding
#: material resists less, so the hit outlasts it: on inertial-v6 (12 lb into
#: 4130) the target was still absorbing energy at the estimated 286 µs.
HIT_LENGTH_FACTOR = 2.0

#: Stand-in resistance for materials with no yield (hyperelastic, elastic).
ELASTOMER_FLOW_MPA = 30.0


def _norm(v: Sequence[float]) -> Vec:
    n = math.sqrt(sum(c * c for c in v))
    if n < 1e-12:
        raise ValueError("zero-length vector")
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def infer_up(lo: Sequence[float], hi: Sequence[float]) -> Vec:
    """The robot's up axis: its thinnest overall dimension (combat robots are
    flat). The agent states it and the user confirms or overrides it."""
    span = [hi[i] - lo[i] for i in range(3)]
    axis = min(range(3), key=lambda i: span[i])
    return tuple(1.0 if i == axis else 0.0 for i in range(3))  # type: ignore[return-value]


def parse_up(value: str | None, lo: Sequence[float], hi: Sequence[float]) -> Vec:
    """`auto`/None, or an axis name such as `z`, `-y`."""
    if not value or value == "auto":
        return infer_up(lo, hi)
    sign = -1.0 if value.startswith("-") else 1.0
    axis = "xyz".index(value.lstrip("+-").lower())
    return tuple(sign if i == axis else 0.0 for i in range(3))  # type: ignore[return-value]


@dataclass(frozen=True)
class Swing:
    hub: Vec  #: point on the spin axis, at the weapon's centre
    axis: Vec  #: unit spin axis; rotation is right-handed about it at `omega`
    omega: float  #: rad/s, positive
    radius_mm: float
    tip_velocity: Vec  #: unit direction the tooth moves at first contact
    v_tip_ms: float

    def to_dict(self) -> dict[str, object]:
        return {"hub": list(self.hub), "axis": list(self.axis), "omega": self.omega,
                "radius_mm": self.radius_mm, "tip_velocity": list(self.tip_velocity),
                "v_tip_ms": self.v_tip_ms}


def swing(
    strike: Sequence[float],
    normal_out: Sequence[float],
    up: Sequence[float],
    archetype: str,
    radius_mm: float,
    v_tip_ms: float,
    robot_centre: Sequence[float],
) -> Swing:
    """Place the weapon so its tooth's arc passes through `strike`.

    The spin axis is the robot's up axis for a horizontal spinner, and
    horizontal across the line of attack for a drum or vertical spinner. At
    contact the tooth moves along the inward surface normal projected onto the
    weapon's plane of rotation: a wall facing the weapon is met head-on and the
    tooth then sweeps along its arc; a sloped wedge is met obliquely and can
    turn the tooth aside. Of the two hub positions that give that velocity,
    the one outside the struck robot (farther from its centre) is used — the
    weapon belongs to the opponent.
    """
    up = _norm(up)
    n = _norm(normal_out)
    inward = (-n[0], -n[1], -n[2])
    if archetype == "horizontal":
        axis = up
    else:
        heading = _sub(inward, tuple(_dot(inward, up) * u for u in up))
        if math.sqrt(_dot(heading, heading)) < 1e-6:
            raise ValueError("a vertical weapon cannot reach a face that points straight up or down")
        axis = _norm(_cross(_norm(heading), up))
    t = _sub(inward, tuple(_dot(inward, axis) * a for a in axis))
    if math.sqrt(_dot(t, t)) < 1e-6:
        raise ValueError(
            "this face is parallel to the weapon's plane of rotation (e.g. the top of the "
            "robot for a horizontal spinner); its tooth cannot strike it")
    t = _norm(t)
    best = None
    for a in (axis, (-axis[0], -axis[1], -axis[2])):
        # v(P) = omega * a x (P - H) = omega * R * t  when  P - H = R * (t x a)
        offset = _cross(t, a)
        hub = (strike[0] - radius_mm * offset[0], strike[1] - radius_mm * offset[1],
               strike[2] - radius_mm * offset[2])
        d = _sub(hub, robot_centre)
        score = _dot(d, d)
        if best is None or score > best[0]:
            best = (score, hub, a)
    _, hub, a = best
    omega = v_tip_ms * 1000.0 / radius_mm
    return Swing(hub=hub, axis=a, omega=omega, radius_mm=radius_mm, tip_velocity=t, v_tip_ms=v_tip_ms)


def weapon_inertia(axis: Sequence[float], i_axis: float) -> list[float]:
    """Inertia tensor about the hub, global axes, as [Jxx, Jyy, Jzz, Jxy, Jyz, Jxz].

    `i_axis` about the spin axis (`m_eff * R**2`, which is what the preset's
    energy defines); half that about any perpendicular axis, as for a bar.
    """
    a = _norm(axis)
    perp = 0.5 * i_axis
    J = [[perp * (1.0 if i == j else 0.0) + (i_axis - perp) * a[i] * a[j] for j in range(3)]
         for i in range(3)]
    return [J[0][0], J[1][1], J[2][2], J[0][1], J[1][2], J[0][2]]


def estimate_end_time(
    ke_j: float, v_tip_ms: float, flow_stress_mpa: float, contact_area_mm2: float
) -> float:
    """How long the hit lasts, in seconds, clamped to [floor, cap].

    A crude stopping estimate: the target resists with about flow stress x
    tooth face area, so the tooth stops in `d = KE / F` while decelerating
    roughly uniformly, which takes `2 d / v`. A glancing hit ends sooner; a
    free robot being knocked away also ends it sooner. The post-solve energy
    check reports whether the run actually caught the end of the hit.
    """
    if flow_stress_mpa <= 0 or contact_area_mm2 <= 0 or v_tip_ms <= 0:
        return END_TIME_CAP_S
    force_n = flow_stress_mpa * contact_area_mm2
    stop_mm = ke_j * 1000.0 / force_n
    t = HIT_LENGTH_FACTOR * 2.0 * stop_mm / (v_tip_ms * 1000.0)
    return min(END_TIME_CAP_S, max(END_TIME_FLOOR_S, t))


def flow_stress(card: Mapping[str, object]) -> float:
    """A representative flow stress (MPa) for the duration estimate: the yield
    where the card has one; elastomers and elastic cards have none, so they get
    ELASTOMER_FLOW_MPA (TPU-like), which in practice sends soft targets to the cap."""
    if card.get("yield"):
        return float(card["yield"])  # type: ignore[arg-type]
    return ELASTOMER_FLOW_MPA
