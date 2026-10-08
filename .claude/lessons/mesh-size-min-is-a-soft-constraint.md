# `Mesh.MeshSizeMin` is a soft clamp on the target size field, not a floor on elements

gmsh's size options constrain the *prescribed* size the meshers aim at, never
the elements they produce — confirmed by Geuzaine in gmsh issues
[#2710](https://gitlab.onelab.info/gmsh/gmsh/-/issues/2710) ("these are indeed
soft constraints") and [#1492](https://gitlab.onelab.info/gmsh/gmsh/-/issues/1492)
(±2× spread on a unit box "seem OK"). Worse, under `Algorithm3D = 4` the
option is not even soft: gmsh hands Netgen only `maxh`
(`meshGRegionNetgen.cpp`), so Frontal never sees a minimum at all, and volume
size fields are documented no-ops outside Delaunay/HXT. Only HXT enforces a
real spacing floor, and only for newly inserted interior points.

**How to act next time:** never treat a gmsh size option as a guarantee about
the mesh — measure the realized distribution (`mesh_metrics.analyse` does)
and enforce floors with tools that operate on elements (mmg3d `-hmin`, or a
gate). Full anatomy and evidence: `docs/history/mesh-sliver-anatomy.md`.
