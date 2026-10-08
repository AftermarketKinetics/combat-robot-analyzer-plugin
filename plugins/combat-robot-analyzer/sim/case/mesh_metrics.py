"""B4's admission arithmetic: how long will this mesh take, and is it valid?

Two numbers decide whether a paid job runs.

**Element-cycles** is the cost. ``build_deck.py`` reports its own
``cycles_for_end_time``, but that estimate ignores ``timestep_min`` entirely —
on ``meowtybrain`` it predicted 1,277,892 cycles where the run did 10,001,
because mass scaling had lifted every element to the floor. Real assemblies sit
far below any floor worth setting (backing out Phase 5's meowtybrain deck gives
a shortest edge of 0.44 um), so in practice ``cycles = end_time / timestep_min``
and the cost is driven by our knobs, not the user's geometry.

**Predicted added mass** is a fidelity check, but a narrower one than it first
appears, and the limit is measured rather than assumed.

Phase 5's two bad runs were expected to fail this check. They do not, and they
should not. Validating against the corpus meshes gives:

===========  ==============  ===============
model        measured        predicted here
===========  ==============  ===============
inertial-v6  100% (killed)   0.00%
meowtybrain  0.00%           0.00%
seraphs-v1   65%             0.03%
===========  ==============  ===============

The agreement on the one *stable* run is exact; the two disagreements are the
interesting part. Reading the engine output shows why: ``inertial-v6`` ends
with ``RUN KILLED: TOTAL MASS ERROR LIMIT REACHED`` after its clock has
diverged to t = 7.2e35 s, and ``seraphs-v1`` reaches 99.9% mass error by cycle
100 of a 10,000-cycle run. Neither started bad. They *became* bad, because
elements deform under load until their characteristic length collapses and
``/DT/NODA/CST`` keeps buying timestep with mass.

So added mass at t = 0 is a **necessary but not sufficient** condition. It
catches a mesh that is already too fine to afford, which is worth catching
cheaply. It cannot predict runtime runaway, and B4 must not claim otherwise —
runtime quality stays B6's responsibility, after the solve.

Element-cycles depends only on the mesh and the control block, both known
before the solver starts — but for three weeks it depended on the *wrong*
property of the mesh. The timestep was estimated from each tet's shortest
edge, and the solver's goes with volume over largest face area, so a sliver
with an ordinary shortest edge was costed as though it were a healthy element.
Measured over the corpus, that over-predicted the timestep by a median of 2.3x
and under-charged the resulting cycle count by the same factor, on the gate
that decides whether a user is billed. :func:`tet_geometry` carries what the
right length is and :data:`K_TET` how it was established; the residual after
the fix is contact and nodal stiffness, which no amount of geometry predicts
and which B6 catches after the fact.

Deliberately stdlib-only. numpy's manylinux wheel will not load on the NixOS
system python, so importing it would make this module untestable outside the
batch image — unacceptable for the gate that decides whether a user is
charged. Node and element data comes straight out of the live gmsh session, so
there is no mesh file to parse and no meshio dependency either.
"""

from __future__ import annotations

import bisect
import heapq
import math
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

__all__ = [
    "CHAR_QUANTILES",
    "DEFAULT_POISSON",
    "K_TET",
    "MeshMetrics",
    "PartMetrics",
    "analyse",
    "dilatational_wave_speed",
    "poisson_ratio",
    "tet_geometry",
    "wave_speed",
]

# Radioss's own default; ``control.timestep_scale`` overrides it.
DEFAULT_TIMESTEP_SCALE = 0.9

#: The tetrahedron's characteristic length is ``K_TET * volume / largest face
#: area``, and the stable timestep is ``scale * that / dilatational wave
#: speed``.
#:
#: **Measured against the solver, not derived.** The starter prints a
#: ``SOLID ELEMENTS TIME STEP`` table -- the smallest element timesteps and the
#: element controlling each -- so ``dt * c / (scale * V/A_max)`` can be
#: evaluated per element against the solver's own answer. Over every element of
#: every corpus case it is this constant to six figures, with a spread of
#: 1.000000. ``cra.calibrate.timestep_fit`` is that check, and it runs in the
#: test venv because it reads decks and starter output rather than meshing
#: anything.
#:
#: What it is in closed form is not identified, and nothing here depends on
#: knowing. What *is* established is the shape: volume over largest face area,
#: and the dilatational speed rather than the bar speed -- the constant only
#: collapses to a single value when both are right. Using the bar speed leaves
#: it tracking Poisson's ratio, which is how the wave speed was identified.
K_TET = 3.170831

#: Poisson's ratio when a material card carries neither ``poisson`` nor
#: ``nu12``. Only the five composite entries lack both, and 0.3 is what their
#: matrix resin would have. It is also the conservative direction: a higher
#: ratio means a faster dilatational wave, a smaller timestep and more cycles,
#: so an unknown material is costed as slightly harder rather than cheaper.
DEFAULT_POISSON = 0.3

#: Quantiles of the characteristic length kept alongside the minimum, as
#: ``char_p001_mm`` / ``char_p01_mm`` / ``char_p50_mm`` -- the digits after the
#: ``p`` are the quantile with its leading ``0.`` stripped.
#:
#: **The minimum alone cannot tell an artefact from a fine part, and the whole
#: price rides on which it is.** Measured 2026-09-19 over 34 freshly meshed
#: cases: the median case has p50 = 1.28 mm -- the 1.5 mm request, correctly
#: met, since a regular tet's characteristic length is 0.86x its edge --
#: p1 = 0.62 mm, and a *minimum* of 0.127 mm. The smallest element anywhere in
#: the corpus is 0.00105 mm. 33 of 34 go below the ``min_size`` floor gmsh was
#: handed, which is the documented behaviour of the option rather than a
#: defect: it clamps the target size field, and the Frontal path is never
#: shown it at all. So the offending elements are a tail of roughly 1 in
#: 2,000, not a part that genuinely resolves that fine -- and since cost goes
#: as the reciprocal of the minimum, that tail sets the price by a median
#: 2.4x against a floor that held.
#:
#: **An earlier version of this note said six cases reached 6e-7 mm.** That
#: was measured on meshes retained from 2026-08-21 and was already dead when
#: it was written: ``8dfea71`` snapped the stand-in boxes to a micron the day
#: before, and those sub-nanometre tets were its bounding-box noise. The shape
#: of the finding survived the correction; the extreme tail did not. See
#: ``docs/history/mesh-sliver-anatomy.md`` for what the tail is made of now.
#:
#: 0.001 is the finest quantile worth keeping: below 1,000 elements it *is*
#: the minimum and says nothing.
CHAR_QUANTILES = (0.001, 0.01, 0.5)

# The six node pairs of a linear tetrahedron.
_TET_EDGES = ((0, 1), (0, 2), (0, 3), (1, 2), (1, 3), (2, 3))

# The four node triples bounding a linear tetrahedron.
_TET_FACES = ((0, 1, 2), (0, 1, 3), (0, 2, 3), (1, 2, 3))


def wave_speed(material: Mapping[str, Any]) -> float:
    """Acoustic wave speed for timestep estimation.

    Mirrors ``build_deck.wave_speed`` for the laws combat robot structures
    actually use. Reimplemented rather than imported because that module pulls
    in numpy and meshio at import time, which would drag the whole batch-image
    dependency set into a calculation that has to run in the test venv.
    ``tests/test_mesh_metrics.py`` pins the agreement.
    """
    rho = float(material.get("density") or 0.0)
    if rho <= 0:
        return 0.0

    law = str(material.get("law", "johnson_cook")).lower()

    if law in ("ogden", "law42", "mooney_rivlin", "mooney"):
        bulk = material.get("bulk_modulus")
        if bulk is None:
            nu = float(material.get("poisson", 0.495))
            if law in ("mooney_rivlin", "mooney"):
                mus, alphas = [float(material["c10"]), -float(material["c01"])], [2.0, -2.0]
            else:
                mus = [float(x) for x in material.get("mu", [0])]
                alphas = [float(x) for x in material.get("alpha", [1])]
            mu0 = sum(u * a for u, a in zip(mus, alphas, strict=False)) / 2
            if abs(1 - 2 * nu) < 1e-12:
                nu = 0.499
            bulk = 2 * abs(mu0) * (1 + nu) / (3 * (1 - 2 * nu))
        bulk = float(bulk)
        return math.sqrt(bulk / rho) if bulk > 0 else 0.0

    if law in ("compsh", "law25", "composite", "fabric", "fabri", "law19"):
        e_max = max(float(material.get("e11", 0)), float(material.get("e22", 0)))
        return math.sqrt(e_max / rho) if e_max > 0 else 0.0

    young = float(material.get("young", 0))
    return math.sqrt(young / rho) if young > 0 else 0.0


def poisson_ratio(material: Mapping[str, Any]) -> float:
    """Poisson's ratio, from ``poisson``, then ``nu12``, then the default.

    The composite laws spell it ``nu12``; everything else spells it
    ``poisson``. Clamped below 0.5 because the dilatational speed diverges
    there, and a near-incompressible card would otherwise price a job at
    infinite cost rather than refusing it for a reason anyone can read.
    """
    for key in ("poisson", "nu12"):
        value = material.get(key)
        if value is not None:
            nu = float(value)
            break
    else:
        nu = DEFAULT_POISSON
    return min(max(nu, 0.0), 0.499)


def dilatational_wave_speed(material: Mapping[str, Any]) -> float:
    """The speed the solver's timestep actually goes with.

    ``wave_speed`` is the bar speed, ``sqrt(E/rho)``, which is what
    ``build_deck`` reports and what a one-dimensional rod would ring at. A
    solid element is not a rod: it is laterally constrained, so the wave that
    sets its stable timestep travels at the dilatational speed, faster by
    ``sqrt((1-nu) / ((1+nu)(1-2nu)))`` -- 14% for steel, and more as a material
    approaches incompressible.

    Distinguishing them is not a refinement. Fitting the solver's own element
    timesteps with the bar speed leaves a constant that still moves with
    Poisson's ratio, 2.7699 at nu=0.29 against 2.7329 at nu=0.30; with this one
    it collapses to :data:`K_TET` for every material in the corpus.
    """
    c = wave_speed(material)
    if c <= 0:
        return 0.0
    nu = poisson_ratio(material)
    return c * math.sqrt((1.0 - nu) / ((1.0 + nu) * (1.0 - 2.0 * nu)))


def tet_geometry(
    coords: Sequence[tuple[float, float, float]],
) -> tuple[float, float, float]:
    """``(shortest edge, volume, characteristic length)`` of one linear tet.

    All three in one pass: a corpus load case carries up to 2.4 million tets,
    and the volume is wanted for mass as well as for the length.

    **The characteristic length is not the shortest edge**, which is what this
    module used to assume and what ``build_deck`` still reports. It is
    :data:`K_TET` times volume over largest face area -- the tet's smallest
    altitude, up to the constant. The two agree to within a factor of 1.2 on a
    well-shaped tet and diverge without limit on a sliver, whose shortest edge
    stays perfectly ordinary while the height it can be squashed through goes
    to nothing. Over the corpus the old assumption over-predicted the timestep
    by a median of 2.3x and a worst case of 54x, and the cost estimate that
    B4 charges people on is the timestep's reciprocal.

    The docstring this replaces claimed the shortest edge "can only
    under-predict the timestep, never over-predict it". It is the other way
    round, and it was never checked against a solver that prints the answer.
    """
    shortest = math.inf
    for a, b in _TET_EDGES:
        d = math.dist(coords[a], coords[b])
        if 0.0 < d < shortest:
            shortest = d

    (x0, y0, z0), (x1, y1, z1), (x2, y2, z2), (x3, y3, z3) = coords
    ax, ay, az = x1 - x0, y1 - y0, z1 - z0
    bx, by, bz = x2 - x0, y2 - y0, z2 - z0
    cx, cy, cz = x3 - x0, y3 - y0, z3 - z0
    det = ax * (by * cz - bz * cy) - ay * (bx * cz - bz * cx) + az * (bx * cy - by * cx)
    volume = abs(det) / 6.0

    largest_face = 0.0
    for i, j, k in _TET_FACES:
        (px, py, pz), (qx, qy, qz), (rx, ry, rz) = coords[i], coords[j], coords[k]
        ux, uy, uz = qx - px, qy - py, qz - pz
        vx, vy, vz = rx - px, ry - py, rz - pz
        nx, ny, nz = uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx
        area = 0.5 * math.sqrt(nx * nx + ny * ny + nz * nz)
        largest_face = max(largest_face, area)

    char = K_TET * volume / largest_face if largest_face > 0 else 0.0
    return shortest, volume, char


def _quantiles(ascending: Sequence[float]) -> tuple[float, ...]:
    """:data:`CHAR_QUANTILES` of an already-sorted sequence, nearest-rank.

    No interpolation, deliberately. The question these answer is "how small is
    the one-in-a-thousand element", and an interpolated value between two real
    elements is not an element. ``statistics.quantiles`` interpolates, needs at
    least two points, and is slower on the 2.4 million tets a corpus case can
    carry.

    Returns a tuple of infinities for an empty sequence, matching what
    ``min_char_mm`` carries for a part with no usable tets.
    """
    n = len(ascending)
    if not n:
        return tuple(math.inf for _ in CHAR_QUANTILES)
    return tuple(ascending[min(n - 1, max(0, int(q * (n - 1))))] for q in CHAR_QUANTILES)


@dataclass(frozen=True)
class PartMetrics:
    """Per-part rollup. ``added_mass`` is what makes a refusal actionable."""

    part_id: str
    elements: int
    #: Shortest edge in the part. Kept because it is what B4's refusal text
    #: names and what a user can act on -- "this fillet is 0.4 mm" is a fact
    #: about their CAD, where a characteristic length is a fact about our
    #: mesher. It no longer sets the timestep.
    min_edge_mm: float
    #: Smallest ``K_TET * V / A_max`` in the part. This is what the timestep
    #: goes with.
    min_char_mm: float
    wave_speed: float
    mass: float
    added_mass: float
    #: :data:`CHAR_QUANTILES` of the part's characteristic lengths. ``inf``
    #: means not measured -- the gate fixtures construct these by hand.
    char_p001_mm: float = math.inf
    char_p01_mm: float = math.inf
    char_p50_mm: float = math.inf
    #: Elements below the ``min_size`` floor handed to gmsh, or 0 when
    #: ``analyse`` was given no floor to compare against.
    below_mesh_min: int = 0

    @property
    def dt_physical(self) -> float:
        if self.wave_speed <= 0 or not math.isfinite(self.min_char_mm):
            return math.inf
        return self.min_char_mm / self.wave_speed


@dataclass(frozen=True)
class MeshMetrics:
    """Everything B4 needs to accept or refuse a load case."""

    elements: int
    dt_physical: float
    dt_effective: float
    cycles: int
    element_cycles: float
    mass_total: float
    mass_added: float
    per_part: tuple[PartMetrics, ...]
    #: :data:`CHAR_QUANTILES` over every part's elements together, and the
    #: count below the floor gmsh was handed. ``dt_physical`` goes with the
    #: minimum; these say whether that minimum is one element or the mesh.
    char_p001_mm: float = math.inf
    char_p01_mm: float = math.inf
    char_p50_mm: float = math.inf
    below_mesh_min: int = 0
    #: Parts whose material gave no usable density or wave speed, so they
    #: contribute neither mass nor added mass. B4 must treat a non-empty tuple
    #: as a hard failure rather than trusting an added-mass fraction computed
    #: over only part of the model.
    unpriced_parts: tuple[str, ...] = ()

    @property
    def added_mass_fraction(self) -> float:
        if self.mass_total <= 0:
            return 0.0
        return self.mass_added / self.mass_total

    @property
    def mass_scaling_active(self) -> bool:
        """True when the floor, not the geometry, is setting the timestep."""
        return self.dt_effective > self.dt_physical

    def worst_added_mass(self, limit: int = 3) -> tuple[PartMetrics, ...]:
        """The parts driving added mass, worst first — for the refusal text."""
        ranked = sorted(self.per_part, key=lambda p: p.added_mass, reverse=True)
        return tuple(p for p in ranked[:limit] if p.added_mass > 0)


def analyse(
    parts: Mapping[str, Iterable[Sequence[tuple[float, float, float]]]],
    materials: Mapping[str, Mapping[str, Any]],
    *,
    end_time: float,
    timestep_min: float,
    timestep_scale: float = DEFAULT_TIMESTEP_SCALE,
    mesh_size_min: float = 0.0,
) -> MeshMetrics:
    """Estimate cost and added mass for one meshed load case.

    ``parts`` maps part id to that part's tets, each a sequence of four
    ``(x, y, z)`` node coordinates. ``materials`` maps part id to the raw
    material body from ``materials.yaml``.

    The mass-scaling model: an element runs at ``scale * L/c``. Where that is
    below the floor, Radioss inflates the element's density until it reaches
    the floor. Since ``dt`` scales as ``sqrt(rho)``, the required mass ratio is
    ``(dt_floor / (scale * L/c))**2``.

    ``mesh_size_min`` is the ``min_size`` the mesher was told to respect, and
    is used for nothing but counting the elements that came out below it. Pass
    0 to skip the count. It is a diagnostic, never an input to the price: the
    timestep goes with the smallest element that actually exists, whatever we
    asked for. See :data:`CHAR_QUANTILES` for what the corpus says about how
    often the two disagree.
    """
    if end_time <= 0:
        raise ValueError("end_time must be positive")

    rollups: list[PartMetrics] = []
    total_elements = 0
    dt_physical = math.inf

    # First pass: per-element geometry, and the physical timestep it implies.
    measured: list[
        tuple[str, list[tuple[float, float, float]], float, float, float, float, list[float]]
    ] = []
    for part_id, tets in parts.items():
        material = materials.get(part_id) or {}
        c = dilatational_wave_speed(material)
        rho = float(material.get("density") or 0.0)

        per_element: list[tuple[float, float, float]] = []
        part_min_edge = math.inf
        part_chars: list[float] = []
        for coords in tets:
            edge, volume, char = tet_geometry(coords)
            if not math.isfinite(edge) or volume <= 0 or char <= 0:
                continue
            per_element.append((edge, volume, char))
            part_min_edge = min(part_min_edge, edge)
            part_chars.append(char)

        # Sorted once here and reused for the quantiles, the below-floor count
        # and the minimum, so the extra pass over 2.4 million tets a corpus
        # case can carry is one sort rather than three scans.
        part_chars.sort()
        part_min_char = part_chars[0] if part_chars else math.inf

        total_elements += len(per_element)
        measured.append((part_id, per_element, c, rho, part_min_edge, part_min_char, part_chars))
        if c > 0 and math.isfinite(part_min_char):
            dt_physical = min(dt_physical, part_min_char / c)

    if not total_elements:
        raise ValueError("the mesh contains no usable tetrahedra")

    # The timestep the solver will actually run at.
    scaled = timestep_scale * dt_physical if math.isfinite(dt_physical) else math.inf
    dt_effective = max(scaled, timestep_min) if timestep_min > 0 else scaled
    if not math.isfinite(dt_effective) or dt_effective <= 0:
        raise ValueError("no element yielded a usable timestep; check the materials")

    # Second pass: how much mass the floor has to add to get there.
    mass_total = 0.0
    mass_added = 0.0
    for part_id, per_element, c, rho, part_min_edge, part_min_char, part_chars in measured:
        part_mass = 0.0
        part_added = 0.0
        for _edge, volume, char in per_element:
            m = rho * volume
            part_mass += m
            if c <= 0:
                continue
            dt_element = timestep_scale * char / c
            if dt_element < dt_effective:
                ratio = (dt_effective / dt_element) ** 2
                part_added += m * (ratio - 1.0)

        mass_total += part_mass
        mass_added += part_added
        p001, p01, p50 = _quantiles(part_chars)
        rollups.append(
            PartMetrics(
                part_id=part_id,
                elements=len(per_element),
                min_edge_mm=part_min_edge,
                min_char_mm=part_min_char,
                wave_speed=c,
                mass=part_mass,
                added_mass=part_added,
                char_p001_mm=p001,
                char_p01_mm=p01,
                char_p50_mm=p50,
                below_mesh_min=(
                    bisect.bisect_left(part_chars, mesh_size_min) if mesh_size_min > 0 else 0
                ),
            )
        )

    # Merged rather than re-sorted: the per-part lists are already ascending,
    # and ``heapq.merge`` is O(n log parts) against the O(n log n) of sorting
    # the concatenation again.
    all_chars = list(heapq.merge(*(m[6] for m in measured)))
    all_p001, all_p01, all_p50 = _quantiles(all_chars)

    cycles = math.ceil(end_time / dt_effective)
    return MeshMetrics(
        elements=total_elements,
        dt_physical=dt_physical,
        dt_effective=dt_effective,
        cycles=cycles,
        element_cycles=float(total_elements) * cycles,
        mass_total=mass_total,
        mass_added=mass_added,
        per_part=tuple(rollups),
        char_p001_mm=all_p001,
        char_p01_mm=all_p01,
        char_p50_mm=all_p50,
        below_mesh_min=(bisect.bisect_left(all_chars, mesh_size_min) if mesh_size_min > 0 else 0),
        unpriced_parts=tuple(p.part_id for p in rollups if p.wave_speed <= 0 or p.mass <= 0),
    )
