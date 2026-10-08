"""Scoped load-case construction — what actually gets solved.

The user uploads a whole robot; we do not simulate one. ``inertial-v6`` meshes
to 666,862 elements whole, and solved whole it dies: the timestep collapses on
slivers in the interference zones, mass scaling takes over, added mass reaches
100% and the run is killed. The same bot scoped to the struck panel plus the
opponent tooth is 96,112 elements and terminates normally at the physical
timestep with -0.38% energy drift.

**The cut is made at a physically derived radius.** Whatever is deleted has to
be replaced by a boundary condition, and that boundary is a lie unless the
stress wave never arrives at it. So the model keeps every part with geometry
within ``c_max * end_time`` of the strike point and deletes the rest:

    40 us  ->  202 mm in 7075, 206 mm in S7, 35 mm in UHMW
    400 us ->  2.0 m,  2.1 m,   347 mm

which means **scope radius and ``end_time`` are one decision**. A far-end clamp
on a 529 mm panel is honest at 40 us and dishonest at 400 us, where the wave
reaches it, reflects, and returns as energy the physics never contained. Push
``end_time`` far enough and the whole machine is back in the model, which is
the configuration that failed. See PLAN.md decision 32 and §10.

**v2 (2026-10-08) changes the boundary and the hit.** The far end is no longer
clamped: it is a rigid body carrying the mass of every part that is not
meshed, so the robot is knocked away rather than held. The radius is capped at
``SCOPE_RADIUS_CAP_MM`` (250 mm) because a long hit (up to 500 µs, run until it
is over) would otherwise pull the whole robot back in; past the cap the wave
meets that rigid remainder, which reflects earlier than real parts would — the
accepted cost. The impactor is a rigid tooth on a spinning weapon
(``case/weapon.py``, ``deck_spec``), and the target is meshed fine at the
strike and coarser with distance.

**Parts far from the strike are meshed coarsely.** A large element raises the
stable timestep rather than lowering it, so distance is nearly free: the
surround exists to carry the wave and hold the boundary, not to be resolved.

**The surround is stand-in blocks, not the neighbours' real geometry.** Two
things ruled the obvious approaches out. Penalty contact cannot hold the
surround on: bolted parts sit flush, TYPE7 reads a coincident face as an
initial penetration, and the starter rejects the deck with 7,331 errors —
identically at ``gap_scale`` 0.02, 0.10 and 1.00 and at ``inacti`` 0, 5 and 6,
so it is structural rather than a tuning failure. And fragmenting eighteen
arbitrary CAD solids into one conformal mesh invites slivers exactly where the
parts interfere, which is how Phase 5 died.

So each neighbour is replaced by a **box built from its bounding box, carrying
that neighbour's real material card**. At 40 us the wave does not traverse the
neighbour, so what governs the interface is acoustic impedance rho*c, and the
real material card makes that exact; the box only has to be the right stuff in
roughly the right place. Boxes are cut against the target and against each
other so nothing overlaps, then fragmented, so touching faces become shared
nodes and the assembly is tied with no contact interface at all. Fragmenting
clean boxes is a controlled operation in a way that fragmenting the CAD is
not, and the real parts' ordinal solid-to-part mapping is untouched, because
the boxes are not Tier 0 parts.

They chain outward from the target by adjacency: a box that touches nothing is
a free rigid body, so a neighbour is only placed if it meets the target or a
box already placed. The load transferred into the first ring is what LC2 wants
for "load into the chassis mounts"; LC1's bolt shear is that resultant divided
by the bolt count ``step_fasteners`` already extracts, reported as the
even-share estimate it is.
"""

from __future__ import annotations

import math
import time
from collections.abc import Iterable, Mapping, Sequence
from contextlib import suppress
from dataclasses import asdict, dataclass, field, replace
from pathlib import Path
from typing import Any

import yaml

from case.aim import load_surface
from case.mesh_metrics import MeshMetrics, analyse, wave_speed
from case.mmgpass import guarded_pass
from case.models import Aim, ImpactorSpec
from case.placement import Approach, PlacementError, choose_approach, snap_to_surface
from case.simgeom import (
    ALGO3D_FRONTAL,
    ALGO3D_HXT,
    GeometryError,
    build_tooth,
    generate_mesh,
    gmsh_session,
    import_solids,
    map_solids,
    name_group,
)
from case.standin_grid import structure_standins
from case.tooth import KG_PER_MG, ToothGeometry, tooth_geometry
from case.weapon import (Swing, estimate_end_time, flow_stress, parse_up,
                         weapon_inertia)
from case.weapon import swing as weapon_swing

__all__ = [
    "BuiltCase",
    "LoadCaseError",
    "body_bbox",
    "build_case",
    "suggest_scope",
    "deck_spec",
    "parts_in_scope",
    "scope_radius",
]

TOOTH_HEAD = "tooth_head"
TOOTH_BACK = "tooth_back"

#: The tooth starts this fraction of its travel away from the target, capped
#: below so a fast preset does not begin embedded in the panel.
STANDOFF_TRAVEL_FRACTION = 0.02
MAX_STANDOFF_MM = 0.5

#: How much coarser a stand-in is meshed than the target. It carries the wave
#: and holds the clamp; it is not where the answer is read.
SURROUND_COARSENING = 4.0

#: gmsh's ``Mesh.MeshSizeMin`` is set to ``mesh_size / this``. Named because
#: two callers need the same number: the worker hands it to gmsh, and the
#: parent hands it to :func:`cra.mesh_metrics.analyse` so the metrics can count
#: how many elements came out below the floor gmsh was given. Measured over the
#: retained corpus, 52 of 54 cases have at least one -- see ``CHAR_QUANTILES``.
MIN_SIZE_FRACTION = 5.0

#: A stand-in is placed only if it comes this close to the target or to a
#: stand-in already placed. Anything further off would be a free rigid body.
TOUCH_TOLERANCE_MM = 1.0

#: Boxes are cheap, so the cap is generous; it exists to stop a pathological
#: assembly turning into a hundred-body fragment operation.
MAX_STANDINS = 24

#: A stand-in smaller than this fraction of the target is not worth a body of
#: its own — a washer's bounding box does not change how the panel responds.
MIN_STANDIN_VOLUME_FRACTION = 0.01

#: Grid every stand-in box corner is snapped to, in mm.
#:
#: Tier 0 bounding boxes carry floating-point noise from the CAD kernel: one
#: corpus model reports a part spanning x = ±146.75001083 where its neighbour
#: spans exactly ±146.75. `fragment` faithfully turns that 10.7 nanometre
#: overhang into a 10.7 nanometre sliver, and the stable timestep goes with the
#: shortest edge in the *whole model* -- so one invisible artefact of the
#: bounding box priced a real job at 79 million cycles and 3,699x its budget.
#:
#: Measured, not guessed: two boxes fragmented with that exact overhang mesh to
#: a 1.073e-05 mm shortest edge; with their faces exactly coincident, 1.468 mm.
#:
#: A micron is far below anything this construction claims to resolve -- a
#: stand-in "only has to be the right material in roughly the right place" --
#: and far above the noise.
STANDIN_SNAP_MM = 1e-3

#: Fuzzy tolerance for the stand-in fragment, in mm.
#:
#: `STANDIN_SNAP_MM` puts two *boxes* on a common grid, which is what stopped
#: bounding-box noise slivering box against box. It cannot help where a box
#: meets the target's real CAD: the curved surface is not on the grid, so
#: snapping the box moves its face up to half a micron either side of where
#: the CAD actually is, and half the time that is *into* it. `fragment` then
#: turns the lens between them into a body of its own.
#:
#: Measured on a cylinder snapped 0.4 um inside its own bounding box: at an
#: exact boolean the target comes back as **two** bodies -- itself plus the
#: lens, which the earliest-input rule below hands to the target -- and the
#: lens is the razor the timestep then follows. A fuzzy boolean at 1e-2 mm
#: returns one body whose volume is identical to twelve digits, and puts the
#: 4.8e-3 mm3 of difference on the *stand-in's* side, where a body that "only
#: has to be the right material in roughly the right place" can carry it.
#:
#: Ten times `STANDIN_SNAP_MM`, and that gap is not slack: 1e-3 and 1e-4 both
#: left the lens intact. The snap sets where a box *is*; this sets how close
#: two faces have to be before OCC stops believing the difference.
STANDIN_FUZZ_MM = 1e-2

#: How far the target's own volume may move across the stand-in fragment
#: before the model is no longer about the part that was measured. The fuzzy
#: boolean above measured 0.0000% on the case it was fitted to; this is loose
#: enough not to fire on arithmetic and tight enough to catch OCC deciding to
#: absorb real material.
MAX_TARGET_VOLUME_DRIFT = 1e-3

#: Prefix for a stand-in's physical group. Never a Tier 0 part id, so nothing
#: downstream can mistake one for real geometry the user supplied.
STANDIN_PREFIX = "fill_"

#: How far a body's box may sit from where Tier 0 said it was before the two
#: are treated as describing different bodies. Measured across every
#: multi-solid target in the corpus, the two kernels agree to 0.000000 mm, so
#: this is a guard against a mismatch rather than a tolerance for a known
#: disagreement — anything above float noise means something is wrong.
SOLID_BOX_TOLERANCE_MM = 0.01

#: Fraction of the scoped model, measured back along the strike direction,
#: that is fixed. This is the cut face standing in for the rest of the bot.
CLAMP_FRACTION = 0.2

#: Friction between bot parts, and between the tooth and what it hits. Steel
#: on aluminium, unlubricated and not polished.
FRICTION = 0.15

#: v2 scope cap: past this the free robot's rigid remainder carries the load.
SCOPE_RADIUS_CAP_MM = 250.0
#: v2 graded mesh on the target: `mesh_size` within GRADE_R0_MM (or two tooth
#: widths) of the strike, then growing GRADE_GROWTH mm per mm of distance up to
#: GRADE_FAR_FACTOR x `mesh_size`.
GRADE_R0_MM = 20.0
GRADE_GROWTH = 0.15
GRADE_FAR_FACTOR = 4.0

#: TYPE7 initial-penetration handling. 6 = auto-depenetrate, which the corpus
#: needs: real assemblies overlap slightly and Phase 5 tripped over it.
INITIAL_PENETRATION = 6

_BIG_MESH_SIZE = 1.0e22


class LoadCaseError(RuntimeError):
    """The load case cannot be posed. Distinct from a geometry failure."""


def scope_radius(materials: Iterable[Mapping[str, Any]], end_time: float) -> float:
    """How far the impact can be felt in ``end_time``.

    The fastest wave among the assigned materials sets the radius, because it
    is the first thing to reach the boundary and the only one that has to be
    out of reach for the clamp to be honest.
    """
    if end_time <= 0:
        raise ValueError(f"end_time must be positive, got {end_time}")
    speeds = [wave_speed(m) for m in materials]
    fastest = max((c for c in speeds if c > 0), default=0.0)
    if fastest <= 0:
        raise LoadCaseError(
            "no assigned material has a usable wave speed, so the scope radius cannot be derived"
        )
    return fastest * end_time


def _bbox_distance(bbox: Mapping[str, Sequence[float]], point: Sequence[float]) -> float:
    """Distance from a point to a box; zero when the point is inside it."""
    lo, hi = bbox["min"], bbox["max"]
    gaps = [max(lo[i] - point[i], 0.0, point[i] - hi[i]) for i in range(3)]
    return math.sqrt(sum(g * g for g in gaps))


def parts_in_scope(
    parts: Sequence[dict[str, Any]],
    strike_point: Sequence[float],
    radius: float,
    *,
    always: Sequence[str] = (),
) -> list[str]:
    """Which parts survive the cut.

    A part is kept when any of its geometry lies within ``radius`` of the
    strike point — measured to its bounding box, which is generous in the
    right direction: including a part that did not need to be there costs
    elements, excluding one that did corrupts the answer.

    ``always`` is kept regardless, so the target survives even on a radius so
    short that nothing else does.
    """
    kept = list(always)
    for part in parts:
        part_id = part.get("id")
        bbox = part.get("bbox")
        if not part_id or part_id in kept or not bbox:
            continue
        if _bbox_distance(bbox, strike_point) <= radius:
            kept.append(str(part_id))
    return kept


@dataclass(frozen=True)
class BuiltCase:
    """One meshed, deck-ready load case, and how it came to look like that."""

    case: str
    target_id: str
    approach: Approach
    tooth: ToothGeometry
    radius: float
    scoped_ids: tuple[str, ...]
    mesh_path: Path
    spec_path: Path
    metrics: MeshMetrics
    warnings: tuple[str, ...] = ()
    #: ``{"structured": n, "fallback": n}`` when the mesh was built with
    #: ``structured_standins``; ``None`` on the production path.
    standin_grid: Mapping[str, int] | None = None
    #: :meth:`cra.mmgpass.PassResult.record` when the guarded mmg pass ran;
    #: ``None`` when it did not. ``accepted`` inside says whether
    #: ``mesh_path`` and ``metrics`` are the replacement or the original.
    mmg: Mapping[str, Any] | None = None
    #: v2: the contact time used (estimated when the caller passed None),
    #: the weapon's placement, and the robot's up axis.
    end_time: float | None = None
    swing: Swing | None = None
    up: tuple[float, float, float] | None = None
    #: where meshing time went, by stage (mesh_geometry's breakdown)
    stage_seconds: Mapping[str, float] | None = None

    @property
    def surround_ids(self) -> tuple[str, ...]:
        return tuple(p for p in self.scoped_ids if p != self.target_id)

    def describe(self) -> str:
        surround = len(self.surround_ids)
        return (
            f"{self.case}: {self.target_id} struck "
            f"{self.approach.describe()}; scope radius {self.radius:.0f} mm "
            f"keeps {surround} surrounding part(s); "
            f"{self.metrics.elements:,} elements, "
            f"{self.metrics.element_cycles:.2e} element-cycles"
        )


def _body_for_point(
    part: Mapping[str, Any], point: Sequence[float], fallback: int | None
) -> int | None:
    """Which body of a multi-solid part a strike point lands on.

    Boxes, not surfaces, because these are the same boxes ``choose_approach``
    scored and ``_struck_solid`` verifies the mesh against — a body picked
    here by one rule and checked there by another would disagree about exactly
    the geometry this exists to pin down.

    Containment first, since a graded strike sits on the target's surface and
    so inside its own body's box. Ties and near-misses fall back to the
    nearest box: sibling bodies may have overlapping boxes, and a point placed
    a standoff clear of the surface can sit just outside its own.
    """
    # Gaps in `solid_bboxes` are bodies Tier 0 could not box; they cannot be
    # ranked, and the surviving indices must not be renumbered around them.
    boxes = [(i, b) for i, b in enumerate(part.get("solid_bboxes") or ()) if b]
    if not boxes:
        return fallback

    gaps = [(i, _bbox_distance(b, point)) for i, b in boxes]
    inside = [i for i, g in gaps if g <= 0.0]
    if len(inside) == 1:
        return inside[0]
    return min(gaps, key=lambda pair: pair[1])[0]


def _with_aim(
    approach: Approach, aim: Aim, standoff: float, part: Mapping[str, Any] | None = None
) -> Approach:
    """Replace the prefill's aim with the user's, and keep the rest.

    ``choose_approach`` still runs, because two of the fields it produces are
    not things a user supplies: ``exposure`` and ``reach`` describe how the
    part sits in the assembly, and ``arbitrary`` records that a rotationally
    symmetric target has no meaningful azimuth.

    ``solid_index`` was kept from it too, on the grounds that which body is
    struck is not the user's to decide. That was wrong, and it cost a real
    job. ``choose_approach`` ranks bodies by *exposure* and never sees the
    aim, so on inertial-v6 it chose p035's 400x422x73 mm shell while the
    builder had aimed at the 54x58x58 mm boss at the other end of the machine
    — the prefill sat at y = +347, the builder moved it to y = -76. Only the
    shell was meshed, and the tooth was fired at a point 1.46 mm outside it,
    into the space where the boss would have been. B3.5 grades an aim against
    the part's whole surface, so it passed; the first sign of trouble was B4
    refusing with "no usable density or wave speed for p035", about a part
    whose material the builder had chosen by hand.

    So the body is derived from the point the user actually placed. For a
    single-body part, or one with no per-solid boxes, this is what
    ``choose_approach`` already said.

    ``standoff`` is recomputed rather than taken from the aim. The browser's
    value is a viewing convenience chosen against a drawn tooth; the real one
    is derived from ``end_time`` so a faster preset does not start embedded,
    and that arithmetic is not the user's to override.
    """
    solid_index = approach.solid_index
    if part is not None:
        solid_index = _body_for_point(part, aim.point, solid_index)
    return replace(
        approach,
        strike_point=aim.point,
        direction=aim.direction,
        standoff=standoff,
        solid_index=solid_index,
    )


def _clamp_box(direction: Sequence[float]) -> dict[str, float]:
    """The far end of the scoped model, back along the strike direction.

    ``direction`` points from the target out towards where the tooth starts,
    so the tooth travels along ``-direction`` and the face to hold is the one
    it is heading for. ``rel_box`` reads ``xmax`` as "at or below this fraction
    of the range", hence the asymmetry between the two branches.
    """
    axis = max(range(3), key=lambda i: abs(direction[i]))
    name = "xyz"[axis]
    if direction[axis] > 0:
        return {f"{name}max": CLAMP_FRACTION}
    return {f"{name}min": 1.0 - CLAMP_FRACTION}


def deck_spec(
    *,
    case: str,
    target_id: str,
    standin_ids: Sequence[str],
    materials: Mapping[str, str],
    library: Mapping[str, Any],
    tooth: ToothGeometry,
    approach: Approach,
    impactor: ImpactorSpec,
    end_time: float,
    swing: Swing,
    rest: Mapping[str, Any],
    timestep_scale: float = 0.9,
) -> dict[str, Any]:
    """Assemble the simulation spec, deterministically (v1: PLAN.md decision 34).

    v2 physics (2026-10-08), replacing v1's deformable tooth fired along the
    surface normal at a clamped target:

    - **The weapon is a rigid body spinning about its axis.** The tooth head is
      meshed for its contact shape and made rigid; the weapon's spin inertia
      (`m_eff * R**2`, which is what the preset's energy defines) and the
      opponent's whole mass sit on a hub node on the axis, so after the hit
      the weapon recoils with its robot. `/INIVEL/AXIS` starts it at
      `omega = v_tip / R`; the tooth meets the target along its arc and can
      glance off.
    - **The struck robot is free.** The far end of the model, which v1 fixed,
      is a rigid body carrying the mass and inertia of every part that is not
      meshed (`rest`), so the robot is knocked away instead of held.
    - **Parts erode** at their material's `eps_max` (/FAIL cards).
    """
    tooth_material = "tooth_rigid"
    s7 = library[impactor.material]
    # The head is rigid (its nodes belong to the weapon's /RBODY); the card
    # only sets contact stiffness and the timestep, so plain elastic S7.
    tooth_card = {"law": "elastic", "density": s7["density"], "young": s7["young"],
                  "poisson": s7["poisson"]}

    used: dict[str, Any] = {tooth_material: tooth_card}
    for part_id in (target_id, *standin_ids):
        name = materials[part_id]
        used[name] = dict(library[name])

    opponent_mg = float(impactor.opponent_mass_kg) / KG_PER_MG
    # The meshed head is part of the rigid weapon and contributes its own
    # inertia at the arc radius; the hub carries the rest, so the total spin
    # energy is the preset's (it came out 10 % high on inertial-v6 before).
    head_mg = tooth.head_volume * float(s7["density"])
    i_axis = max(0.0, float(impactor.m_eff_kg) / KG_PER_MG - head_mg) * swing.radius_mm ** 2

    spec: dict[str, Any] = {
        "name": case,
        "units": "mm_Mg_s",
        "erosion_from_eps_max": True,
        "parts": (
            [{"match": target_id, "material": materials[target_id]}]
            + [
                {"match": f"{STANDIN_PREFIX}{pid}", "material": materials[pid]}
                for pid in standin_ids
            ]
            + [{"match": TOOTH_HEAD, "material": tooth_material}]
        ),
        "materials": used,
        "extra_nodes": [
            {"name": "hub", "xyz": list(swing.hub)},
            {"name": "chassis", "xyz": list(rest["com"])},
        ],
        "sets": {
            "mount": {"rel_box": _clamp_box(approach.direction), "with_nodes": ["chassis"]},
            "weapon": {"part": "tooth*", "with_nodes": ["hub"]},
        },
        "rigid_bodies": [
            {"name": "weapon", "set": "weapon", "main": "hub", "mass": opponent_mg,
             "inertia": weapon_inertia(swing.axis, i_axis)},
            {"name": "rest_of_robot", "set": "mount", "main": "chassis",
             "mass": float(rest["mass"]), "inertia": list(rest["inertia"])},
        ],
        "initial_velocity": [{
            "name": "spin", "set": "weapon",
            "axis": {"origin": list(swing.hub), "direction": list(swing.axis), "omega": swing.omega},
        }],
        "contact": [
            {
                "name": "tooth_into_target",
                "secondary": TOOTH_HEAD,
                "main": target_id,
                "friction": FRICTION,
                "gap_scale": 0.10,
                "initial_penetration": INITIAL_PENETRATION,
            },
            {
                "name": "target_into_tooth",
                "secondary": target_id,
                "main": TOOTH_HEAD,
                "friction": FRICTION,
                "gap_scale": 0.10,
            },
        ],
        "control": {
            "end_time": end_time,
            "animation_dt": end_time / 20.0,
            "th_dt": end_time / 200.0,
            "timestep_scale": timestep_scale,
        },
    }
    return spec


def rest_of_robot(
    parts: Sequence[Mapping[str, Any]],
    materials: Mapping[str, str],
    library: Mapping[str, Any],
    meshed_ids: Sequence[str],
) -> dict[str, Any]:
    """Mass, centre and inertia (about that centre) of every part not meshed.

    Each part is a point mass at its centre of mass (volume x density); the
    sum is what the free robot's rigid remainder carries. Parts with no
    volume, centre or density are skipped and counted in `unmeasured`.
    """
    meshed = set(meshed_ids)
    pts: list[tuple[float, tuple[float, float, float]]] = []
    unmeasured = 0
    for p in parts:
        pid = str(p.get("id"))
        if pid in meshed:
            continue
        card = library.get(materials.get(pid, ""), {})
        com, vol = p.get("com"), p.get("volume_mm3")
        if not com or not vol or not card.get("density"):
            unmeasured += 1
            continue
        pts.append((float(vol) * float(card["density"]), tuple(float(c) for c in com)))
    mass = sum(m for m, _ in pts)
    if mass <= 0:
        return {"mass": 0.0, "com": [0.0, 0.0, 0.0], "inertia": [0.0] * 6, "unmeasured": unmeasured}
    com = tuple(sum(m * c[i] for m, c in pts) / mass for i in range(3))
    J = [[0.0] * 3 for _ in range(3)]
    for m, c in pts:
        r = [c[i] - com[i] for i in range(3)]
        r2 = sum(v * v for v in r)
        for i in range(3):
            for j in range(3):
                J[i][j] += m * ((r2 if i == j else 0.0) - r[i] * r[j])
    return {"mass": mass, "com": list(com),
            "inertia": [J[0][0], J[1][1], J[2][2], J[0][1], J[1][2], J[0][2]],
            "unmeasured": unmeasured}

def _box_gap(a: Mapping[str, Sequence[float]], b: Mapping[str, Sequence[float]]) -> float:
    """Shortest distance between two axis-aligned boxes; zero if they touch."""
    gaps = [max(a["min"][i] - b["max"][i], b["min"][i] - a["max"][i], 0.0) for i in range(3)]
    return math.sqrt(sum(g * g for g in gaps))


def body_bbox(part: Mapping[str, Any], solid_index: int | None) -> Mapping[str, Any]:
    """The struck body's box, or the whole part's when there is only one.

    A part built from several bodies -- mirrored left/right armour exported
    together, a weapon hub and its teeth -- has a bounding box spanning all of
    them, which for a mirrored pair is the width of the machine and is nobody's
    geometry. Laying stand-ins out against that box, or chaining adjacency from
    it, reasons about a shape that does not exist.

    Falls back to the part box when there is no per-solid box to use, which is
    the documented back-compatible path for a report cached before they
    existed.
    """
    whole: Mapping[str, Any] = part["bbox"]
    boxes = part.get("solid_bboxes")
    if solid_index is None or not boxes:
        return whole
    try:
        body: Mapping[str, Any] | None = boxes[solid_index]
    except (IndexError, TypeError):
        return whole
    # `None` where Tier 0 could not box that body: the entry is held open so
    # the indices keep naming the same solids, and the part box is the same
    # fallback a report cached before `solid_bboxes` existed gets.
    return body if body else whole


def order_standins(
    parts: Sequence[dict[str, Any]],
    target_id: str,
    scoped_ids: Sequence[str],
    *,
    target_bbox: Mapping[str, Any] | None = None,
    tolerance: float = TOUCH_TOLERANCE_MM,
    limit: int = MAX_STANDINS,
) -> list[str]:
    """Which neighbours get a stand-in, and in what order to build them.

    Grown outward from the target by adjacency rather than taken straight off
    the scope list: a block that touches nothing is a free rigid body, which
    the solver will happily accelerate off to infinity. Each ring is ordered by
    how close it comes to what is already placed, so the build order is
    deterministic and the nearest — the ones actually holding the target on —
    are the ones kept when the cap bites.
    """
    by_id = {str(p["id"]): p for p in parts if p.get("id") and p.get("bbox")}
    if target_id not in by_id:
        return []

    placed_boxes = [target_bbox or by_id[target_id]["bbox"]]
    remaining = [p for p in scoped_ids if p != target_id and p in by_id]
    order: list[str] = []

    while remaining and len(order) < limit:
        reachable = [
            (min(_box_gap(by_id[pid]["bbox"], box) for box in placed_boxes), pid)
            for pid in remaining
        ]
        reachable = [(gap, pid) for gap, pid in reachable if gap <= tolerance]
        if not reachable:
            break  # nothing else is connected; the chain ends here
        _, nearest = min(reachable, key=lambda item: (item[0], item[1]))
        order.append(nearest)
        placed_boxes.append(by_id[nearest]["bbox"])
        remaining.remove(nearest)

    return order


def clip_outside(
    box: Mapping[str, Sequence[float]],
    obstacle: Mapping[str, Sequence[float]],
) -> tuple[list[float], list[float]] | None:
    """Pull an axis-aligned box back so it abuts an obstacle instead of
    overlapping it.

    Done in arithmetic rather than with a boolean because OCC will not cut a
    box against a thin panel whose faces coincide with it — ``BOPAlgo_Alert
    BuilderFailed`` on the first attempt. Clipping is along the single axis
    where the box's centre is furthest outside the obstacle's, which keeps the
    result a box: a stand-in only has to be the right material in roughly the
    right place, so an L-shaped exact remainder buys nothing.

    Returns ``None`` when the box's centre lies inside the obstacle, since
    there is then no side to push it out to.
    """
    lo = [float(v) for v in box["min"]]
    hi = [float(v) for v in box["max"]]
    olo = [float(v) for v in obstacle["min"]]
    ohi = [float(v) for v in obstacle["max"]]

    if any(hi[i] <= olo[i] or lo[i] >= ohi[i] for i in range(3)):
        return lo, hi  # already clear of it

    centre = [(lo[i] + hi[i]) / 2.0 for i in range(3)]
    ocentre = [(olo[i] + ohi[i]) / 2.0 for i in range(3)]
    axis = max(range(3), key=lambda i: abs(centre[i] - ocentre[i]))

    if centre[axis] >= ocentre[axis]:
        lo[axis] = max(lo[axis], ohi[axis])
    else:
        hi[axis] = min(hi[axis], olo[axis])

    if hi[axis] - lo[axis] <= 0:
        return None
    return lo, hi


def _standin_boxes(
    parts_by_id: Mapping[str, Mapping[str, Any]],
    order: Sequence[str],
    target_bbox: Mapping[str, Sequence[float]],
    *,
    min_volume: float,
) -> list[tuple[str, list[float], list[float]]]:
    """Lay the stand-ins out so none overlaps the target or each other."""
    placed: list[tuple[str, list[float], list[float]]] = []
    for part_id in order:
        clipped = clip_outside(parts_by_id[part_id]["bbox"], target_bbox)
        for _, plo, phi in placed:
            if clipped is None:
                break
            clipped = clip_outside({"min": clipped[0], "max": clipped[1]}, {"min": plo, "max": phi})
        if clipped is None:
            continue
        lo, hi = clipped
        # Snap to the grid before measuring. Two boxes meant to share a face
        # share it exactly afterwards, which is what stops `fragment` making a
        # sliver out of bounding-box noise; and a box left thinner than the
        # grid collapses to zero here and is dropped by the check below,
        # instead of becoming the element that sets the model's timestep.
        lo = [round(v / STANDIN_SNAP_MM) * STANDIN_SNAP_MM for v in lo]
        hi = [round(v / STANDIN_SNAP_MM) * STANDIN_SNAP_MM for v in hi]
        size = [hi[i] - lo[i] for i in range(3)]
        if min(size) <= 0 or size[0] * size[1] * size[2] < min_volume:
            # A washer's bounding box, or a box wholly swallowed by one
            # already placed. Its material is represented either way.
            continue
        placed.append((part_id, lo, hi))
    return placed


def _add_standins(
    gmsh: Any,
    boxes: Sequence[tuple[str, list[float], list[float]]],
    target_tags: Sequence[int],
    *,
    target_volume: float | None = None,
) -> tuple[list[int], dict[str, list[int]]]:
    """Build the boxes and tie them to the target with one fragment.

    Fragment rather than cut: the boxes are already laid out not to overlap,
    so all this has to do is make coincident faces shared, and one boolean
    over clean boxes is far better behaved than a cut per box against the CAD.
    A shared node carries tension, which is what a bolted bracket does and
    penalty contact cannot.

    The fragment is *fuzzy* — see :data:`STANDIN_FUZZ_MM`. Snapping puts two
    boxes on a shared grid, but the target is real CAD and is not on it, so a
    snapped box face lands a fraction of a micron inside the target about half
    the time and the exact boolean makes a body out of the lens. Given to the
    target by the earliest-input rule below, that lens is then the shortest
    edge in the model and sets the timestep for the whole job.

    ``target_volume`` is checked against what survives, when given. A fuzzy
    boolean is OCC being told to stop believing differences below a tolerance,
    and the one thing it must never round away is the customer's own part.
    """
    if not boxes:
        return list(target_tags), {}

    tags_for: dict[str, list[int]] = {}
    for part_id, lo, hi in boxes:
        tag = gmsh.model.occ.addBox(
            lo[0], lo[1], lo[2], hi[0] - lo[0], hi[1] - lo[1], hi[2] - lo[2]
        )
        tags_for[part_id] = [tag]
    gmsh.model.occ.synchronize()

    objects = [(3, tag) for tag in target_tags]
    tools = [(3, tag) for tags in tags_for.values() for tag in tags]
    # Scoped to this one boolean and put back afterwards: the tolerance is
    # justified by how a stand-in is built, and nothing else in the session
    # has that excuse.
    previous_fuzz = gmsh.option.getNumber("Geometry.ToleranceBoolean")
    gmsh.option.setNumber("Geometry.ToleranceBoolean", STANDIN_FUZZ_MM)
    try:
        _, mapping = gmsh.model.occ.fragment(objects, tools)
    finally:
        gmsh.option.setNumber("Geometry.ToleranceBoolean", previous_fuzz)
    gmsh.model.occ.synchronize()

    # ``mapping`` runs parallel to objects + tools. The boxes were laid out
    # not to overlap, so a fragment belongs to one input; where OCC still
    # reports a shared piece, the earliest input wins, which is the nearest
    # neighbour and so the one holding the target on.
    claimed: set[int] = set()

    def take(entries: Sequence[tuple[int, int]]) -> list[int]:
        out = []
        for dim, tag in entries:
            if dim == 3 and tag not in claimed:
                claimed.add(tag)
                out.append(tag)
        return out

    new_target = [tag for entry in mapping[: len(objects)] for tag in take(entry)]
    cursor = len(objects)
    for part_id, tags in list(tags_for.items()):
        fragments: list[int] = []
        for _ in tags:
            fragments.extend(take(mapping[cursor]))
            cursor += 1
        if fragments:
            tags_for[part_id] = fragments
        else:
            del tags_for[part_id]

    if target_volume is not None and target_volume > 0:
        survived = sum(gmsh.model.occ.getMass(3, tag) for tag in new_target)
        drift = abs(survived - target_volume) / target_volume
        if drift > MAX_TARGET_VOLUME_DRIFT:
            raise LoadCaseError(
                f"the fragment moved the struck part's own volume by "
                f"{drift * 100:.3f}%, {target_volume:.3f} to {survived:.3f} "
                f"mm3, so the report would be about geometry nothing measured"
            )

    return new_target, tags_for


def _size_fields(
    gmsh: Any,
    buckets: Sequence[tuple[Sequence[int], float]],
    graded: Mapping[str, Any] | None = None,
) -> None:
    """Drive mesh size per volume, and let nothing else override it.

    A per-point size hint does not survive ``Mesh.MeshSizeMax``, which is how
    a 0.517 kg lump of inert backing came to be meshed at 116,883 elements —
    more than the target it was there to hit. Fields do survive it, provided
    every volume is covered by one, which is why the ``Min`` combination below
    has no fall-through case.
    """
    fields = []
    if graded and graded.get("volumes"):
        # Fine where the tooth strikes, coarser with distance: the far shell
        # only carries the wave, and element count is what a long hit costs.
        # A Ball field: `near` within r0 of the strike, a linear ramp to `far`
        # over (far - near) / growth. A MathEval formula field computing the
        # same thing segfaulted gmsh on inertial-v6 (formula fields are not
        # safe under gmsh's multithreaded meshing); Ball is built in.
        cx, cy, cz = graded["centre"]
        near, r0, growth, far = graded["near"], graded["r0"], graded["growth"], graded["far"]
        ball = gmsh.model.mesh.field.add("Ball")
        for key, value in (("XCenter", cx), ("YCenter", cy), ("ZCenter", cz), ("Radius", r0),
                           ("Thickness", max(1e-6, (far - near) / growth)),
                           ("VIn", near), ("VOut", far)):
            gmsh.model.mesh.field.setNumber(ball, key, float(value))
        # Unrestricted: a Restrict wrapper (target volumes only) made the
        # build take >10 min instead of under 3, querying entity membership at
        # every size evaluation. Unrestricted is equivalent here: the field is
        # combined by Min with each other volume's own Constant, so it can only
        # refine near the strike, where the tooth already is fine.
        fields.append(float(ball))
    for volumes, size in buckets:
        if not volumes:
            continue
        f = gmsh.model.mesh.field.add("Constant")
        gmsh.model.mesh.field.setNumbers(f, "VolumesList", [float(v) for v in volumes])
        gmsh.model.mesh.field.setNumber(f, "VIn", size)
        gmsh.model.mesh.field.setNumber(f, "VOut", _BIG_MESH_SIZE)
        fields.append(float(f))

    if not fields:
        return
    combined = gmsh.model.mesh.field.add("Min")
    gmsh.model.mesh.field.setNumbers(combined, "FieldsList", fields)
    gmsh.model.mesh.field.setAsBackgroundMesh(combined)

    gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
    gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", 0)
    gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 0)


#: A tet whose gamma (inscribed / circumscribed radius, normalised to 1 for a
#: regular tet) is under this is a sliver the solver cannot carry. The
#: inertial-v6 slivers that blew a solve up were 0.0003-0.001; clean graded
#: meshes there bottomed out at 0.08.
SLIVER_GAMMA = 0.02

#: Meshing attempts before a build is refused for slivers, and how much a
#: stand-in's element size shrinks per retry when it had them.
MESH_ATTEMPTS = 4
STANDIN_REFINE = 0.7


def _slivers(gmsh: Any) -> dict[str, int]:
    """Sliver tets per physical group (only groups that have any)."""
    out: dict[str, int] = {}
    for dim, tag in gmsh.model.getPhysicalGroups(3):
        tets: list[int] = []
        for ent in gmsh.model.getEntitiesForPhysicalGroup(dim, tag):
            types, elems, _ = gmsh.model.mesh.getElements(3, ent)
            for t, e in zip(types, elems):
                if t == 4:
                    tets.extend(int(x) for x in e)
        if not tets:
            continue
        n = sum(1 for q in gmsh.model.mesh.getElementQualities(tets, "gamma") if q < SLIVER_GAMMA)
        if n:
            out[gmsh.model.getPhysicalName(dim, tag)] = n
    return out


def _tets_by_group(gmsh: Any) -> dict[str, list[list[tuple[float, float, float]]]]:
    """Pull the mesh back out of the session, keyed by physical-group name."""
    node_xyz: dict[int, tuple[float, float, float]] = {}
    tags, coords, _ = gmsh.model.mesh.getNodes()
    for i, tag in enumerate(tags):
        node_xyz[int(tag)] = (coords[3 * i], coords[3 * i + 1], coords[3 * i + 2])

    groups: dict[str, list[list[tuple[float, float, float]]]] = {}
    for dim, gtag in gmsh.model.getPhysicalGroups(3):
        name = gmsh.model.getPhysicalName(dim, gtag)
        tets: list[list[tuple[float, float, float]]] = []
        for entity in gmsh.model.getEntitiesForPhysicalGroup(dim, gtag):
            etypes, _, enodes = gmsh.model.mesh.getElements(3, entity)
            for etype, nodes in zip(etypes, enodes, strict=True):
                if etype != 4:  # linear tet
                    continue
                for k in range(0, len(nodes), 4):
                    tets.append([node_xyz[int(n)] for n in nodes[k : k + 4]])
        groups[name] = tets
    return groups


@dataclass
class _Session:
    """Scratch state threaded through the gmsh block, kept out of the flow."""

    warnings: list[str] = field(default_factory=list)


def _approach(
    parts: Sequence[dict[str, Any]],
    target_id: str,
    impactor: ImpactorSpec,
    geom: ToothGeometry,
    *,
    end_time: float,
    exclude_span: bool,
    aim: Aim | None,
    geometry: Mapping[str, Any] | None,
) -> Approach:
    """Where the tooth starts and which way it travels, aim applied."""
    by_id = {str(p["id"]): p for p in parts if p.get("id")}
    # 2% of the tooth's travel, so a faster preset does not start embedded.
    travel = float(impactor.v_tip_ms) * 1000.0 * end_time
    standoff = min(MAX_STANDOFF_MM, STANDOFF_TRAVEL_FRACTION * travel)

    approach = choose_approach(
        parts,
        [target_id],
        tooth_width=geom.head_width,
        standoff=standoff,
        exclude_span=exclude_span,
    )
    if aim is not None:
        approach = _with_aim(approach, aim, standoff, by_id.get(target_id))
    elif geometry is not None:
        # No user aim, so the prefill is what gets solved. Snap it onto the
        # material rather than solving a strike that starts in mid-air: this
        # is the difference between a run that tests something and one that
        # reports NORMAL TERMINATION having never made contact.
        # No surface for this part is survivable: the prefill stands and the
        # aim gate is the backstop.
        with suppress(KeyError, ValueError):
            approach = snap_to_surface(approach, load_surface(geometry, target_id))
    return approach


def resolve_end_time(
    end_time: float | None,
    impactor: ImpactorSpec,
    target_card: Mapping[str, Any],
) -> float:
    """`end_time`, or the estimated length of the hit when it is None.

    One function for build and scope, so a scope preview and the build it
    previews use the same duration (and so the same radius)."""
    if end_time is not None:
        return float(end_time)
    geom = tooth_geometry(impactor)
    return estimate_end_time(float(impactor.ke_j), float(impactor.v_tip_ms),
                             flow_stress(target_card), geom.head_width * geom.head_thickness)


def suggest_scope(
    parts: Sequence[dict[str, Any]],
    materials: Mapping[str, str],
    library: Mapping[str, Any],
    *,
    target_id: str,
    impactor: ImpactorSpec,
    end_time: float,
    exclude_span: bool,
    aim: Aim | None = None,
    geometry: Mapping[str, Any] | None = None,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
) -> dict[str, Any]:
    """The part set :func:`build_case` will carry, without meshing.

    Two lists, because they differ a lot on a real bot: ``in_radius_ids`` is
    every part the stress wave can reach (inertial-v6: 38 parts), and
    ``standin_ids`` is the subset that actually gets a stand-in block: only
    neighbours that connect back to the target by touching it or another
    placed block (capped at ``MAX_STANDINS``), whose box survives clipping
    against the target and the blocks before it, and is not negligibly small
    beside the struck body. Applies ``include``/``exclude`` exactly as
    ``build_case`` does. If the stand-in fragment fails in OCC, ``build_case``
    falls back to the target alone and says so.
    """
    by_id = {str(p["id"]): p for p in parts if p.get("id")}
    if target_id not in by_id:
        raise LoadCaseError(f"target {target_id} is not in this assembly")
    geom = tooth_geometry(impactor)
    approach = _approach(
        parts, target_id, impactor, geom,
        end_time=end_time, exclude_span=exclude_span, aim=aim, geometry=geometry,
    )
    radius = min(SCOPE_RADIUS_CAP_MM,
                 scope_radius((library[materials[pid]] for pid in by_id), end_time))
    in_radius = parts_in_scope(parts, approach.strike_point, radius, always=[target_id])
    scoped = in_radius if include is None else [target_id, *(p for p in include if p != target_id)]
    dropped = set(exclude) - {target_id}
    scoped = [pid for pid in scoped if pid not in dropped]
    target_box = body_bbox(by_id[target_id], approach.solid_index)
    wanted = order_standins(list(parts), target_id, scoped, target_bbox=target_box)
    # The same clip-and-cull mesh_geometry applies, sized against the struck
    # body's volume as the report measured it rather than as gmsh will.
    target = by_id[target_id]
    volumes = target.get("solid_volumes")
    if approach.solid_index is not None and volumes and volumes[approach.solid_index]:
        target_volume = float(volumes[approach.solid_index])
    else:
        target_volume = float(target.get("volume_mm3") or 0.0)
    boxes = _standin_boxes(
        by_id, wanted, target_box, min_volume=MIN_STANDIN_VOLUME_FRACTION * target_volume
    )
    standins = [pid for pid, _, _ in boxes]
    return {
        "radius_mm": radius,
        "strike_point": list(approach.strike_point),
        "target_id": target_id,
        "in_radius_ids": in_radius,
        "standin_ids": standins,
        "scoped_ids": [target_id, *standins],
    }


def build_case(
    step_path: str | Path,
    parts: Sequence[dict[str, Any]],
    materials: Mapping[str, str],
    library: Mapping[str, Any],
    *,
    case: str,
    target_id: str,
    impactor: ImpactorSpec,
    workdir: str | Path,
    end_time: float | None,
    mesh_size: float,
    exclude_span: bool,
    aim: Aim | None = None,
    geometry: Mapping[str, Any] | None = None,
    timestep_scale: float = 0.9,
    with_standins: bool = True,
    include: Sequence[str] | None = None,
    exclude: Sequence[str] = (),
    up_axis: str | None = None,
    graded: bool = True,
    algo3d: int | None = None,
    optimize_threshold: float | None = None,
    structured_standins: bool = False,
    mmg_pass: bool = False,
) -> BuiltCase:
    """Build, scope and mesh one load case, and estimate what it will cost.

    One attempt, in this process. v1 retried without stand-ins inside a
    child-process mesh worker; in v2 each attempt is its own sandbox exec, so
    a gmsh segfault ends the exec and the *agent* retries with
    ``with_standins=False`` and tells the user mount loads are gone.

    Scoping is a suggestion the user can override: ``include`` replaces the
    radius-derived scope (``suggest_scope``) with an explicit list of part
    ids to carry as stand-ins, and ``exclude`` drops ids from whichever scope
    applies. The target is always kept.

    ``end_time=None`` estimates how long the hit lasts (``weapon.
    estimate_end_time``). ``up_axis`` (``auto``/``x``/``-y``/...) orients the
    weapon; ``auto`` is the robot's thinnest dimension. ``graded`` meshes the
    target fine at the strike and coarser with distance.
    """
    work = Path(workdir)
    work.mkdir(parents=True, exist_ok=True)
    state = _Session()
    auto_algo = algo3d is None
    if algo3d is None:
        # HXT (parallel Delaunay) on graded meshes: inertial-v6 meshed in 4.6 s
        # against Frontal's 771 s, with fewer slivers (char p0.1 0.413 mm vs
        # 0.247) and a 2.4x larger nodal timestep. v1 chose Frontal on uniform
        # meshes because Delaunay hit PLC errors on some multi-body assemblies.
        algo3d = ALGO3D_HXT if graded else ALGO3D_FRONTAL

    by_id = {str(p["id"]): p for p in parts if p.get("id")}
    if target_id not in by_id:
        raise LoadCaseError(f"{case}: target {target_id} is not in this assembly")

    # The head only: the weapon is rigid and its mass and inertia live on the
    # hub (deck_spec), so there is no lumped backing block any more.
    geom = replace(tooth_geometry(impactor), back_section=0.0, back_volume=0.0)

    end_time = resolve_end_time(end_time, impactor, library[materials[target_id]])

    approach = _approach(
        parts, target_id, impactor, geom,
        end_time=end_time, exclude_span=exclude_span, aim=aim, geometry=geometry,
    )

    boxes = [p["bbox"] for p in parts if p.get("bbox")]
    lo = [min(float(b["min"][i]) for b in boxes) for i in range(3)]
    hi = [max(float(b["max"][i]) for b in boxes) for i in range(3)]
    up = parse_up(up_axis, lo, hi)
    try:
        sw = weapon_swing(approach.strike_point, approach.direction, up, str(impactor.archetype),
                          float(impactor.r_arc_mm), float(impactor.v_tip_ms),
                          [(lo[i] + hi[i]) / 2.0 for i in range(3)])
    except ValueError as exc:
        raise LoadCaseError(f"{case}: {exc}") from exc
    # The tooth is placed for its travel direction, not the surface normal:
    # its striking edge leads along the arc's tangent at the strike point.
    tooth_approach = replace(approach, direction=tuple(-c for c in sw.tip_velocity))

    # Beyond SCOPE_RADIUS_CAP_MM the free robot's rigid remainder carries the
    # load instead of more meshed parts; a long hit would otherwise pull the
    # whole robot into the mesh (how v1's whole-bot runs died).
    radius = min(SCOPE_RADIUS_CAP_MM,
                 scope_radius((library[materials[pid]] for pid in by_id), end_time))
    if include is None:
        scoped_ids = parts_in_scope(parts, approach.strike_point, radius, always=[target_id])
    else:
        scoped_ids = [target_id, *(pid for pid in include if pid != target_id)]
    dropped = set(exclude) - {target_id}
    scoped_ids = [pid for pid in scoped_ids if pid not in dropped]

    struck_box = body_bbox(by_id[target_id], approach.solid_index)
    wanted = (
        order_standins(list(parts), target_id, scoped_ids, target_bbox=struck_box)
        if with_standins
        else []
    )

    mesh_path = work / f"{case}.msh"
    payload = {
        "step_path": str(step_path),
        "parts": list(parts),
        "target_id": target_id,
        "wanted": list(wanted),
        "geom": asdict(geom),
        "approach": asdict(tooth_approach),
        "width_axis": list(sw.axis),
        "grade": ({"centre": list(approach.strike_point), "r0": max(GRADE_R0_MM, 2.0 * geom.head_width),
                   "growth": GRADE_GROWTH, "far": GRADE_FAR_FACTOR * mesh_size}
                  if graded else None),
        "mesh_size": mesh_size,
        "mesh_path": str(mesh_path),
        "algo3d": algo3d,
        "optimize_threshold": optimize_threshold,
        "structured_standins": structured_standins,
    }
    try:
        meshed = mesh_geometry(payload)
    except GeometryError as exc:
        if not (auto_algo and algo3d == ALGO3D_HXT):
            raise
        # HXT's PLC check rejects some multi-body assemblies outright ("A
        # vertex lies in a segment" on meowtybrain); Frontal meshes them, more
        # slowly. Only the automatic choice falls back; an explicit one fails.
        state.warnings.append(f"Fast meshing failed ({str(exc)[:120]}); used the slower mesher.")
        payload["algo3d"] = ALGO3D_FRONTAL
        meshed = mesh_geometry(payload)
    state.warnings.extend(meshed["warnings"])
    groups = {str(k): v for k, v in meshed["groups"].items()}
    standin_ids = list(meshed["standin_ids"])
    has_tooth_back = bool(meshed["has_tooth_back"])

    cards: dict[str, Any] = {target_id: library[materials[target_id]]}
    for pid in standin_ids:
        cards[f"{STANDIN_PREFIX}{pid}"] = library[materials[pid]]
    cards[TOOTH_HEAD] = library[impactor.material]

    missing = sorted(set(groups) - set(cards))
    if missing:
        raise GeometryError(f"{case}: meshed groups with no material: {', '.join(missing)}")

    metrics = analyse(
        groups,
        cards,
        end_time=end_time,
        # No floor. Mass scaling is what made the Phase 5 runs meaningless, so
        # a case that needs it should be refused by B4, not quietly propped up.
        timestep_min=0.0,
        timestep_scale=timestep_scale,
        mesh_size_min=mesh_size / MIN_SIZE_FRACTION,
    )

    # The guarded mmg3d pass: propose a repaired mesh, keep it only if it is
    # measurably cheaper. Runs in the parent -- mmg is its own subprocess, so
    # the gmsh worker's crash isolation is not needed here. On acceptance the
    # replacement is a sibling file and the gmsh mesh stays for diagnosis.
    mmg_record: dict[str, Any] | None = None
    if mmg_pass:
        proposed = guarded_pass(
            groups,
            cards,
            baseline=metrics,
            end_time=end_time,
            timestep_scale=timestep_scale,
            mesh_floor_mm=mesh_size / MIN_SIZE_FRACTION,
            workdir=work,
            case=case,
        )
        mmg_record = proposed.record()
        if proposed.accepted:
            assert proposed.metrics is not None and proposed.mesh_path is not None
            metrics = proposed.metrics
            mesh_path = proposed.mesh_path

    spec = deck_spec(
        case=case,
        target_id=target_id,
        standin_ids=standin_ids,
        materials=materials,
        library=library,
        tooth=geom,
        approach=approach,
        impactor=impactor,
        end_time=end_time,
        swing=sw,
        rest=rest_of_robot(parts, materials, library, [target_id, *standin_ids]),
        timestep_scale=timestep_scale,
    )
    spec_path = work / f"{case}.yaml"
    spec_path.write_text(yaml.safe_dump(spec, sort_keys=False))

    return BuiltCase(
        case=case,
        target_id=target_id,
        approach=approach,
        tooth=geom,
        radius=radius,
        scoped_ids=tuple([target_id, *standin_ids]),
        mesh_path=mesh_path,
        spec_path=spec_path,
        metrics=metrics,
        warnings=tuple(state.warnings),
        standin_grid=meshed.get("standin_grid"),
        mmg=mmg_record,
        end_time=end_time,
        swing=sw,
        up=up,
        stage_seconds=meshed.get("stage_seconds"),
    )


def _rebuild_approach(raw: Mapping[str, Any]) -> Approach:
    """Reconstruct the strike on the far side of the mesh worker.

    Field by field rather than ``Approach(**raw)``, because the coordinates
    have to come back as tuples. That is also how ``part_id`` and
    ``solid_index`` came to be dropped when they were added to
    :class:`~cra.placement.Approach` for multi-solid targeting: the mesh runs
    in a child process, the two fields never crossed it, and every multi-body
    target was silently meshed whole -- in the sweep as well as in production,
    and invisibly, because a whole part meshes perfectly well.

    Pulled out of the worker so a test can hold it to that, without a solver.
    """
    return Approach(
        direction=(
            float(raw["direction"][0]),
            float(raw["direction"][1]),
            float(raw["direction"][2]),
        ),
        strike_point=(
            float(raw["strike_point"][0]),
            float(raw["strike_point"][1]),
            float(raw["strike_point"][2]),
        ),
        standoff=raw["standoff"],
        reach=raw["reach"],
        exposure=raw["exposure"],
        arbitrary=raw["arbitrary"],
        part_id=raw.get("part_id"),
        solid_index=raw.get("solid_index"),
    )


def _struck_solid(
    gmsh: Any, tags: Sequence[int], part: Mapping[str, Any], solid_index: int | None
) -> int:
    """Which of a multi-body part's gmsh solids the tooth actually hits.

    Only the struck body is meshed. Keeping the siblings puts free rigid
    bodies in the model -- measured at up to 339 mm outside a 202 mm scope
    radius -- which are elements paid for and not used, and which the solver
    will happily accelerate off to infinity.

    **The index is the mapping.** ``docs/history/target-selection-fixes.md``
    warned against matching by ordinal, on the grounds that Tier 0's boxes come from
    ``occshapes.iter_solids`` while the mesh comes from gmsh's own STEP
    import, and the two orderings are "not the same file read twice".
    Measured across every multi-solid target in the corpus -- 2, 3, 3, 5 and 7
    bodies -- the orderings are the identity and the boxes agree to
    0.000000 mm. So the index is used directly.

    It is still *verified* rather than trusted. The check costs one bounding
    box query, and meshing the wrong body would produce a confident report
    about a part the opponent never touched -- the same class of failure as
    aiming into empty space, and just as invisible afterwards.
    """
    if solid_index is None:
        raise LoadCaseError(
            f"{part.get('id')} is built from {len(tags)} bodies but the strike "
            f"does not say which one it lands on, so there is no way to tell "
            f"which to mesh."
        )
    try:
        tag = tags[solid_index]
    except IndexError as exc:
        raise LoadCaseError(
            f"{part.get('id')} has {len(tags)} bodies in the mesh but the "
            f"strike names body {solid_index}."
        ) from exc

    boxes = part.get("solid_bboxes")
    if boxes and solid_index < len(boxes) and boxes[solid_index]:
        want = boxes[solid_index]
        got = gmsh.model.occ.getBoundingBox(3, tag)
        lo = [float(v) for v in want["min"]]
        hi = [float(v) for v in want["max"]]
        drift = max(
            max(abs(lo[k] - got[k]) for k in range(3)),
            max(abs(hi[k] - got[3 + k]) for k in range(3)),
        )
        if drift > SOLID_BOX_TOLERANCE_MM:
            raise LoadCaseError(
                f"{part.get('id')} body {solid_index} sits at a different "
                f"place in the mesh than in the analysis ({drift:.3f} mm "
                f"apart), so the two disagree about which body is which and "
                f"meshing either would be a guess."
            )
    return tag


def _drop_solids(gmsh: Any, drop: Sequence[tuple[int, int]], keep_tags: Sequence[int]) -> None:
    """Delete every solid but the target, cheaply, in the OCC kernel.

    ``occ.remove(drop, recursive=True)`` does the same thing and was 95 s of a
    129 s case build -- 74% of it, against 1.6 s to actually mesh. Recursion
    makes gmsh ask, for each of ~10,000 sub-entities, whether anything still
    references it. We already know what the target owns, so the cascade is
    explicit: solids, then the faces the target does not own, then the edges
    and vertices left dangling. Measured 5.9 s against 95.4 s for an identical
    end state (1 volume, 6 faces, 12 edges).

    It has to happen in the kernel, not the model. ``model.removeEntities`` is
    faster still at 7.6 s but the OCC shapes survive it, and the next
    ``occ.synchronize`` -- which adding the stand-ins and the tooth both
    trigger -- puts all 281 volumes straight back.
    """
    keep_faces = {
        abs(tag) for _, tag in gmsh.model.getBoundary([(3, t) for t in keep_tags], oriented=False)
    }

    gmsh.model.occ.remove(list(drop), recursive=False)
    gmsh.model.occ.synchronize()

    orphan_faces = [(2, tag) for _, tag in gmsh.model.getEntities(2) if tag not in keep_faces]
    if orphan_faces:
        gmsh.model.occ.remove(orphan_faces, recursive=False)
        gmsh.model.occ.synchronize()

    keep_edges = {
        abs(tag)
        for _, tag in gmsh.model.getBoundary(
            [(2, f) for f in keep_faces], oriented=False, combined=False
        )
    }
    orphan_edges = [(1, tag) for _, tag in gmsh.model.getEntities(1) if tag not in keep_edges]
    if orphan_edges:
        gmsh.model.occ.remove(orphan_edges, recursive=False)
    # Vertices still used by a surviving edge are refused, and removal is
    # best-effort here: a stray vertex costs nothing to mesh, unlike a face.
    gmsh.model.occ.remove(gmsh.model.getEntities(0), recursive=False)
    gmsh.model.occ.synchronize()


class _Stages:
    """Elapsed seconds per stage of a case build.

    A build is one opaque number to every caller today, and on the scoped
    corpus it is the larger half of the paid job -- ~170 s against ~15 s of
    solve, and it tracks neither part count, mesh size nor file size. Nothing
    can be tuned or gated that cannot be attributed, so the breakdown travels
    with the result.
    """

    def __init__(self) -> None:
        self.times: dict[str, float] = {}
        self._t0 = time.monotonic()

    def mark(self, name: str) -> None:
        now = time.monotonic()
        self.times[name] = round(now - self._t0, 2)
        self._t0 = now


def mesh_geometry(payload: Mapping[str, Any]) -> dict[str, Any]:
    """The gmsh half of :func:`build_case`, isolated behind a JSON boundary.

    Everything that touches gmsh happens here, so the whole of it can be run
    in a child process — see :mod:`cra.meshworker`. gmsh segfaults on some
    real combat-robot CAD, and in-process that kills the Batch task outright:
    no gate result, no refusal, nothing to tell the user.

    Returns only JSON-safe values; the mesh itself crosses as a file.
    """
    step_path = payload["step_path"]
    parts = list(payload["parts"])
    target_id = str(payload["target_id"])
    wanted = list(payload["wanted"])
    geom = ToothGeometry(**payload["geom"])
    approach = _rebuild_approach(payload["approach"])
    mesh_size = float(payload["mesh_size"])
    mesh_path = Path(payload["mesh_path"])
    # Absent means production. An older payload -- a queued job, a replayed
    # sweep record -- must mesh exactly as it did before this knob existed.
    algo3d = int(payload.get("algo3d") or ALGO3D_FRONTAL)
    threshold = payload.get("optimize_threshold")
    optimize_threshold = None if threshold is None else float(threshold)
    structured_standins = bool(payload.get("structured_standins"))
    # v2: the tooth's width lies along the weapon's spin axis, and the target
    # is meshed fine at the strike and coarser with distance (both optional,
    # so a payload without them meshes as v1 did).
    width_axis = payload.get("width_axis")
    grade = payload.get("grade")

    by_id_full = {str(p["id"]): p for p in parts if p.get("id")}
    warnings: list[str] = []

    stages = _Stages()
    with gmsh_session() as gmsh:
        stages.mark("session")
        solids = import_solids(gmsh, step_path)
        stages.mark("import")
        smap = map_solids(solids, list(parts))
        stages.mark("map_solids")

        # Only the target's real geometry survives. The neighbours come back
        # as stand-in boxes, which is what lets the joint be conformal.
        target_tags = list(smap.tags_by_part.get(target_id, ()))
        if len(target_tags) > 1:
            target_tags = [
                _struck_solid(gmsh, target_tags, by_id_full[target_id], approach.solid_index)
            ]
        keep_tags = set(target_tags)
        drop = [(3, tag) for tag in smap.part_by_tag if tag not in keep_tags]
        if drop:
            _drop_solids(gmsh, drop, target_tags)
        stages.mark("drop_others")

        target_volume = sum(gmsh.model.occ.getMass(3, tag) for tag in target_tags)
        stages.mark("target_volume")
        boxes = _standin_boxes(
            by_id_full,
            wanted,
            body_bbox(by_id_full[target_id], approach.solid_index),
            min_volume=MIN_STANDIN_VOLUME_FRACTION * target_volume,
        )
        try:
            target_tags, standins = _add_standins(
                gmsh, boxes, target_tags, target_volume=target_volume
            )
        except Exception as exc:  # OCC booleans fail on geometry, not on logic
            # Degrading to the target alone beats failing the build.
            warnings.append(
                f"The mounting stand-ins could not be built ({exc}), so the "
                f"panel's far end is tied straight to the rest of the robot's "
                f"rigid mass. Mount loads are not reportable for this case."
            )
            standins = {}
        stages.mark("standins")
        standin_ids = list(standins)
        if wanted and not standin_ids and not warnings:
            warnings.append(
                "No neighbour was close enough to stand in for the mounting, "
                "so the panel's far end is tied straight to the rest of the "
                "robot's rigid mass. Mount loads are not reportable for this case."
            )

        tooth = build_tooth(gmsh, geom, approach, width_axis=width_axis)
        stages.mark("tooth")

        group = gmsh.model.addPhysicalGroup(3, target_tags)
        gmsh.model.setPhysicalName(3, group, target_id)
        for part_id, tags in standins.items():
            name_group(gmsh, tags, f"{STANDIN_PREFIX}{part_id}")
        name_group(gmsh, tooth["head"], TOOTH_HEAD)
        if tooth["back"]:
            name_group(gmsh, tooth["back"], TOOTH_BACK)

        stages.mark("groups")
        standin_tags = [tag for tags in standins.values() for tag in tags]
        coarse_back = max(mesh_size, geom.back_section / 5.0)
        coarse_surround = mesh_size * SURROUND_COARSENING

        # Stand-ins default to one coarse unstructured bucket. Structured,
        # each box that survived the fragment with box topology gets a
        # transfinite grid (no slivers possible on a lattice), and each box
        # the fragment split falls back to a size cap under half its own
        # thickness -- a cell that cannot span the slab cannot build the
        # crossing-edge sliver of mesh-sliver-anatomy.md §2. The cap never
        # goes below `mesh_size`, the target's own resolution: the first
        # subset sweep floored it at the 0.3 mm *floor* instead, and a
        # 0.8 mm-thin plate-sized box meshed at 0.4 mm cost x21-x27 in
        # element-cycles and minted a 0.006 mm artefact. A box thinner than
        # twice the target keeps its spanning-sliver risk rather than
        # trading it for a guaranteed explosion.
        standin_buckets: list[tuple[Sequence[int], float]] = [(standin_tags, coarse_surround)]
        grid_counts: dict[str, int] | None = None
        if structured_standins and standin_tags:
            grid = structure_standins(gmsh, standin_tags, coarse_surround)
            standin_buckets = [(list(grid.structured), coarse_surround)]
            for tag, thin in grid.fallback.items():
                capped = min(coarse_surround, max(thin / 2.0, mesh_size))
                standin_buckets.append(([tag], capped))
            grid_counts = {"structured": len(grid.structured), "fallback": len(grid.fallback)}
            stages.mark("grid")

        # Mesh, then refuse to hand the solver a sliver. A graded mesh whose
        # far size exceeds a thin wall leaves no node inside it, and HXT then
        # joins the wall's two faces with flat tets tens of mm long: on
        # inertial-v6's 3.8 mm lip (far 6 mm) 203 of them, gamma down to
        # 0.0003, which erode at almost no load and blew the solve up to NaN
        # at 8.5 us. So re-mesh until clean, refining only what had slivers:
        # the target's far size halves (far 3 mm was clean there, at 2.3x the
        # elements); a stand-in's size shrinks by STANDIN_REFINE (halving the
        # whole target for 2 stand-in slivers cost 911k elements). Refuse after
        # MESH_ATTEMPTS.
        far = float(grade["far"]) if grade else 0.0
        standin_scale = 1.0
        for attempt in range(MESH_ATTEMPTS):
            graded = None
            if grade:
                graded = {**grade, "volumes": list(target_tags), "near": mesh_size, "far": far}
            _size_fields(
                gmsh,
                [
                    ((list(tooth["head"]) if graded else target_tags + list(tooth["head"])), mesh_size),
                    *((tags, size * standin_scale) for tags, size in standin_buckets),
                    (list(tooth["back"]), coarse_back),
                ],
                graded=graded,
            )
            generate_mesh(
                gmsh,
                mesh_path,
                size=max(coarse_back, coarse_surround, far),
                min_size=mesh_size / MIN_SIZE_FRACTION,
                netgen=not grade,
                algo3d=algo3d,
                optimize_threshold=optimize_threshold,
                # Only used to name the offending part if meshing fails: gmsh's
                # own message identifies a face by an entity tag the submitter has
                # no way to look up.
                solid_map=smap,
            )
            slivers = _slivers(gmsh)
            if not slivers:
                break
            found = ", ".join(f"{n} in {name}" for name, n in sorted(slivers.items()))
            in_target = target_id in slivers
            in_standin = any(name.startswith(STANDIN_PREFIX) for name in slivers)
            can_refine = (in_target and grade and far > mesh_size) or in_standin
            if attempt == MESH_ATTEMPTS - 1 or not can_refine:
                raise GeometryError(
                    f"the mesh has degenerate (flat) elements ({found}) that would make the "
                    "solve blow up; try a smaller mesh_size, or exclude the part")
            if in_target and grade:
                far = max(mesh_size, far / 2.0)
            if in_standin:
                standin_scale *= STANDIN_REFINE
            warnings.append(f"re-meshed to remove degenerate elements ({found})")
            gmsh.model.mesh.clear()
            for f in gmsh.model.mesh.field.list():
                gmsh.model.mesh.field.remove(f)
        stages.mark("mesh")
        groups = _tets_by_group(gmsh)
        stages.mark("tets_by_group")

    return {
        "groups": groups,
        "standin_ids": standin_ids,
        "has_tooth_back": bool(tooth["back"]),
        "standin_grid": grid_counts,
        "warnings": warnings,
        "stage_seconds": stages.times,
    }

