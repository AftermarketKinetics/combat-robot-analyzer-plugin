"""Geometry preparation for Tier 1: one gmsh session, opaque part ids.

Tier 1 does **not** use the plugin's ``mesh_step.py``. Three things have to
happen in a single gmsh session and that script can do none of them:

* **The tooth has to be inserted.** The opponent impactor is not in the user's
  STEP file; it is generated here and meshed alongside the target.
* **The model has to be scoped.** A whole-bot mesh does not fit the element
  budget (see ``phase6`` notes), so only the role-tagged target parts survive.
* **Part identity has to survive.** ``mesh_step.py`` names each physical group
  from the STEP product name, which is user-authored text. Those names reach
  ``agent/`` and the Radioss starter's stderr, breaking PLAN.md §5's "the
  string never enters the context window". They are also not unique — on
  ``meowtybrain`` 415 groups collapse to 306 distinct names and
  ``build_deck.load_parts`` keys parts by name, so a quarter of the model
  silently loses its material assignment.

Writing a combined STEP and re-meshing it would be worse, not better: OCC's
STEP writer drops product names, so the mapping back to Tier 0 ids would have
to be rebuilt from scratch anyway.

**The mapping is ordinal, not geometric.** ``cad-step`` enumerates product
occurrences in entity-id order and gmsh imports solids in the same order, so
part *i* owns the next ``parts[i]["solids"]`` solids. Verified on all three
corpus models: summed solid volumes agree with the Tier 0 part volume to
better than 1e-5 relative. Volume agreement is checked rather than assumed —
a silent mis-map would put the wrong material on the armour and produce a
report that is confidently wrong.
"""

from __future__ import annotations

import math
import re
import resource
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

__all__ = [
    "ALGO3D",
    "GeometryError",
    "SolidMap",
    "apply_part_groups",
    "build_tooth",
    "generate_mesh",
    "gmsh_session",
    "head_solids",
    "import_solids",
    "map_solids",
    "name_group",
]

# gmsh's frontal 3D algorithm. Delaunay throws PLC errors on multi-body combat
# robot assemblies, where interfering surfaces are the norm rather than a
# defect; frontal tolerates them. Measured in Phase 5 on all three corpus
# models.
ALGO3D_FRONTAL = 4

# The two alternatives, named so a calibration run can select one without
# passing a bare integer through three layers. Neither is production.
#
# Delaunay has been measured clean once, on inertial-v6's weapon bar: same
# 0.198582 mm floor as optimised frontal, 814,082 elements against 826,689,
# and faster. What ruled it out was PLC errors on multi-body assemblies, which
# is a different failure from sliver quality and was never re-tested after
# scoping cut the mesh down to one panel plus the tooth.
ALGO3D_DELAUNAY = 1
# HXT is Delaunay-based and parallel. ``docs/history/meshing-and-solving-issues.md``
# tested it only on the seven models that crash the mesher outright, where it
# turned 6 of 7 SIGSEGVs into a catchable ``HXT 3D mesh failed``. Its behaviour
# on a model that meshes fine is unmeasured.
ALGO3D_HXT = 10

#: Selectable by name, for ``cra.calibrate.mesh_algo``. ``mesh_geometry``
#: takes the integer, but nothing readable should carry ``4`` around.
ALGO3D = {
    "frontal": ALGO3D_FRONTAL,
    "delaunay": ALGO3D_DELAUNAY,
    "hxt": ALGO3D_HXT,
}

# Relative volume agreement required between a Tier 0 part and the solids
# assigned to it. Across the corpus's 491 parts only two exceed 1e-5 and the
# worst is 6.0e-5, so this leaves ~17x headroom for tessellation drift. It is
# still two orders tighter than any realistic mis-map, which pairs a part with
# entirely different bodies and lands orders of magnitude out, not 0.1%.
VOLUME_TOLERANCE = 1e-3

# Below this a part is not a body. Assemblies carry construction occurrences
# that own no solid and whose volume is float noise around zero — meowtybrain
# has four, one declaring -1.07e-09 mm^3. A 0.01 mm cube is 1e-6 mm^3, so
# nothing real sits under this floor.
NEGLIGIBLE_VOLUME_MM3 = 1e-6


class GeometryError(RuntimeError):
    """Geometry could not be prepared — always a hard gate failure."""


@dataclass(frozen=True)
class SolidMap:
    """Which gmsh solids belong to which Tier 0 part."""

    tags_by_part: dict[str, list[int]]
    part_by_tag: dict[int, str]
    residuals: dict[str, float] = field(default_factory=dict)
    #: Parts whose Tier 0 volume was unavailable, so the mapping could not be
    #: checked against anything. The ordinal structure still held. B2 already
    #: warns about unmeasured parts; B4 repeats the warning for these.
    unverified: tuple[str, ...] = ()

    @property
    def worst_residual(self) -> float:
        return max(self.residuals.values(), default=0.0)

    @property
    def part_count(self) -> int:
        return len(self.tags_by_part)

    @property
    def solid_count(self) -> int:
        return len(self.part_by_tag)


def map_solids(
    solids: Sequence[tuple[int, float]],
    parts: Sequence[dict[str, Any]],
    *,
    tolerance: float = VOLUME_TOLERANCE,
) -> SolidMap:
    """Assign gmsh solid tags to Tier 0 part ids.

    ``solids`` is ``(tag, volume)`` in gmsh import order; ``parts`` is the
    Tier 0 ``parts`` array, which carries ``id``, ``solids`` (a count) and,
    for multi-body parts, ``solid_volumes``.

    Raises :class:`GeometryError` if the counts or the volumes disagree. That
    is deliberate: an unmatched or ambiguous solid means the wrong material on
    a load-bearing part, which is worse than refusing the job.
    """
    expected = sum(int(p.get("solids") or 0) for p in parts)
    if expected != len(solids):
        raise GeometryError(
            f"the STEP file yielded {len(solids)} solids but the Tier 0 "
            f"document accounts for {expected}; the geometry and the analysis "
            "disagree and no part assignment can be trusted"
        )

    tags_by_part: dict[str, list[int]] = {}
    part_by_tag: dict[int, str] = {}
    residuals: dict[str, float] = {}
    unverified: list[str] = []

    cursor = 0
    for part in parts:
        part_id = str(part.get("id") or "")
        if not part_id:
            raise GeometryError("a Tier 0 part has no id; the document is malformed")
        count = int(part.get("solids") or 0)
        chunk = solids[cursor : cursor + count]
        cursor += count

        tags = [tag for tag, _ in chunk]
        tags_by_part[part_id] = tags
        for tag in tags:
            part_by_tag[tag] = part_id

        # Per solid where Tier 0 recorded it. The part's own volume_mm3 is the
        # *union* of its bodies, so for anything whose bodies overlap -- a pin
        # seated into a package, a boss into a plate -- the sum of the solids
        # legitimately exceeds it, and checking one against the other reports
        # a mismatch that is not one. Measured on a SOT-23-5 package: 5.582057
        # union against 5.600437 summed, 0.33% apart against a 0.1%
        # tolerance, which refused two corpus models outright while blaming an
        # ordering that was provably correct.
        #
        # Comparing each solid against its counterpart is also strictly
        # stronger than the sum it replaces: a sum can hide two errors that
        # cancel.
        per_solid = part.get("solid_volumes")
        if per_solid and len(per_solid) == count:
            worst = 0.0
            for (_, measured), claimed in zip(chunk, per_solid, strict=True):
                want = abs(float(claimed))
                if want <= NEGLIGIBLE_VOLUME_MM3:
                    continue
                worst = max(worst, abs(measured - want) / want)
            residuals[part_id] = worst
            if worst > tolerance:
                raise GeometryError(
                    f"part {part_id}'s solids do not match the Tier 0 "
                    f"enumeration: worst body is {worst:.2%} off. The mesh and "
                    f"the analysis disagree about which body is which."
                )
            continue

        raw = part.get("volume_mm3")
        actual = sum(volume for _, volume in chunk)

        # cad-step could not measure this part, so there is nothing to check
        # it against. The ordinal structure still has to hold, and it did.
        if raw is None:
            unverified.append(part_id)
            continue

        declared = float(raw)

        # A bodiless occurrence that also owns no solid is consistent, and its
        # near-zero volume would otherwise divide into a meaningless residual.
        if not tags and abs(declared) <= NEGLIGIBLE_VOLUME_MM3:
            residuals[part_id] = 0.0
            continue

        # Volume but no solid is a different fault entirely, and blaming the
        # ordering for it sends the reader looking in the wrong place. Two
        # corpus models carry one -- Subdivide P Mk 1.3 v25 p139 at 140 mm^3
        # and Yeetus V3 Final Assm p008 at 49.5 -- most likely an open shell,
        # which has a measurable volume and nothing a mesher can fill.
        if not tags:
            raise GeometryError(
                f"part {part_id} measures {declared:.6g} mm^3 but contributes no "
                f"solid body to the mesh. A shape can have a volume and still "
                f"not be solid -- an open shell is the usual cause -- and there "
                f"is nothing here to mesh or to assign a material to."
            )

        residual = abs(actual - declared) / max(abs(declared), NEGLIGIBLE_VOLUME_MM3)
        residuals[part_id] = residual
        if residual > tolerance:
            raise GeometryError(
                f"part {part_id} claims {declared:.6g} mm^3 but its {count} "
                f"solid(s) measure {actual:.6g} mm^3 ({residual:.2%} off); the "
                "solid ordering does not match the Tier 0 enumeration"
            )

    return SolidMap(
        tags_by_part=tags_by_part,
        part_by_tag=part_by_tag,
        residuals=residuals,
        unverified=tuple(unverified),
    )


# --- the gmsh session -----------------------------------------------------
#
# gmsh is a process-global singleton, so the session is a context manager and
# the steps inside it are plain functions taking the module. That lets B4 do
# import -> map -> scope -> insert the tooth -> name -> mesh in one session,
# with no STEP round trip in the middle.


@contextmanager
def gmsh_session(*, verbose: bool = False) -> Iterator[Any]:
    """Own a gmsh session for the duration of the block.

    Imported lazily: gmsh ships in the Batch image only, and the Lambda path
    and most of the test suite never touch it.
    """
    try:
        import gmsh
    except ImportError as exc:  # pragma: no cover - depends on the install extra
        raise GeometryError("gmsh is not installed; Tier 1 needs the batch image") from exc

    # gmsh.initialize() raises RLIMIT_STACK to unlimited process-wide for its
    # own recursive meshing and never puts it back. That leaks into every
    # child: glibc reads RLIMIT_STACK to size the default pthread stack, and
    # reads "unlimited" as 2 MB rather than the 8 MB a normal limit gives. The
    # OpenRadioss engine's OpenMP workers need more than 2 MB and segfault
    # during startup, which is how a solver crash gets blamed on thread count.
    # Measured in the batch image: limit 8 MB -> 8.00 MB stacks, unlimited ->
    # 2.00 MB. run_sim.sh sets OMP_STACKSIZE as well; both are wanted, since
    # this restores the limit for children that are not the solver.
    stack_limit = resource.getrlimit(resource.RLIMIT_STACK)
    gmsh.initialize()
    try:
        # netgen and tetgen write PLC diagnostics straight to stdout, which is
        # why nothing here may parse a subprocess's stdout as JSON.
        gmsh.option.setNumber("General.Terminal", 1 if verbose else 0)
        yield gmsh
    finally:
        gmsh.finalize()
        resource.setrlimit(resource.RLIMIT_STACK, stack_limit)


def import_solids(gmsh: Any, step_path: str | Path) -> list[tuple[int, float]]:
    """Import a STEP file and return ``(tag, volume)`` in import order.

    The order is the contract :func:`map_solids` relies on.
    """
    path = Path(step_path)
    if not path.is_file():
        raise GeometryError(f"no such STEP file: {path}")

    gmsh.model.occ.importShapes(str(path))
    gmsh.model.occ.synchronize()
    solids = [(tag, float(gmsh.model.occ.getMass(3, tag))) for _, tag in gmsh.model.getEntities(3)]
    if not solids:
        raise GeometryError(f"{path.name} contains no solid bodies to mesh")
    return solids


def apply_part_groups(gmsh: Any, solid_map: SolidMap, keep: Sequence[str] | None = None) -> int:
    """Create one physical group per Tier 0 part, named with the opaque id.

    One group *per part* rather than per solid, because ``assignments.json`` is
    keyed on part ids — that is the granularity at which the user chose a
    material. ``keep`` narrows the model to the named parts; everything else is
    left ungrouped and so is never written to the mesh.

    Returns the number of groups created.
    """
    wanted = set(keep) if keep is not None else None
    created = 0
    for part_id, tags in solid_map.tags_by_part.items():
        if not tags or (wanted is not None and part_id not in wanted):
            continue
        group = gmsh.model.addPhysicalGroup(3, tags)
        gmsh.model.setPhysicalName(3, group, part_id)
        created += 1

    if created == 0:
        raise GeometryError(
            "no parts survived scoping; the load case has nothing to mesh"
            + (f" (wanted {sorted(wanted)})" if wanted else "")
        )
    return created


def head_solids(occ: Any, w: float, t: float, length: float, land: float) -> list[int]:
    """The striking head: a trapezoidal prism with a flat land at the tip.

    Public, and separate from :func:`build_tooth`, so that
    ``cra.calibrate.tooth_shapes`` can measure *this* rather than a copy of
    it. A harness that re-implements the thing it checks passes long after the
    original has moved on -- which is how the construction below stayed wrong
    through two rounds of measurement.
    """
    if not 0.0 <= land < t:
        raise GeometryError(
            f"the striking land is {land:.4f} mm on a head {t:.4f} mm thick; it has to "
            f"be narrower than the head, and not negative"
        )
    theta = math.atan2((t - land) / 2.0, length)
    big = 4.0 * max(w, t, length)
    head = occ.addBox(-w / 2.0, -t / 2.0, 0.0, w, t, length)

    upper = occ.addBox(-w, 0.0, -big, 2.0 * w, big, 2.0 * big)
    occ.rotate([(3, upper)], 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, -theta)
    occ.translate([(3, upper)], 0.0, land / 2.0, 0.0)
    lower = occ.addBox(-w, -big, -big, 2.0 * w, big, 2.0 * big)
    occ.rotate([(3, lower)], 0.0, 0.0, 0.0, 1.0, 0.0, 0.0, theta)
    occ.translate([(3, lower)], 0.0, -land / 2.0, 0.0)

    out, _ = occ.cut([(3, head)], [(3, upper), (3, lower)])
    return [tag for dim, tag in out if dim == 3]


def build_tooth(gmsh: Any, geom: Any, approach: Any, width_axis: Any = None) -> dict[str, list[int]]:
    """Add the impactor to the model, aimed along the approach direction.

    Built at the origin pointing down -Z (striking land lowest), then rotated
    so -Z lands on the approach direction and translated so the land sits one
    standoff short of the strike point. Returns the solid tags for the head and
    the backing separately, because they carry different materials and only the
    head belongs in the contact definition's leading role.

    The head is cut from a box rather than built with ``addWedge`` so the
    land's orientation is explicit rather than depending on that primitive's
    internal convention.

    **The land is centred on the tooth's axis**, which is the axis the
    placement below aims by, so what strikes lands where the user pointed.
    ``cra.calibrate.tooth_shapes`` measures this construction against that
    promise and against ``tooth.tooth_geometry``'s volume, on every run.
    """
    occ = gmsh.model.occ
    w, t, length = geom.head_width, geom.head_thickness, geom.head_length
    land = geom.land_width

    # Head: a box spanning the full thickness, with two flanks cut away to
    # leave a flat **land** of width ``land`` along the -Z face.
    #
    # The cutter's *face*, not its body, is what defines a flank, so the face
    # has to contain the line the land's corner lies on. A box whose y = 0
    # face sits on the origin, rotated about the width axis *through that
    # origin*, keeps it there -- rotating the half-space y >= 0 by -theta
    # gives y >= z tan(theta) -- and is then moved out by half the land so the
    # two flanks meet the land's corners instead of each other.
    #
    # This replaced a construction that rotated finite boxes and translated
    # them by +/- t/2. That left the striking edge at y = -t/2 rather than on
    # the axis the tooth is aimed by, so it landed half a head-thickness from
    # the strike point, and cut away half the head: measured 0.505 of the
    # modelled volume, which the backing had already been sized against. On a
    # target narrower than the offset it missed entirely -- three corpus cases
    # made no contact with a strike point 0.0001 mm from the meshed surface.
    head_tags = head_solids(occ, w, t, length, land)

    back_tags: list[int] = []
    if geom.back_section > 0:
        s = geom.back_section
        back = occ.addBox(-s / 2.0, -s / 2.0, length, s, s, s)
        back_tags.append(back)

    everything = [(3, tag) for tag in (*head_tags, *back_tags)]

    # ``approach.direction`` points *outward* from the target, towards where
    # the opponent is: the tooth travels along -direction and its chisel edge
    # has to lead. As built the edge is at z = 0 and the body runs to
    # z = +length, so **+Z** is what maps onto the approach — that puts the
    # body behind the edge, away from the target. Aiming -Z instead buries the
    # body in the part being struck, which is what this did until 2026-08-14:
    # measured on inertial-v6, 45.7% of the head volume started inside the
    # armour panel, tip 0.08 mm *below* a surface it was supposed to be
    # 0.08 mm above.
    dx, dy, dz = (float(c) for c in approach.direction)
    target = (dx, dy, dz)
    if width_axis is None:
        _aim(occ, everything, (0.0, 0.0, 1.0), target)
    else:
        # A spinning weapon's tooth is as wide as its bar along the spin
        # axis: map local x (width) onto that axis and local +z onto the
        # approach direction, in one rigid transform.
        _orient(occ, everything, target, width_axis)

    # Stand the edge one standoff clear of the surface, on the outside.
    sx, sy, sz = approach.strike_point
    offset = approach.standoff
    occ.translate(
        everything,
        sx + target[0] * offset,
        sy + target[1] * offset,
        sz + target[2] * offset,
    )
    occ.synchronize()
    return {"head": head_tags, "back": back_tags}


def _orient(occ: Any, dim_tags: Any, z_to: Any, x_toward: Any) -> None:
    """Rotate so local +Z lands on `z_to` and local +X on `x_toward`
    (projected perpendicular to `z_to`)."""
    import numpy as np

    ez = np.asarray(z_to, dtype=float)
    ez /= np.linalg.norm(ez)
    ex = np.asarray(x_toward, dtype=float)
    ex = ex - ex.dot(ez) * ez
    if np.linalg.norm(ex) < 1e-9:
        _aim(occ, dim_tags, (0.0, 0.0, 1.0), tuple(ez))
        return
    ex /= np.linalg.norm(ex)
    ey = np.cross(ez, ex)
    R = np.column_stack([ex, ey, ez])  # local -> global
    occ.affineTransform(dim_tags, [*R[0], 0.0, *R[1], 0.0, *R[2], 0.0])


def _aim(
    occ: Any,
    dim_tags: Sequence[tuple[int, int]],
    source: tuple[float, float, float],
    target: tuple[float, float, float],
) -> None:
    """Rotate ``dim_tags`` so ``source`` points along ``target``."""
    dot = sum(a * b for a, b in zip(source, target, strict=True))
    dot = max(-1.0, min(1.0, dot))
    if dot > 1.0 - 1e-12:
        return
    axis = (
        source[1] * target[2] - source[2] * target[1],
        source[2] * target[0] - source[0] * target[2],
        source[0] * target[1] - source[1] * target[0],
    )
    norm = math.sqrt(sum(c * c for c in axis))
    if norm < 1e-12:
        # Exactly antiparallel: any perpendicular axis gives the same result.
        axis = (1.0, 0.0, 0.0) if abs(source[0]) < 0.9 else (0.0, 1.0, 0.0)
        norm = 1.0
    occ.rotate(
        list(dim_tags),
        0.0,
        0.0,
        0.0,
        axis[0] / norm,
        axis[1] / norm,
        axis[2] / norm,
        math.acos(dot),
    )


def name_group(gmsh: Any, tags: Sequence[int], name: str) -> int:
    """One physical group over ``tags``, labelled ``name``."""
    group = int(gmsh.model.addPhysicalGroup(3, list(tags)))
    gmsh.model.setPhysicalName(3, group, name)
    return group


def generate_mesh(
    gmsh: Any,
    out_path: str | Path,
    *,
    size: float,
    min_size: float = 0.0,
    solid_map: Any = None,
    algo3d: int = ALGO3D_FRONTAL,
    optimize_threshold: float | None = None,
    netgen: bool = True,
) -> Path:
    """Mesh the grouped volumes and write a ``.msh``.

    ``min_size`` defaults to a fifth of ``size``, matching ``mesh_step.py``.

    ``solid_map`` is optional and only used to name the offending part when
    meshing fails; without it the failure still reports, just less usefully.

    ``algo3d`` and ``optimize_threshold`` exist for ``cra.calibrate.mesh_algo``
    and default to what production runs, so leaving them alone is exactly
    today's behaviour. They are here rather than in a separate copy of this
    function because a calibration run that meshes differently from the
    product measures nothing the product will experience -- the same reason
    ``scoped.py`` gates on ``Settings.per_case_budget`` instead of its own
    threshold. ``optimize_threshold`` of ``None`` leaves gmsh's default of
    0.3, which is what every measurement in this file was taken at.
    """
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)

    gmsh.option.setNumber("Mesh.Algorithm3D", algo3d)
    gmsh.option.setNumber("Mesh.MeshSizeMax", size)
    gmsh.option.setNumber("Mesh.MeshSizeMin", min_size or size / 5.0)

    # Frontal builds slivers, and one sliver taxes the whole solve: the
    # timestep is set by the smallest element in the *model*, so a handful of
    # bad tets multiply every part's cycle count. Measured on inertial-v6's
    # weapon bar, meshed with the tooth present: without optimisation, 938,423
    # elements with a 0.001833 mm edge and 61 tets under 0.05 mm; with it,
    # 826,689 elements and a 0.198582 mm floor — the shortest edge the
    # geometry actually has, and 0 slivers. That is 1.16e11 element-cycles
    # against 9.2e8, a 125x difference, from an option.
    #
    # Frontal is kept because Phase 5 found Delaunay throws PLC errors on
    # multi-body STEP assemblies. Delaunay also produces a clean mesh here
    # (814,082 elements, same 0.198582 floor) and is faster, so it is worth
    # revisiting if optimisation ever proves too slow — it costs roughly 2x
    # the meshing time.
    gmsh.option.setNumber("Mesh.Optimize", 1)
    # Netgen's optimiser segfaulted gmsh on a graded mesh of inertial-v6
    # (2026-10-08, twice); without it the graded mesh is valid and its stable
    # timestep held (3.5e-8 s, 124,753 elements). Callers grading the mesh
    # turn it off.
    gmsh.option.setNumber("Mesh.OptimizeNetgen", 1 if netgen else 0)
    if optimize_threshold is not None:
        gmsh.option.setNumber("Mesh.OptimizeThreshold", optimize_threshold)
    try:
        gmsh.model.mesh.generate(3)
    except Exception as exc:
        # gmsh raises a bare Exception whose text names a gmsh entity tag and
        # nothing else. On its own that reaches the user as "Impossible to
        # mesh periodic surface 6056", which names nothing they can find in
        # their CAD. Resolve the tag to the part that owns it.
        raise GeometryError(_mesh_failure_message(gmsh, exc, solid_map)) from exc

    # A mesh with no volume elements is a failure that did not raise: gmsh
    # returns, gmsh.write succeeds, and the file exists. Measured on a 617 mm3
    # bearing that "meshed" to zero tetrahedra.
    if not _volume_element_count(gmsh):
        raise GeometryError(
            "the mesher produced no volume elements, so there is nothing to "
            "solve. The geometry imported and meshed without raising, which "
            "usually means a solid that is not watertight."
        )

    gmsh.write(str(out))

    if not out.is_file():
        raise GeometryError(f"gmsh produced no mesh at {out}")
    return out


def _volume_element_count(gmsh: Any) -> int:
    """Tetrahedra in the current model. Zero means nothing to solve."""
    try:
        # 4 is gmsh's element type for a 4-node tetrahedron.
        tags = gmsh.model.mesh.getElementsByType(4)[0]
    except Exception:
        return 0
    return len(tags)


def _mesh_failure_message(gmsh: Any, exc: Exception, solid_map: Any = None) -> str:
    """Turn a gmsh meshing exception into something the submitter can act on."""
    raw = str(exc).strip().splitlines()[-1] if str(exc).strip() else exc.__class__.__name__

    match = re.search(r"surface (\d+)", raw)
    if match is None:
        return f"the mesher failed: {raw}"

    tag = int(match.group(1))
    owner = _surface_owner(gmsh, tag, solid_map)
    if owner is None:
        return f"the mesher failed on surface {tag}: {raw}"

    part_id, extent = owner
    where = f" ({extent})" if extent else ""
    return (
        f"the mesher failed on a face of {part_id}{where}: {raw}. That face "
        f"cannot be meshed as it is exported; the part has to be repaired or "
        f"replaced before this load case can run."
    )


def _surface_owner(gmsh: Any, tag: int, solid_map: Any) -> tuple[str, str] | None:
    """``(part_id, extent)`` for the part owning a surface tag, if resolvable."""
    try:
        volumes = gmsh.model.getAdjacencies(2, tag)[0]
    except Exception:
        return None
    part_by_tag = getattr(solid_map, "part_by_tag", None) or {}
    for volume in volumes:
        part_id = part_by_tag.get(int(volume))
        if not part_id:
            continue
        try:
            lo = gmsh.model.getBoundingBox(3, int(volume))
            extent = " x ".join(f"{hi - low:.1f}" for low, hi in zip(lo[:3], lo[3:], strict=True))
            return part_id, f"{extent} mm"
        except Exception:
            return part_id, ""
    return None


# --- verification CLI -----------------------------------------------------


def verify(step_path: str | Path) -> dict[str, Any]:
    """Prove the ordinal mapping holds for one STEP file.

    Runs the real cad-step report and the real gmsh import, so it exercises
    exactly the path ``build_case`` takes. Needs pythonocc (sim Python).
    """
    from case.cadstep import run_report

    report = run_report(
        step_path,
        skip=("clash", "drawing", "features", "fasteners", "placements"),
    )
    parts = report.get("parts") or []

    with gmsh_session() as gmsh:
        solids = import_solids(gmsh, step_path)
        solid_map = map_solids(solids, parts)

    return {
        "step": str(step_path),
        "solids": solid_map.solid_count,
        "parts": solid_map.part_count,
        "worst_residual": solid_map.worst_residual,
        "unverified": list(solid_map.unverified),
        "ids_unique": len(set(solid_map.tags_by_part)) == solid_map.part_count,
    }


def main(argv: Sequence[str] | None = None) -> int:
    import argparse
    import json
    import sys

    p = argparse.ArgumentParser(
        prog="cra.simgeom",
        description="Verify that gmsh solids map onto Tier 0 part ids.",
    )
    p.add_argument("--verify", metavar="STEP", required=True, help="STEP file to check")
    args = p.parse_args(argv)

    try:
        result = verify(args.verify)
    except GeometryError as exc:
        print(f"FAIL {args.verify}: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2))
    print(
        f"PASS {result['solids']} solids -> {result['parts']} parts, "
        f"worst residual {result['worst_residual']:.2e}",
        file=sys.stderr,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
