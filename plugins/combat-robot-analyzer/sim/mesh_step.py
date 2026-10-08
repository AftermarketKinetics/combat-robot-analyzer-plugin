#!/usr/bin/env python3
"""Mesh a STEP file with gmsh.

Reads CAD geometry (STEP/IGES/BREP) and writes a gmsh .msh file plus a JSON
summary describing what was produced. This is stage 1 of the pipeline; stage 2
(build_deck.py) turns the mesh into a solver deck.

Every solid in the STEP becomes its own physical group, so parts stay separable
downstream for per-part materials and contacts.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

import gmsh

# gmsh 3D meshing algorithms. HXT is much faster on large models but falls back
# to Delaunay on geometry it cannot handle.
ALGO3D = {"delaunay": 1, "frontal": 4, "hxt": 10}
ALGO2D = {"meshadapt": 1, "auto": 2, "delaunay": 5, "frontal": 6}
# Blossom handles the periodic faces of holes and fillets; the "full-quad"
# variants (2, 3) refuse to recombine them at all.
RECOMBINE = {"simple": 0, "blossom": 1, "simple-full": 2, "blossom-full": 3}


def parse_args(argv=None):
    p = argparse.ArgumentParser(
        prog="mesh_step.py",
        description="Mesh a STEP/IGES/BREP file into a gmsh .msh",
    )
    p.add_argument("step", help="input CAD file (.step/.stp/.iges/.brep)")
    p.add_argument("-o", "--out", help="output mesh (default: <step>.msh)")
    p.add_argument(
        "-s", "--size", type=float, required=True,
        help="target element edge length, in the CAD file's units (usually mm)",
    )
    p.add_argument(
        "--min-size", type=float,
        help="smallest allowed element (default: size/5)",
    )
    p.add_argument(
        "-e", "--element", choices=["tri", "quad", "tet"], default="tet",
        help="tri/quad = surface (shell) mesh, tet = solid mesh (default: tet)",
    )
    p.add_argument(
        "--order", type=int, choices=[1, 2], default=1,
        help="element order; 2 = midside nodes (default: 1)",
    )
    p.add_argument(
        "--curvature", type=int, default=0,
        help="elements per 2*pi of curvature; refines holes and fillets "
             "automatically (0 = off, 12 is a sane starting point)",
    )
    p.add_argument(
        "--scale", type=float, default=1.0,
        help="multiply all coordinates by this factor on import "
             "(e.g. 1000 to turn metres into mm)",
    )
    p.add_argument("--heal", action="store_true", help="run OCC shape healing on import")
    p.add_argument(
        "--fragment", action="store_true",
        help="run OCC BooleanFragments on all solids so touching volumes share "
             "conformal mesh interfaces (required for multi-solid STEPs where "
             "parts should be glued)",
    )
    p.add_argument(
        "--grade", action="store_true",
        help="propagate boundary refinement into the interior for smoother "
             "size transitions; costs roughly 1.7x the elements",
    )
    p.add_argument("--optimize", action="store_true", help="run mesh optimisation after generation")
    p.add_argument("--algo3d", choices=sorted(ALGO3D), default="delaunay")
    p.add_argument("--algo2d", choices=sorted(ALGO2D), default="frontal")
    p.add_argument(
        "--recombine-algo", choices=sorted(RECOMBINE), default="blossom",
        help="quad recombination strategy for --element quad (default: blossom)",
    )
    p.add_argument(
        "--faces", metavar="TAGS",
        help="comma-separated surface tags to mesh (shell meshes only); "
             "use --list-faces to discover them",
    )
    p.add_argument(
        "--list-faces", action="store_true",
        help="print each surface with its area, centroid and normal, then exit",
    )
    p.add_argument(
        "--group-per-face", action="store_true",
        help="emit one part per CAD face instead of one part per solid",
    )
    p.add_argument("--json", dest="json_out", help="write the summary to this path as well")
    p.add_argument("-v", "--verbose", action="store_true", help="let gmsh print its own log")
    return p.parse_args(argv)


def bounding_box():
    xmin, ymin, zmin, xmax, ymax, zmax = gmsh.model.getBoundingBox(-1, -1)
    return {
        "min": [xmin, ymin, zmin],
        "max": [xmax, ymax, zmax],
        "size": [xmax - xmin, ymax - ymin, zmax - zmin],
    }


def describe_faces():
    """Area, centroid and outward normal for every surface in the model."""
    out = []
    for _, tag in gmsh.model.getEntities(2):
        com = gmsh.model.occ.getCenterOfMass(2, tag)
        area = gmsh.model.occ.getMass(2, tag)
        try:
            # Normal at the middle of the parametric domain is representative
            # for planar faces and indicative for the rest.
            lo, hi = gmsh.model.getParametrizationBounds(2, tag)
            mid = [(a + b) / 2.0 for a, b in zip(lo, hi)]
            normal = list(gmsh.model.getNormal(tag, mid))
        except Exception:
            normal = None
        out.append({
            "tag": tag,
            "area": area,
            "centroid": [round(c, 6) for c in com],
            "normal": [round(n, 6) for n in normal] if normal else None,
            "type": gmsh.model.getType(2, tag),
        })
    return out


def _named(dim, tag, fallback):
    name = gmsh.model.getEntityName(dim, tag) or ""
    # STEP product names arrive as slash-delimited paths; keep the leaf, and
    # drop the generic label OCC stamps on unnamed shapes.
    label = name.split("/")[-1].strip()
    if not label or label.startswith("Open CASCADE STEP translator"):
        label = fallback
    return label


def tag_parts(dim, faces=None, per_face=False):
    """Create physical groups, which is what gets written to the .msh.

    Solids become one part each. Surfaces are grouped by the solid they bound,
    so a shell mesh of a two-part assembly yields two parts rather than one per
    CAD face.
    """
    groups = []

    def add(d, tags, label):
        pg = gmsh.model.addPhysicalGroup(d, tags)
        gmsh.model.setPhysicalName(d, pg, label)
        groups.append({"id": pg, "name": label, "entities": list(tags)})

    if dim == 3:
        for _, tag in gmsh.model.getEntities(3):
            add(3, [tag], _named(3, tag, f"volume_{tag}"))
        return groups

    selected = set(faces) if faces else {t for _, t in gmsh.model.getEntities(2)}
    if per_face:
        for tag in sorted(selected):
            add(2, [tag], _named(2, tag, f"surface_{tag}"))
        return groups

    solids = gmsh.model.getEntities(3)
    if solids:
        for _, stag in solids:
            bnd = {abs(t) for _, t in gmsh.model.getBoundary([(3, stag)], oriented=False)}
            tags = sorted(bnd & selected)
            if tags:
                add(2, tags, _named(3, stag, f"volume_{stag}"))
    else:
        add(2, sorted(selected), "shell")
    return groups


def main(argv=None):
    args = parse_args(argv)

    if not os.path.isfile(args.step):
        sys.exit(f"mesh_step: no such file: {args.step}")

    out = args.out or os.path.splitext(args.step)[0] + ".msh"
    parent = os.path.dirname(os.path.abspath(out))
    os.makedirs(parent, exist_ok=True)
    dim = 3 if args.element == "tet" else 2
    min_size = args.min_size if args.min_size is not None else args.size / 5.0

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 1 if args.verbose else 0)

        if args.scale != 1.0:
            gmsh.option.setNumber("Geometry.OCCScaling", args.scale)
        if args.heal:
            gmsh.option.setNumber("Geometry.OCCFixDegenerated", 1)
            gmsh.option.setNumber("Geometry.OCCFixSmallEdges", 1)
            gmsh.option.setNumber("Geometry.OCCFixSmallFaces", 1)
            gmsh.option.setNumber("Geometry.OCCSewFaces", 1)

        gmsh.model.add("cad")
        gmsh.model.occ.importShapes(args.step)

        if args.fragment:
            vols = gmsh.model.occ.getEntities(3)
            if len(vols) > 1:
                # BooleanFragments creates conformal interfaces between all
                # solids: shared faces get matching nodes so the parts are
                # effectively glued in the mesh.  Without this, each solid
                # meshes independently and stacked plates fly apart.
                gmsh.model.occ.fragment([vols[0]], vols[1:])

        gmsh.model.occ.synchronize()

        if args.list_faces:
            print(json.dumps({"faces": describe_faces()}, indent=2))
            return 0

        solids = gmsh.model.getEntities(3)
        faces = gmsh.model.getEntities(2)
        selected = None
        if args.faces:
            selected = [int(t) for t in args.faces.replace(",", " ").split()]
            known = {t for _, t in faces}
            missing = sorted(set(selected) - known)
            if missing:
                sys.exit(f"mesh_step: no such surface tag(s): {missing}")
            if dim == 3:
                sys.exit("mesh_step: --faces only applies to shell meshes (--element tri/quad)")
        if dim == 3 and not solids:
            sys.exit(
                "mesh_step: --element tet needs a solid, but the STEP contains "
                f"only {len(faces)} surface(s). Re-export as a solid, or mesh "
                "with --element tri/quad."
            )

        gmsh.option.setNumber("Mesh.MeshSizeMax", args.size)
        gmsh.option.setNumber("Mesh.MeshSizeMin", min_size)
        gmsh.option.setNumber("Mesh.Algorithm", ALGO2D[args.algo2d])
        gmsh.option.setNumber("Mesh.Algorithm3D", ALGO3D[args.algo3d])
        if args.curvature:
            gmsh.option.setNumber("Mesh.MeshSizeFromCurvature", args.curvature)
        # Geometry point spacing otherwise silently overrides the sizes above.
        gmsh.option.setNumber("Mesh.MeshSizeFromPoints", 0)
        # Off by default: when on, fine curve discretisation around small holes
        # bleeds inward and the mesh comes out well below the requested --size.
        gmsh.option.setNumber("Mesh.MeshSizeExtendFromBoundary", 1 if args.grade else 0)

        if args.element == "quad":
            gmsh.option.setNumber("Mesh.RecombineAll", 1)
            gmsh.option.setNumber("Mesh.RecombinationAlgorithm", RECOMBINE[args.recombine_algo])

        # Only physical groups reach the .msh, so tagging a subset of faces is
        # what makes --faces actually restrict the output.
        gmsh.option.setNumber("Mesh.SaveAll", 0)
        groups = tag_parts(dim, faces=selected, per_face=args.group_per_face)
        gmsh.model.mesh.generate(dim)
        if args.order == 2:
            gmsh.model.mesh.setOrder(2)
        if args.optimize:
            gmsh.model.mesh.optimize("Netgen" if dim == 3 else "Laplace2D")

        # Only the physical groups reach the file, so the summary must count
        # those and not every element gmsh happened to generate.
        counts = {}
        written_nodes = set()
        for group in groups:
            total = 0
            for ent in group["entities"]:
                etypes, etags, enodes = gmsh.model.mesh.getElements(dim, ent)
                for etype, tags, nodes in zip(etypes, etags, enodes):
                    name = gmsh.model.mesh.getElementProperties(etype)[0]
                    counts[name] = counts.get(name, 0) + len(tags)
                    total += len(tags)
                    written_nodes.update(nodes)
            group["elements"] = total
        node_tags = written_nodes

        bbox = bounding_box()
        gmsh.option.setNumber("Mesh.MshFileVersion", 4.1)
        gmsh.write(out)
    finally:
        gmsh.finalize()

    summary = {
        "source": os.path.abspath(args.step),
        "mesh": os.path.abspath(out),
        "dimension": dim,
        "element": args.element,
        "order": args.order,
        "target_size": args.size,
        "min_size": min_size,
        "nodes": len(node_tags),
        "elements": counts,
        "element_total": sum(counts.values()),
        "parts": groups,
        "bounding_box": bbox,
    }

    if not counts:
        sys.exit("mesh_step: meshing produced no elements — check the geometry and --size")

    text = json.dumps(summary, indent=2)
    if args.json_out:
        with open(args.json_out, "w") as fh:
            fh.write(text + "\n")
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
