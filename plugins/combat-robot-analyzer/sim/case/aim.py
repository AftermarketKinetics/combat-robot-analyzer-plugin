"""Is the strike actually on the target?

The defect this exists to stop: across the corpus, 46 of 58 automatically
placed strikes landed more than 1 mm off any material, in the worst cases 170,
129 and 120 mm clear of it. The tooth travels 3.9 to 4.8 mm in the whole
simulation, so those runs ended with it still in flight — full duration,
``NORMAL TERMINATION``, no contact, no strain energy, and clean throughput
numbers. Thirty-eight of forty-four solved cases tested nothing.

Nothing that reasons about bounding boxes can catch that, which is why it went
unnoticed: Tier 0 has no surfaces. Now it does, so the question can be asked
here, before the card is charged, rather than inferred afterwards from an
energy history nobody was reading.

**Pure standard library, deliberately.** Same reason as :mod:`cra.mesh_metrics`
-- numpy's manylinux wheel will not load on the NixOS system python, so a gate
that depends on it cannot be unit-tested by ``make test``. This one decides
whether a submission is accepted, and that has to be testable with no wheels
at all. A part tessellates to a few thousand triangles and the tests below run
in milliseconds, so there is nothing to buy.

The browser runs the same tests interactively, against the same triangles, and
its answer is feedback. This one is the gate.
"""

from __future__ import annotations

import base64
import math
import struct
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "AIM_TOLERANCE_MM",
    "AimVerdict",
    "Triangle",
    "first_blocker",
    "first_hit",
    "grade_aim",
    "is_inside",
    "load_surface",
    "travel_mm",
]

#: How far the viewer's surface and the solver's surface may legitimately
#: disagree about the same CAD face, in mm.
#:
#: Measured, not chosen — ``cra.calibrate.aim_tolerance`` over ten load cases
#: across five models, 1 lb to 30 lb. Worst single point 0.0791 mm, and the
#: maximum barely exceeded the p99 in every case, so it is a bound rather than
#: the largest thing a sample happened to hold. This is three times that.
#:
#: The mechanism is the solver's end, not the viewer's: as a fraction of the
#: viewer's chord tolerance the disagreement *falls* with model size, so it is
#: not chord-driven. A 1.5 mm element chording a cylinder of radius ``R`` cuts
#: inside by ``h**2 / 8R``; 0.25 mm corresponds to ``R`` of 1.1 mm, and a
#: curved feature smaller than that is already reported by the timestep driver
#: pass. So a gap above this is not two tessellations disagreeing.
AIM_TOLERANCE_MM = 0.25

#: Three directions for the inside/outside parity test. A single ray that
#: grazes an edge or a shared vertex is counted once or twice depending on
#: floating-point luck, and either way the parity flips and the answer is
#: exactly wrong. Three awkward directions and a majority vote turns a wrong
#: answer into a disagreement, which cannot happen for all three at once on a
#: closed surface.
_PARITY_DIRECTIONS = (
    (0.5773502691896258, 0.5773502691896258, 0.5773502691896258),
    (-0.7302967433402214, 0.5477225575051661, 0.4082482904638631),
    (0.3015113445777636, -0.9045340337332909, 0.30151134457776363),
)

#: Möller-Trumbore rejects a ray parallel to a triangle's plane on this. In mm
#: units on parts tens to hundreds of mm across, 1e-12 is far below any real
#: geometry and far above double-precision noise.
_EPS = 1e-12

Triangle = tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]


@dataclass(frozen=True)
class AimVerdict:
    """What the geometry says about one strike point.

    ``status`` is the decision; ``gap_mm`` is how far the aim sits from the
    surface along the line of travel, and is ``None`` when the line never
    meets the part, because there is no distance to report rather than a
    distance of zero.
    """

    status: str
    gap_mm: float | None
    travel_mm: float
    message: str
    #: The part the tooth meets first, when ``status`` is ``occluded``.
    blocker: str | None = None

    #: Statuses that must stop a submission. Everything else is a warning at
    #: worst, because the tooth does still arrive.
    BLOCKING = ("inside", "no_intersection", "beyond_travel", "occluded")

    @property
    def blocks(self) -> bool:
        return self.status in self.BLOCKING

    @property
    def warns(self) -> bool:
        return self.status == "marginal"


def travel_mm(v_tip_ms: float, end_time_s: float) -> float:
    """How far the tooth gets in the whole simulation, in mm.

    The number every gap is judged against. Judging against zero would call a
    4.79 mm gap "nearly on the surface" when it is a clean miss: the tooth
    only travels 4.68 mm.
    """
    return float(v_tip_ms) * 1000.0 * float(end_time_s)


# ── reading the surface ───────────────────────────────────────────────


def load_surface(doc: Mapping[str, Any], part_id: str) -> list[Triangle]:
    """The triangles of one part, dequantised out of a geometry document.

    ``verts`` is three contiguous uint16 coordinate blocks over the *model's*
    bounding box, not the part's, because every part in a document shares one
    frame.
    """
    rec = None
    for p in doc.get("parts") or ():
        if str(p.get("id")) == part_id:
            rec = p
            break
    if rec is None:
        raise KeyError(part_id)

    lo = [float(v) for v in doc["lo"]]
    span = [float(v) for v in doc["span"]]
    nv = int(rec["nv"])
    if nv == 0:
        return []

    raw = base64.b64decode(rec["verts"])
    if len(raw) != nv * 6:
        raise ValueError(f"{part_id}: {len(raw)} vertex bytes for {nv} vertices")
    q = struct.unpack(f"<{nv * 3}H", raw)
    scale = [(span[k] if span[k] > 0 else 1.0) / 65535.0 for k in range(3)]
    verts = [
        (lo[0] + q[i] * scale[0], lo[1] + q[nv + i] * scale[1], lo[2] + q[2 * nv + i] * scale[2])
        for i in range(nv)
    ]

    packed = base64.b64decode(rec["tris"])
    idx = struct.unpack(f"<{len(packed) // 4}I", packed)
    return [(verts[idx[n]], verts[idx[n + 1]], verts[idx[n + 2]]) for n in range(0, len(idx), 3)]


# ── ray casting ───────────────────────────────────────────────────────


def _ray_triangle(
    origin: Sequence[float], direction: Sequence[float], tri: Triangle
) -> float | None:
    """Möller-Trumbore. Distance along ``direction``, or ``None`` for a miss.

    Back faces are not culled: the surface has to be hit from inside as well
    as outside for the parity test, and a part whose triangle winding is
    inconsistent is common enough in exported CAD that relying on it would be
    a bug waiting for one bad model.
    """
    (ax, ay, az), (bx, by, bz), (cx, cy, cz) = tri
    e1 = (bx - ax, by - ay, bz - az)
    e2 = (cx - ax, cy - ay, cz - az)
    px = direction[1] * e2[2] - direction[2] * e2[1]
    py = direction[2] * e2[0] - direction[0] * e2[2]
    pz = direction[0] * e2[1] - direction[1] * e2[0]
    det = e1[0] * px + e1[1] * py + e1[2] * pz
    if -_EPS < det < _EPS:
        return None
    inv = 1.0 / det
    tx, ty, tz = origin[0] - ax, origin[1] - ay, origin[2] - az
    u = (tx * px + ty * py + tz * pz) * inv
    if u < 0.0 or u > 1.0:
        return None
    qx = ty * e1[2] - tz * e1[1]
    qy = tz * e1[0] - tx * e1[2]
    qz = tx * e1[1] - ty * e1[0]
    v = (direction[0] * qx + direction[1] * qy + direction[2] * qz) * inv
    if v < 0.0 or u + v > 1.0:
        return None
    t = (e2[0] * qx + e2[1] * qy + e2[2] * qz) * inv
    return t if t > _EPS else None


def first_hit(
    origin: Sequence[float], direction: Sequence[float], tris: Iterable[Triangle]
) -> float | None:
    """Distance to the nearest triangle along ``direction``, or ``None``."""
    best: float | None = None
    for tri in tris:
        t = _ray_triangle(origin, direction, tri)
        if t is not None and (best is None or t < best):
            best = t
    return best


def _ray_box(
    origin: Sequence[float], direction: Sequence[float], lo: Sequence[float], hi: Sequence[float]
) -> float | None:
    """Slab test. Distance at which the ray enters the box, or ``None``.

    A pre-filter, not an answer. Casting every part's triangles would mean
    hundreds of thousands of triangle tests per strike; almost every part's
    box is nowhere near the line of travel, and rejecting it costs six
    comparisons.
    """
    near, far = -math.inf, math.inf
    for k in range(3):
        d = direction[k]
        if abs(d) < _EPS:
            if origin[k] < lo[k] or origin[k] > hi[k]:
                return None
            continue
        t1 = (lo[k] - origin[k]) / d
        t2 = (hi[k] - origin[k]) / d
        if t1 > t2:
            t1, t2 = t2, t1
        near = max(near, t1)
        far = min(far, t2)
        if near > far:
            return None
    return near if far >= 0 else None


def first_blocker(
    origin: Sequence[float],
    direction: Sequence[float],
    doc: Mapping[str, Any],
    *,
    target_id: str,
    target_distance: float,
    margin: float = AIM_TOLERANCE_MM,
) -> tuple[str, float] | None:
    """The part the tooth meets before it reaches the target, if any.

    A strike can sit exactly on the right surface and still be impossible,
    because something else is in front of it. That is not a question about the
    target's own geometry, so grading the target alone cannot see it — and the
    scoped model would happily replace the obstruction with a stand-in box and
    report on an impact that hit the box.

    ``margin`` keeps a neighbour that is merely flush with the target from
    counting as an obstruction: two surfaces that meet are not one blocking
    the other, and they agree only to within the tessellation tolerance
    anyway.
    """
    best: tuple[str, float] | None = None
    for rec in doc.get("parts") or ():
        part_id = str(rec.get("id"))
        if part_id == target_id:
            continue
        box = rec.get("bbox")
        if isinstance(box, list | tuple) and len(box) == 6:
            entry = _ray_box(origin, direction, box[:3], box[3:])
            # Missed entirely, or first reachable at a point the tooth has
            # already stopped at — either way it is not in the way.
            if entry is None or entry > target_distance:
                continue
        try:
            tris = load_surface(doc, part_id)
        except (KeyError, ValueError):
            continue
        hit = first_hit(origin, direction, tris)
        if hit is None or hit >= target_distance - margin:
            continue
        if best is None or hit < best[1]:
            best = (part_id, hit)
    return best


def is_inside(point: Sequence[float], tris: Sequence[Triangle]) -> bool:
    """Is the point within the closed surface? Majority of three parity rays."""
    votes = 0
    for d in _PARITY_DIRECTIONS:
        crossings = 0
        for tri in tris:
            if _ray_triangle(point, d, tri) is not None:
                crossings += 1
        votes += crossings % 2
    return votes >= 2


# ── the grade ─────────────────────────────────────────────────────────


def grade_aim(
    point: Sequence[float],
    direction: Sequence[float],
    tris: Sequence[Triangle],
    *,
    travel: float,
    target_id: str,
    tolerance: float = AIM_TOLERANCE_MM,
    doc: Mapping[str, Any] | None = None,
) -> AimVerdict:
    """Judge one strike point against the target's real surface.

    ``direction`` runs from the target *outwards*, towards where the tooth
    starts, so the tooth travels along ``-direction``. The gap is measured by
    casting back down the line of travel from well clear of the part and
    taking the first surface it meets — the same thing the tooth will do.

    Pass ``doc`` to also check that nothing else is in the way. Without it the
    grade answers "is this point on the target", which is a strictly weaker
    question than "will the tooth arrive here": a strike can sit exactly on
    the right surface with another part in front of it, and the scoped model
    would replace that part with a stand-in box and report on hitting the box.
    """
    if not tris:
        return AimVerdict(
            "no_geometry",
            None,
            travel,
            f"{target_id} has no surface geometry, so the strike on it could not be checked.",
        )

    # The ray comes first and the inside/outside test second, which is not
    # the obvious order. A parity test is ill-defined for a point lying
    # exactly on the surface -- the ray leaves without crossing anything in
    # one direction and crosses once in the other -- and *on the surface* is
    # where every correct aim sits. Judging the gap first means the parity
    # test is only ever asked about points that are demonstrably not on the
    # surface, where it is well defined. It is also cheaper: three parity
    # rays are skipped for every aim that is already right.
    # Start far enough out that the origin is certainly clear of the part, then
    # travel inwards. Three times the part's own extent is unconditionally
    # outside it.
    reach = _extent(tris) * 3.0 + 1.0
    inward = (-direction[0], -direction[1], -direction[2])
    origin = (
        point[0] + direction[0] * reach,
        point[1] + direction[1] * reach,
        point[2] + direction[2] * reach,
    )
    hit = first_hit(origin, inward, tris)
    if hit is None:
        return AimVerdict(
            "no_intersection",
            None,
            travel,
            f"The line of travel never meets {target_id} — an opponent "
            f"approaching from that direction would pass the part entirely. "
            f"Aim at a surface, or come in from another side.",
        )

    gap = abs(reach - hit)

    # Only worth asking once the strike is on the part it claims to be on. A
    # strike that is already 90 mm adrift has a more basic problem than what
    # happens to be in front of it, and saying so is more use than naming a
    # bystander.
    if doc is not None and gap <= travel:
        blocker = first_blocker(origin, inward, doc, target_id=target_id, target_distance=hit)
        if blocker is not None:
            who, at = blocker
            return AimVerdict(
                "occluded",
                gap,
                travel,
                f"{who} is in the way: the tooth reaches it {abs(hit - at):.1f} mm "
                f"before it would reach {target_id}, so this run would measure "
                f"an impact on {who} instead. Strike from a direction that is "
                f"not shadowed, or aim at {who} if that is the exchange you "
                f"want.",
                blocker=who,
            )

    if gap <= tolerance:
        return AimVerdict(
            "on_surface",
            gap,
            travel,
            f"The strike is on the surface of {target_id}, {gap:.3f} mm off.",
        )

    # Off the surface by more than two tessellations can disagree, so which
    # side it is on is now both a meaningful question and an answerable one.
    if is_inside(point, tris):
        return AimVerdict(
            "inside",
            gap,
            travel,
            f"The strike on {target_id} is inside the part rather than on "
            f"it, so the tooth would begin already embedded in the "
            f"material. Place it on a surface you can see.",
        )

    if gap > travel:
        return AimVerdict(
            "beyond_travel",
            gap,
            travel,
            f"The strike sits {gap:.1f} mm clear of {target_id}, and the tooth "
            f"only travels {travel:.1f} mm before the simulation ends — it "
            f"would never arrive, and the report would describe an impact that "
            f"did not happen.",
        )
    return AimVerdict(
        "marginal",
        gap,
        travel,
        f"The strike sits {gap:.2f} mm off the surface of {target_id}, "
        f"against {travel:.1f} mm of travel. The tooth still arrives, but it "
        f"spends part of the simulation getting there.",
    )


def _extent(tris: Sequence[Triangle]) -> float:
    """The diagonal of the triangles' bounding box."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for tri in tris:
        for p in tri:
            for k in range(3):
                if p[k] < lo[k]:
                    lo[k] = p[k]
                if p[k] > hi[k]:
                    hi[k] = p[k]
    return math.dist(lo, hi)
