"""The guarded mmg3d post-pass: propose a repaired mesh, accept it only if
it is measurably cheaper.

`docs/history/mesh-sliver-anatomy.md` §7 measured `mmg3d -optim -nosurf
-hmin` over 35 production meshes: it kills the slab-sliver class outright
(median x0.17 element-cycles, one case x0.036) and makes most other cases
*worse* (median x1.17), because mmg optimizes shape, not size. The lever
only survives as propose-measure-accept — the same shape as the withdrawn
geometry simplifier's acceptance rule, and for the same reason: a repair
not measured against the original is indistinguishable from damage.

The proposal never touches part surfaces (`-nosurf`; measured worst volume
drift 2.3e-8 over the corpus), so contact geometry is bitwise what gmsh
built. The accepted mesh is written as a *sibling* file, MSH 2.2 with the
physical names restored — the format `build_deck.load_parts` resolves via
meshio's `field_data` — and the gmsh original stays on disk for diagnosis.

Split pure-from-subprocess like `simplify.py`: `evaluate_pass` and the two
format codecs are pure; `guarded_pass` is the only function that runs mmg.
"""

from __future__ import annotations

import math
import subprocess
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from case.mesh_metrics import MeshMetrics, analyse, tet_geometry

__all__ = [
    "ACCEPT_MARGIN",
    "MAX_PART_VOLUME_DRIFT",
    "MMG_BIN",
    "PassResult",
    "evaluate_pass",
    "guarded_pass",
    "read_medit",
    "write_medit",
    "write_msh22",
]

#: A proposal must be at least this much cheaper to replace the mesh. The
#: guard exists to stop churn: replacing a mesh for a fraction of a percent
#: swaps the artefact every downstream stage was built against for noise.
ACCEPT_MARGIN = 0.98

#: Per-part volume drift that rejects a proposal. `-nosurf` was measured at
#: 2.3e-8 worst over 35 meshes, so this is a tripwire for "mmg did something
#: it promised not to", not a tolerance the pass is expected to use.
MAX_PART_VOLUME_DRIFT = 1e-3

MMG_BIN = "mmg3d_O3"
TIMEOUT_S = 300.0

Groups = Mapping[str, Sequence[Sequence[tuple[float, float, float]]]]


@dataclass(frozen=True)
class PassResult:
    """What the pass did to one case, JSON-safe via :meth:`record`."""

    accepted: bool
    #: Why it was rejected, or ``"accepted"``. Never empty.
    reason: str
    seconds: float
    ec_before: float
    ec_after: float | None = None
    min_before_mm: float | None = None
    min_after_mm: float | None = None
    #: The replacement mesh and its metrics, populated only on acceptance.
    mesh_path: Path | None = None
    metrics: MeshMetrics | None = None

    def record(self) -> dict[str, Any]:
        return {
            "accepted": self.accepted,
            "reason": self.reason,
            "seconds": round(self.seconds, 1),
            "ec_before": self.ec_before,
            "ec_after": self.ec_after,
            "min_before_mm": self.min_before_mm,
            "min_after_mm": self.min_after_mm,
        }


def _shared_nodes(
    groups: Groups,
) -> tuple[list[tuple[float, float, float]], dict[str, list[tuple[int, ...]]]]:
    """Rebuild shared node identity from coordinates.

    ``loadcase`` hands the mesh over as bare coordinates per tet; conformality
    is still present as *exact* float equality, because every copy of a node
    came from the same gmsh node. Keying on the tuple restores the shared
    topology mmg needs — hashing floats is only safe because nothing here
    ever recomputes a coordinate.
    """
    index: dict[tuple[float, float, float], int] = {}
    nodes: list[tuple[float, float, float]] = []
    tets: dict[str, list[tuple[int, ...]]] = {}
    for name, part in groups.items():
        rows = []
        for tet in part:
            ids = []
            for p in tet:
                key = (p[0], p[1], p[2])
                i = index.get(key)
                if i is None:
                    i = len(nodes)
                    index[key] = i
                    nodes.append(key)
                ids.append(i + 1)
            rows.append(tuple(ids))
        tets[name] = rows
    return nodes, tets


def write_medit(groups: Groups, path: Path) -> dict[int, str]:
    """Write Medit ASCII with one reference per part; returns ref -> name."""
    nodes, tets = _shared_nodes(groups)
    names = sorted(tets)
    with open(path, "w") as f:
        f.write("MeshVersionFormatted 2\nDimension 3\n")
        f.write(f"Vertices\n{len(nodes)}\n")
        for x, y, z in nodes:
            f.write(f"{x:.17g} {y:.17g} {z:.17g} 0\n")
        f.write(f"Tetrahedra\n{sum(len(t) for t in tets.values())}\n")
        for ref, name in enumerate(names, start=1):
            for a, b, c, d in tets[name]:
                f.write(f"{a} {b} {c} {d} {ref}\n")
        f.write("End\n")
    return {ref: name for ref, name in enumerate(names, start=1)}


def read_medit(
    path: Path, names_by_ref: Mapping[int, str]
) -> dict[str, list[list[tuple[float, float, float]]]]:
    """Read mmg's output back into the ``groups`` shape ``analyse`` takes."""
    toks = Path(path).read_text().split()
    i, n = 0, len(toks)
    verts: list[tuple[float, float, float]] = []
    groups: dict[str, list[list[tuple[float, float, float]]]] = {}
    while i < n:
        t = toks[i]
        if t == "Vertices":
            count = int(toks[i + 1])
            i += 2
            for _ in range(count):
                verts.append((float(toks[i]), float(toks[i + 1]), float(toks[i + 2])))
                i += 4
        elif t == "Tetrahedra":
            count = int(toks[i + 1])
            i += 2
            for _ in range(count):
                a, b, c, d = (int(toks[i + k]) for k in range(4))
                ref = int(toks[i + 4])
                name = names_by_ref.get(ref)
                if name is None:
                    raise ValueError(f"mmg output carries unknown reference {ref}")
                groups.setdefault(name, []).append(
                    [verts[a - 1], verts[b - 1], verts[c - 1], verts[d - 1]]
                )
                i += 5
        elif t == "Triangles":
            i += 2 + int(toks[i + 1]) * 4
        elif t == "Edges":
            i += 2 + int(toks[i + 1]) * 3
        elif t in ("Corners", "RequiredVertices", "Ridges", "RequiredEdges", "RequiredTriangles"):
            i += 2 + int(toks[i + 1])
        elif t == "End":
            break
        else:
            i += 1
    return groups


def write_msh22(groups: Groups, path: Path) -> None:
    """MSH 2.2 ASCII with ``$PhysicalNames`` — what ``build_deck`` resolves."""
    nodes, tets = _shared_nodes(groups)
    names = sorted(tets)
    with open(path, "w") as f:
        f.write("$MeshFormat\n2.2 0 8\n$EndMeshFormat\n")
        f.write(f"$PhysicalNames\n{len(names)}\n")
        for ref, name in enumerate(names, start=1):
            f.write(f'3 {ref} "{name}"\n')
        f.write("$EndPhysicalNames\n")
        f.write(f"$Nodes\n{len(nodes)}\n")
        for i, (x, y, z) in enumerate(nodes, start=1):
            f.write(f"{i} {x:.17g} {y:.17g} {z:.17g}\n")
        f.write("$EndNodes\n")
        f.write(f"$Elements\n{sum(len(t) for t in tets.values())}\n")
        eid = 1
        for ref, name in enumerate(names, start=1):
            for a, b, c, d in tets[name]:
                f.write(f"{eid} 4 2 {ref} {ref} {a} {b} {c} {d}\n")
                eid += 1
        f.write("$EndElements\n")


def _part_volumes(groups: Groups) -> dict[str, float]:
    return {
        name: sum(max(tet_geometry(tet)[1], 0.0) for tet in part) for name, part in groups.items()
    }


def evaluate_pass(
    before: MeshMetrics,
    after: MeshMetrics,
    volumes_before: Mapping[str, float],
    volumes_after: Mapping[str, float],
) -> str:
    """``"accepted"`` or the reason the proposal must be rejected."""
    if set(volumes_before) != set(volumes_after):
        return "the proposal changed the part set"
    for name, vol in volumes_before.items():
        if vol <= 0:
            return f"{name} had no volume to compare"
        drift = abs(volumes_after[name] - vol) / vol
        if drift > MAX_PART_VOLUME_DRIFT:
            return f"{name} drifted {drift:.2e} in volume, past {MAX_PART_VOLUME_DRIFT:.0e}"
    if not math.isfinite(after.element_cycles) or after.element_cycles <= 0:
        return "the proposal priced to nothing measurable"
    if after.element_cycles > ACCEPT_MARGIN * before.element_cycles:
        return (
            f"not cheaper: {after.element_cycles:.3g} against "
            f"{before.element_cycles:.3g} element-cycles"
        )
    return "accepted"


def guarded_pass(
    groups: Groups,
    cards: Mapping[str, Mapping[str, Any]],
    *,
    baseline: MeshMetrics,
    end_time: float,
    timestep_scale: float,
    mesh_floor_mm: float,
    workdir: str | Path,
    case: str,
    mmg_bin: str = MMG_BIN,
    timeout_s: float = TIMEOUT_S,
) -> PassResult:
    """Propose a repaired mesh with mmg3d; accept it only if cheaper.

    Every failure mode — mmg missing, crashing, timing out, emitting an
    unreadable or unmeasurable mesh — rejects the proposal and keeps the
    baseline; the pass can make a build slower but never worse.
    """
    t0 = time.monotonic()
    work = Path(workdir)
    min_before = min((p.min_char_mm for p in baseline.per_part), default=math.inf)

    def rejected(reason: str, after: MeshMetrics | None = None) -> PassResult:
        return PassResult(
            accepted=False,
            reason=reason,
            seconds=time.monotonic() - t0,
            ec_before=baseline.element_cycles,
            ec_after=after.element_cycles if after else None,
            min_before_mm=min_before,
            min_after_mm=min((p.min_char_mm for p in after.per_part), default=None)
            if after
            else None,
        )

    medit_in = work / f"{case}-mmg-in.mesh"
    medit_out = work / f"{case}-mmg-out.mesh"
    names_by_ref = write_medit(groups, medit_in)
    try:
        proc = subprocess.run(
            [
                mmg_bin,
                "-in",
                str(medit_in),
                "-out",
                str(medit_out),
                "-optim",
                "-nosurf",
                "-hmin",
                f"{mesh_floor_mm:g}",
                "-v",
                "0",
            ],
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except FileNotFoundError:
        return rejected(f"{mmg_bin} is not on PATH")
    except subprocess.TimeoutExpired:
        return rejected(f"mmg exceeded {timeout_s:.0f}s")
    if proc.returncode != 0 or not medit_out.is_file():
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-1:] or ["no output"]
        return rejected(f"mmg exited {proc.returncode}: {tail[0][:160]}")

    try:
        proposed = read_medit(medit_out, names_by_ref)
    except (ValueError, IndexError) as exc:
        return rejected(f"mmg output unreadable: {exc}")

    metrics = analyse(
        proposed,
        cards,
        end_time=end_time,
        timestep_min=0.0,
        timestep_scale=timestep_scale,
        mesh_size_min=mesh_floor_mm,
    )
    verdict = evaluate_pass(baseline, metrics, _part_volumes(groups), _part_volumes(proposed))
    if verdict != "accepted":
        return rejected(verdict, metrics)

    replacement = work / f"{case}-mmg.msh"
    write_msh22(proposed, replacement)
    return PassResult(
        accepted=True,
        reason="accepted",
        seconds=time.monotonic() - t0,
        ec_before=baseline.element_cycles,
        ec_after=metrics.element_cycles,
        min_before_mm=min_before,
        min_after_mm=min((p.min_char_mm for p in metrics.per_part), default=None),
        mesh_path=replacement,
        metrics=metrics,
    )
