"""Where the opponent strikes from.

The user tags parts by role; nothing says which *direction* an opponent comes
in. This picks one deterministically, from the Tier 0 bounding boxes alone, so
a job is reproducible from its inputs and the choice can be printed in the
report for the user to disagree with.

The rule: sweep candidate directions in the plane perpendicular to the bot's
shortest axis, discard any direction with something standing in the way, and
take the one the target reaches furthest along.

**Blocking is a corridor test, not a reach comparison.** A part blocks only if
it reaches past the target's own surface *and* its footprint overlaps the
target's in the plane perpendicular to the strike. Demanding instead that the
target out-reach everything rejects every flush-mounted panel — inertial-v6's
191 x 166 x 1 mm armour plate sits inside the bot's envelope, as armour
normally does. A part genuinely buried in the middle is blocked from every
direction, which is the signal that the tagging was wrong.

**Parts are treated as inscribed ellipsoids, not as their bounding boxes.** A
box's corners project 73% further than its faces, so scoring on corners quietly
prefers diagonal strikes on everything, and a disc — whose bbox corners sit
0.41R outside its own rim — never reads as rotationally symmetric. The support
function of the inscribed ellipsoid, ``sqrt(sum((a_i*n_i)^2))``, is exact for a
sphere, correct for a disc in its own plane, and never rewards a corner that
the real solid does not have.

Pure geometry over ``tier0["parts"][].bbox`` — no gmsh, so the rule is
testable directly. The *direction* is chosen here; the exact strike point and
standoff are closed later against the real solids.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Any

from case.aim import Triangle, first_hit

__all__ = [
    "Approach",
    "PlacementError",
    "approach_candidates",
    "bbox_corners",
    "choose_approach",
    "select_target",
    "snap_to_surface",
]

# Candidate directions swept around the bot's shortest axis. 32 gives 11.25
# degree resolution, which is finer than the geometry justifies and cheap.
AZIMUTH_STEPS = 32

# A direction whose score ties the winner within this fraction is treated as
# equal, so the tie-break is by index and the result is reproducible rather
# than dependent on float noise.
TIE_FRACTION = 1e-6

# A weapon is struck across its span, not along it. Directions within this
# angle of the target's longest axis are rejected for weapon targets.
SPAN_EXCLUSION_DEG = 20.0

# ...but only when the target is actually a bar. The rule exists to stop a
# spinner being loaded along its stiffest, least representative direction,
# which for a bar is its length. A disc has no such direction: every rim
# strike is equivalent, and excluding a cone around the disc's longest
# in-plane axis removes the one approach a drum tooth would really arrive
# from. Derive Mk 1 v41's weapon is a 4.0 x 32.8 x 30.1 mm disc, and the span
# rule made it unstrikeable from anywhere.
#
# A bar is recognised by its cross-section being roughly square, not by
# elongation alone: 000-PBK-Mk2.1's 6.4 x 89.9 x 42.7 mm plate is elongated
# 2.11:1 and would pass an elongation-only test, but its 6.7:1 cross-section
# says plate. Sorting the extents e0 <= e1 <= e2, a bar is
# ``e2/e1 >= SPAN_ASPECT_RATIO and e1/e0 < SPAN_ASPECT_RATIO``.
SPAN_ASPECT_RATIO = 2.0

# How far past the target's surface a part must reach before it counts as
# standing in the way. Neighbours flush with an armour panel — its own frame,
# the bolts holding it on — sit within a millimetre of it by construction and
# obstruct nothing that a blunt tooth cares about.
BLOCK_MARGIN_MM = 1.0

# How much of the target's silhouette has to be hidden before a direction
# counts as obstructed. The refusal this feeds says the part is "enclosed by
# other parts", and that should mean what it says: one neighbour clipping a
# corner is not enclosure. Derive Feather Mk 1 v121's 98 x 90 x 12 weapon disc
# was reported unreachable from every direction under an all-or-nothing test,
# and a weapon disc nothing can reach is not a weapon.
BLOCK_COVERAGE = 0.8


class PlacementError(RuntimeError):
    """No usable approach direction — always a hard gate failure."""


@dataclass(frozen=True)
class Approach:
    """The chosen strike geometry, for the deck and for the report."""

    direction: tuple[float, float, float]
    strike_point: tuple[float, float, float]
    standoff: float
    reach: float
    exposure: float
    arbitrary: bool
    #: Which body the tooth actually meets. ``solid_index`` is ``None`` for a
    #: single-solid part, where the part *is* the body; otherwise it indexes
    #: the part's ``solid_bboxes``. The load case needs it because a boundary
    #: condition placed on a different solid of the same part holds nothing.
    part_id: str | None = None
    solid_index: int | None = None

    def describe(self) -> str:
        dx, dy, dz = self.direction
        text = f"struck along ({dx:+.2f}, {dy:+.2f}, {dz:+.2f}) from {self.standoff:.2f} mm out"
        if self.arbitrary:
            text += "; the target is rotationally symmetric, so the azimuth is arbitrary"
        return text


def snap_to_surface(approach: Approach, tris: Sequence[Triangle]) -> Approach:
    """Move the strike point onto the material, along the chosen direction.

    Everything else here reasons about bounding boxes, because for most of
    this project's life that was all Tier 0 had. It is why the aim point lands
    in empty space: the support point of the ellipsoid *inscribed* in a box is
    strictly inside the box, and the box is already larger than the part, so
    for anything struck obliquely the point ends up in the void between the
    two. Measured over the corpus, that put 43 of 58 automatic strikes
    somewhere the tooth could never reach.

    Tier 0 carries surfaces now, so the fix is a ray cast rather than a
    schema change: keep the direction, which the box reasoning gets right 53
    times in 58, and take the first material the tooth would actually meet.
    That alone moves the prefill from 43 refusals to 5.

    The 5 that remain are approaches whose *line* misses the part, which is a
    direction failure and not something snapping can repair. Those are
    returned unchanged, for the aim gate to refuse and a human to re-aim.
    """
    if not tris:
        return approach
    d = approach.direction
    reach = _snap_reach(tris)
    origin = tuple(approach.strike_point[k] + d[k] * reach for k in range(3))
    hit = first_hit(origin, tuple(-c for c in d), tris)
    if hit is None:
        return approach
    return replace(
        approach,
        strike_point=(
            origin[0] - d[0] * hit,
            origin[1] - d[1] * hit,
            origin[2] - d[2] * hit,
        ),
    )


def _snap_reach(tris: Sequence[Triangle]) -> float:
    """Far enough outside the part to be certainly clear of it."""
    lo = [math.inf] * 3
    hi = [-math.inf] * 3
    for tri in tris:
        for pt in tri:
            for k in range(3):
                lo[k] = min(lo[k], pt[k])
                hi[k] = max(hi[k], pt[k])
    return math.dist(lo, hi) * 3.0 + 1.0


def bbox_corners(bbox: Any) -> list[tuple[float, float, float]]:
    """The eight corners of a Tier 0 bbox, accepting either shape it takes."""
    if isinstance(bbox, dict):
        lo = bbox.get("min") or [bbox.get(k) for k in ("xmin", "ymin", "zmin")]
        hi = bbox.get("max") or [bbox.get(k) for k in ("xmax", "ymax", "zmax")]
    else:
        seq = list(bbox)
        lo, hi = seq[:3], seq[3:6]
    if any(v is None for v in (*lo, *hi)):
        raise PlacementError(f"unusable bounding box: {bbox!r}")
    lo = [float(v) for v in lo]
    hi = [float(v) for v in hi]
    return [(x, y, z) for x in (lo[0], hi[0]) for y in (lo[1], hi[1]) for z in (lo[2], hi[2])]


def _normalise(v: Sequence[float]) -> tuple[float, float, float]:
    n = math.sqrt(sum(c * c for c in v))
    if n <= 0:
        raise PlacementError("cannot normalise a zero-length direction")
    return (v[0] / n, v[1] / n, v[2] / n)


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]


def approach_candidates(extent: Sequence[float]) -> list[tuple[float, float, float]]:
    """Directions to try, given the whole model's bounding-box extent.

    Swept in the plane perpendicular to the shortest axis — a bot is wider
    than it is tall, and opponents arrive horizontally — plus that axis's own
    two directions so a vertical spinner's tip can still win on reach.
    """
    if len(extent) != 3:
        raise PlacementError(f"expected a 3-component extent, got {extent!r}")
    short = min(range(3), key=lambda i: extent[i])
    u, v = [(short + 1) % 3, (short + 2) % 3]

    out: list[tuple[float, float, float]] = []
    for k in range(AZIMUTH_STEPS):
        theta = 2.0 * math.pi * k / AZIMUTH_STEPS
        vec = [0.0, 0.0, 0.0]
        vec[u] = math.cos(theta)
        vec[v] = math.sin(theta)
        out.append((vec[0], vec[1], vec[2]))

    for sign in (1.0, -1.0):
        vec = [0.0, 0.0, 0.0]
        vec[short] = sign
        out.append((vec[0], vec[1], vec[2]))
    return out


def choose_approach(
    parts: Sequence[dict[str, Any]],
    target_ids: Sequence[str],
    *,
    tooth_width: float,
    standoff: float,
    exclude_span: bool = False,
) -> Approach:
    """Pick the direction an opponent strikes the target from.

    ``parts`` is the Tier 0 ``parts`` array. ``exclude_span`` rejects
    directions close to the target's own longest axis, which is what stops a
    bar or drum being struck on its end cap.
    """
    wanted = set(target_ids)
    targets = [p for p in parts if p.get("id") in wanted]
    if not targets:
        raise PlacementError(f"no geometry for the tagged parts {sorted(wanted)}")

    # One body per *solid*, not per part. A mirrored armour pair exported as a
    # single part has a bounding box spanning the whole bot, which is nobody's
    # geometry: it reads as enormously exposed and shadows everything. Each
    # half is a body, evaluated on its own, and its siblings become ordinary
    # obstructions like any other part.
    bodies: list[tuple[tuple[float, float, float], tuple[float, float, float]]] = []
    owners: list[tuple[str, int | None]] = []
    for p in targets:
        # A `None` entry is a body Tier 0 could not box. The position is still
        # the body's name -- `solid_bboxes` is one entry per solid, gaps
        # included -- so the index is carried through rather than reassigned
        # by enumerating what survived.
        usable = [(i, b) for i, b in enumerate(p.get("solid_bboxes") or ()) if b]
        if usable:
            for i, box in usable:
                bodies.append(_ellipsoid(box))
                owners.append((str(p.get("id")), i))
        elif p.get("bbox"):
            bodies.append(_ellipsoid(p["bbox"]))
            owners.append((str(p.get("id")), None))
    if not bodies:
        raise PlacementError(f"no bounding box for the tagged parts {sorted(wanted)}")

    other_ell = [
        _ellipsoid(p["bbox"]) for p in parts if p.get("id") not in wanted and p.get("bbox")
    ]

    extent = _extent(bodies + other_ell)
    cos_limit = math.cos(math.radians(SPAN_EXCLUSION_DEG))
    candidates = approach_candidates(extent)

    scored: list[
        tuple[
            float,
            float,
            tuple[float, float, float],
            tuple[tuple[float, float, float], tuple[float, float, float]],
            tuple[float, float] | None,
            int,
        ]
    ] = []
    swept: list[float] = []
    any_legal = False
    for body_index, body in enumerate(bodies):
        # Per body: its own span, and its own siblings standing in the way.
        excluded = _span_axis([body]) if exclude_span else None
        blockers = other_ell + [b for j, b in enumerate(bodies) if j != body_index]
        for index, direction in enumerate(candidates):
            if excluded is not None and abs(_dot(direction, excluded)) > cos_limit:
                continue
            any_legal = True
            reach = _support(body, direction)
            covered, exposed = _shadow([body], blockers, direction, reach)
            if covered >= BLOCK_COVERAGE or exposed is None:
                continue
            # Rank by the reach that is actually exposed. Plain reach would let
            # a direction that is 79% hidden beat a clear one a millimetre
            # shorter, which is the wrong trade for a tooth that has to arrive
            # there.
            score = reach * (1.0 - covered)
            scored.append((score, reach, direction, body, exposed, body_index))
            # Only the azimuthal sweep speaks to rotational symmetry; the two
            # axial candidates trail it and would swamp the comparison.
            if index < AZIMUTH_STEPS:
                swept.append(score)

    if not any_legal:
        raise PlacementError(
            "every approach direction runs along the target's own span; "
            "the part tagged for this load case cannot be struck across it"
        )
    if not scored:
        raise PlacementError(
            "the tagged part is enclosed by other parts; nothing can strike it from outside the bot"
        )

    peak = max(s[0] for s in scored)
    # First candidate within the tie band, so a symmetric target always picks
    # the same direction instead of following float noise.
    band = abs(peak) * TIE_FRACTION
    best = next(s for s in scored if s[0] >= peak - band)

    spread = (max(swept) - min(swept)) if swept else 0.0
    arbitrary = spread <= abs(peak) * TIE_FRACTION

    # Aim at the point on the target that actually faces the tooth. Using
    # ``direction * reach`` instead lands on the supporting *plane*, which is
    # only the right point when the target straddles the origin -- on
    # inertial-v6's side panel it sat 11.2 mm off a body 9.5 mm thick, so the
    # tooth flew past and the run recorded a clean miss.
    direction = _normalise(best[2])
    strike = _support_point(best[3], direction)
    # Then slide it sideways onto the part of the silhouette nothing is
    # standing in front of. Without this a partly shadowed panel is aimed at
    # its own centre, which may be exactly the bit its frame covers -- the
    # tooth would hit the frame, and the report would describe the panel.
    strike = _slide_to_exposed(strike, direction, best[4])
    part_id, solid_index = owners[best[5]]
    return Approach(
        direction=direction,
        strike_point=strike,
        standoff=standoff,
        reach=best[1],
        exposure=best[0],
        arbitrary=arbitrary,
        part_id=part_id,
        solid_index=solid_index,
    )


def _slide_to_exposed(
    strike: tuple[float, float, float],
    direction: Sequence[float],
    exposed: tuple[float, float] | None,
) -> tuple[float, float, float]:
    """Move ``strike`` sideways onto the exposed centre of the silhouette.

    Depth along ``direction`` is left alone, so the point stays on the
    supporting surface the tooth meets. That is an approximation on a curved
    body -- the true surface recedes as you move off the support point -- but
    every shape here is already the ellipsoid inscribed in a bounding box, and
    a strike aimed through a neighbour is the larger error by far.
    """
    if exposed is None:
        return strike
    u, v = _perpendicular_basis(direction)
    du = exposed[0] - _dot(strike, u)
    dv = exposed[1] - _dot(strike, v)
    return (
        strike[0] + du * u[0] + dv * v[0],
        strike[1] + du * u[1] + dv * v[1],
        strike[2] + du * u[2] + dv * v[2],
    )


def select_target(
    parts: Sequence[dict[str, Any]],
    candidates: Sequence[str],
    *,
    tooth_width: float,
    standoff: float,
    exclude_span: bool = False,
) -> tuple[str, Approach]:
    """Pick which of several same-role parts the opponent actually strikes.

    A bot usually tags four side panels as armour, and a weapon is often a bar
    plus its bolt-on teeth. Only one of each gets hit, and the honest choice is
    the most *exposed* one — a panel an opponent cannot reach is not the panel
    that decides the match.

    Candidates no direction can reach are skipped rather than scored: that is
    ``choose_approach`` raising, which is a real answer about that part, not an
    error. Ties break on part id so the pick is reproducible.

    The user can override this in the submit form; this is what the form is
    pre-filled with, and what runs when they leave it alone.
    """
    scored: list[tuple[float, str, Approach]] = []
    unreachable: list[str] = []
    for part_id in candidates:
        try:
            approach = choose_approach(
                parts,
                [part_id],
                tooth_width=tooth_width,
                standoff=standoff,
                exclude_span=exclude_span,
            )
        except PlacementError:
            unreachable.append(part_id)
            continue
        scored.append((approach.exposure, part_id, approach))

    if not scored:
        raise PlacementError(
            "no candidate part can be struck from outside the assembly"
            + (f" (tried {', '.join(sorted(unreachable))})" if unreachable else "")
        )

    # Most exposed wins; the id keeps the order total when exposures tie.
    best = max(scored, key=lambda s: (s[0], s[1]))
    return best[1], best[2]


def _support_point(
    ellipsoid: tuple[tuple[float, float, float], tuple[float, float, float]],
    direction: Sequence[float],
) -> tuple[float, float, float]:
    """The point on the ellipsoid furthest along ``direction``.

    ``centre + (a_i**2 * n_i) / |A n|`` — the actual surface point, not the
    projection of the origin onto the supporting plane.
    """
    centre, semi = ellipsoid
    denom = math.sqrt(sum((semi[i] * direction[i]) ** 2 for i in range(3)))
    if denom <= 0:
        return (centre[0], centre[1], centre[2])
    return (
        centre[0] + semi[0] ** 2 * direction[0] / denom,
        centre[1] + semi[1] ** 2 * direction[1] / denom,
        centre[2] + semi[2] ** 2 * direction[2] / denom,
    )


def _perp_rect(
    ellipsoid: tuple[tuple[float, float, float], tuple[float, float, float]],
    u: Sequence[float],
    v: Sequence[float],
) -> tuple[float, float, float, float]:
    """``(cu, cv, half_u, half_v)`` of the ellipsoid's shadow on the ``u,v`` plane.

    Both half-widths, not the larger of the two. Collapsing them to one radius
    turns a 500 mm chassis rail 12 mm wide into a 500 mm disc centred mid-bot,
    which overlaps essentially every target in the machine.
    """
    centre, semi = ellipsoid
    return (
        _dot(centre, u),
        _dot(centre, v),
        math.sqrt(sum((semi[i] * u[i]) ** 2 for i in range(3))),
        math.sqrt(sum((semi[i] * v[i]) ** 2 for i in range(3))),
    )


def _union_bounds(
    rects: Sequence[tuple[float, float, float, float]],
) -> tuple[float, float, float, float]:
    """``(u0, v0, u1, v1)`` enclosing every rectangle."""
    return (
        min(c - h for c, _, h, _ in rects),
        min(c - h for _, c, _, h in rects),
        max(c + h for c, _, h, _ in rects),
        max(c + h for _, c, _, h in rects),
    )


def _shadow(
    targets: Sequence[tuple[tuple[float, float, float], tuple[float, float, float]]],
    others: Sequence[tuple[tuple[float, float, float], tuple[float, float, float]]],
    direction: Sequence[float],
    reach: float,
) -> tuple[float, tuple[float, float] | None]:
    """How much of the target is hidden along ``direction``, and where it is not.

    Returns ``(covered_fraction, exposed_centre)``, the latter in ``(u, v)``
    coordinates on the plane perpendicular to the strike, or ``None`` when
    nothing is exposed.

    A part shadows the target only if it reaches *past* the target's own
    surface; neighbours flush with an armour panel -- its frame, its bolts --
    sit within a millimetre by construction and obstruct nothing a blunt tooth
    cares about, which is what ``BLOCK_MARGIN_MM`` allows for.

    The covered area is computed exactly, by compressing the blocker edges
    into a grid and summing the cells that fall inside any blocker. Only
    blockers that actually overlap the target survive the clip, so the grid
    stays small however many parts the assembly has.
    """
    u, v = _perpendicular_basis(direction)
    lo_u, lo_v, hi_u, hi_v = _union_bounds([_perp_rect(e, u, v) for e in targets])
    area = (hi_u - lo_u) * (hi_v - lo_v)

    boxes: list[tuple[float, float, float, float]] = []
    for other in others:
        if _support(other, direction) <= reach + BLOCK_MARGIN_MM:
            continue
        cu, cv, hu, hv = _perp_rect(other, u, v)
        u0, u1 = max(lo_u, cu - hu), min(hi_u, cu + hu)
        v0, v1 = max(lo_v, cv - hv), min(hi_v, cv + hv)
        if u1 > u0 and v1 > v0:
            boxes.append((u0, v0, u1, v1))

    if area <= 0.0:
        # A target with no perpendicular extent -- a degenerate bbox seen
        # edge-on. Any overlapping blocker hides all of it.
        return (1.0 if boxes else 0.0), None
    if not boxes:
        return 0.0, ((lo_u + hi_u) / 2.0, (lo_v + hi_v) / 2.0)

    us = sorted({lo_u, hi_u, *(b[0] for b in boxes), *(b[2] for b in boxes)})
    vs = sorted({lo_v, hi_v, *(b[1] for b in boxes), *(b[3] for b in boxes)})
    covered = 0.0
    free = 0.0
    free_u = 0.0
    free_v = 0.0
    for i in range(len(us) - 1):
        u0, u1 = us[i], us[i + 1]
        if u1 <= u0:
            continue
        mu = (u0 + u1) / 2.0
        for j in range(len(vs) - 1):
            v0, v1 = vs[j], vs[j + 1]
            if v1 <= v0:
                continue
            mv = (v0 + v1) / 2.0
            cell = (u1 - u0) * (v1 - v0)
            if any(b[0] <= mu <= b[2] and b[1] <= mv <= b[3] for b in boxes):
                covered += cell
            else:
                free += cell
                free_u += mu * cell
                free_v += mv * cell
    centre = (free_u / free, free_v / free) if free > 0.0 else None
    return covered / area, centre


def _perpendicular_basis(
    direction: Sequence[float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Two unit vectors spanning the plane perpendicular to ``direction``."""
    seed = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (1.0, 0.0, 0.0)
    u = (
        direction[1] * seed[2] - direction[2] * seed[1],
        direction[2] * seed[0] - direction[0] * seed[2],
        direction[0] * seed[1] - direction[1] * seed[0],
    )
    u = _normalise(u)
    v = (
        direction[1] * u[2] - direction[2] * u[1],
        direction[2] * u[0] - direction[0] * u[2],
        direction[0] * u[1] - direction[1] * u[0],
    )
    return u, _normalise(v)


def _ellipsoid(bbox: Any) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """``(centre, semi_axes)`` of the ellipsoid inscribed in a bbox."""
    corners = bbox_corners(bbox)
    lo = tuple(min(c[i] for c in corners) for i in range(3))
    hi = tuple(max(c[i] for c in corners) for i in range(3))
    centre = tuple((lo[i] + hi[i]) / 2.0 for i in range(3))
    semi = tuple((hi[i] - lo[i]) / 2.0 for i in range(3))
    return centre, semi  # type: ignore[return-value]


def _support(
    ellipsoid: tuple[tuple[float, float, float], tuple[float, float, float]],
    direction: Sequence[float],
) -> float:
    """How far the ellipsoid reaches along ``direction`` from the origin."""
    centre, semi = ellipsoid
    radius = math.sqrt(sum((semi[i] * direction[i]) ** 2 for i in range(3)))
    return _dot(centre, direction) + radius


def _extent(
    ellipsoids: Sequence[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> list[float]:
    if not ellipsoids:
        raise PlacementError("nothing to place against")
    return [
        max(c[i] + s[i] for c, s in ellipsoids) - min(c[i] - s[i] for c, s in ellipsoids)
        for i in range(3)
    ]


def _span_axis(
    ellipsoids: Sequence[tuple[tuple[float, float, float], tuple[float, float, float]]],
) -> tuple[float, float, float] | None:
    """The axis a bar must not be struck along, or ``None`` if not a bar.

    ``None`` means the target has no span to protect -- a disc, a plate, a
    blade, a block -- and every approach direction stays legal. See
    :data:`SPAN_ASPECT_RATIO` for why elongation alone does not decide it.
    """
    extent = _extent(ellipsoids)
    order = sorted(range(3), key=lambda i: extent[i])
    e0, e1, e2 = (extent[i] for i in order)
    if e0 <= 0.0 or e1 <= 0.0:
        # A degenerate box has no meaningful cross-section to judge; treat it
        # as not-a-bar rather than excluding an axis on the strength of a zero.
        return None
    if not (e2 / e1 >= SPAN_ASPECT_RATIO and e1 / e0 < SPAN_ASPECT_RATIO):
        return None
    axis = [0.0, 0.0, 0.0]
    axis[order[2]] = 1.0
    return (axis[0], axis[1], axis[2])
